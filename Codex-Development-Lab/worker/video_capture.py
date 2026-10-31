"""Trusted X11 recording and capture quality checks, independent of game verdicts."""
from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time


# Xlib ABI: https://www.x.org/releases/current/doc/libX11/libX11/libX11.html
# XWindowAttributes includes pointers, unsigned longs and signed long event masks;
# representing these as ints corrupts the later map_state field on 64-bit Linux.
class XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ('x', ctypes.c_int), ('y', ctypes.c_int),
        ('width', ctypes.c_int), ('height', ctypes.c_int),
        ('border_width', ctypes.c_int), ('depth', ctypes.c_int),
        ('visual', ctypes.c_void_p), ('root', ctypes.c_ulong),
        ('window_class', ctypes.c_int), ('bit_gravity', ctypes.c_int),
        ('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int),
        ('backing_planes', ctypes.c_ulong), ('backing_pixel', ctypes.c_ulong),
        ('save_under', ctypes.c_int), ('colormap', ctypes.c_ulong),
        ('map_installed', ctypes.c_int), ('map_state', ctypes.c_int),
        ('all_event_masks', ctypes.c_long), ('your_event_mask', ctypes.c_long),
        ('do_not_propagate_mask', ctypes.c_long), ('override_redirect', ctypes.c_int),
        ('screen', ctypes.c_void_p),
    ]


_ERROR_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


@_ERROR_CALLBACK
def _ignore_protocol_error(_display, _event):
    # A window can disappear between XQueryTree and XGetWindowAttributes.
    # Xlib's default BadWindow handler exits the entire supervisor process.
    return 0


_XLIB_LOCK = threading.Lock()
_XLIB = None


def _xlib():
    global _XLIB
    if not sys.platform.startswith('linux'):
        raise RuntimeError('X11 capture monitoring requires the Linux runner')
    with _XLIB_LOCK:
        if _XLIB is not None:
            return _XLIB
        try:
            library = ctypes.CDLL('libX11.so.6')
        except OSError as exc:
            raise RuntimeError('X11 capture monitoring requires libX11.so.6') from exc
        library.XInitThreads.argtypes = []
        library.XInitThreads.restype = ctypes.c_int
        if not library.XInitThreads():
            raise RuntimeError('Xlib thread initialization failed')
        library.XSetErrorHandler.argtypes = [_ERROR_CALLBACK]
        library.XSetErrorHandler.restype = ctypes.c_void_p
        library.XSetErrorHandler(_ignore_protocol_error)
        library.XOpenDisplay.argtypes = [ctypes.c_char_p]
        library.XOpenDisplay.restype = ctypes.c_void_p
        library.XCloseDisplay.argtypes = [ctypes.c_void_p]
        library.XCloseDisplay.restype = ctypes.c_int
        library.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        library.XDefaultRootWindow.restype = ctypes.c_ulong
        library.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                      ctypes.POINTER(ctypes.c_ulong),
                                      ctypes.POINTER(ctypes.c_ulong),
                                      ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                                      ctypes.POINTER(ctypes.c_uint)]
        library.XQueryTree.restype = ctypes.c_int
        library.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                               ctypes.POINTER(XWindowAttributes)]
        library.XGetWindowAttributes.restype = ctypes.c_int
        library.XFree.argtypes = [ctypes.c_void_p]
        library.XFree.restype = ctypes.c_int
        _XLIB = library
        return library


class X11Display:
    """Observe a private local display without creating windows or changing access."""

    def __init__(self, display):
        if not isinstance(display, str) or not re.fullmatch(r':[0-9]{1,3}', display):
            raise ValueError('Capture monitor accepts only a local X11 display')
        self.library = _xlib()
        self.handle = self.library.XOpenDisplay(display.encode('ascii'))
        if not self.handle:
            raise RuntimeError('Cannot open private X11 capture display ' + display)
        self.root = self.library.XDefaultRootWindow(self.handle)

    def close(self):
        if self.handle:
            self.library.XCloseDisplay(self.handle)
            self.handle = None

    def visible_window(self):
        root_attributes = XWindowAttributes()
        if not self.library.XGetWindowAttributes(self.handle, self.root,
                                                ctypes.byref(root_attributes)):
            raise RuntimeError('Cannot query private X11 root window')
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        try:
            if not self.library.XQueryTree(self.handle, self.root, ctypes.byref(root),
                                          ctypes.byref(parent), ctypes.byref(children),
                                          ctypes.byref(count)):
                raise RuntimeError('Cannot query private X11 window tree')
            # Newest/topmost children first. Bound work even if project code opens
            # thousands of windows. A disappearing child is skipped, never fatal.
            for index in range(count.value - 1, max(-1, count.value - 257), -1):
                attributes = XWindowAttributes()
                if not self.library.XGetWindowAttributes(self.handle, children[index],
                                                        ctypes.byref(attributes)):
                    continue
                if (attributes.map_state == 2 and attributes.window_class == 1
                        and attributes.width >= 200 and attributes.height >= 120
                        and attributes.x < root_attributes.width
                        and attributes.y < root_attributes.height
                        and attributes.x + attributes.width > 0
                        and attributes.y + attributes.height > 0):
                    return {'window_width': attributes.width,
                            'window_height': attributes.height,
                            'screen_width': root_attributes.width,
                            'screen_height': root_attributes.height}
            return None
        finally:
            if children:
                self.library.XFree(ctypes.cast(children, ctypes.c_void_p))


def _failure(reason, detail, **metadata):
    return {'status': 'ERROR', 'reason': reason, 'detail': detail, **metadata}


def probe_capture(path):
    """Check real H.264 frames; existence/size alone is never capture evidence."""
    path = Path(path)
    metadata = {'bytes': 0, 'codec': None, 'width': None, 'height': None,
                'duration_seconds': None, 'black_fraction': None}
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return _failure('invalid_file', 'Capture is not a regular file', **metadata)
        metadata['bytes'] = info.st_size
        if not 0 < info.st_size <= 256 * 1024**2:
            return _failure('invalid_size', 'Capture file is empty or exceeds 256 MiB', **metadata)
        probe = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height,duration,avg_frame_rate:format=duration',
            '-of', 'json', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20, check=False)
        if probe.returncode:
            return _failure('probe_failed', 'ffprobe could not read capture', **metadata)
        document = json.loads(probe.stdout)
        stream = document['streams'][0]
        duration = float(document.get('format', {}).get('duration') or stream['duration'])
        width, height = int(stream['width']), int(stream['height'])
        metadata.update(codec=stream.get('codec_name'), width=width, height=height,
                        duration_seconds=duration)
        if (metadata['codec'] != 'h264' or not math.isfinite(duration)
                or not 0 < duration <= 60 or not 0 < width <= 4096 or not 0 < height <= 4096):
            return _failure('invalid_video', 'Capture must be H.264, 0–60 seconds, with valid dimensions',
                            **metadata)
        if duration < 0.5:
            return _failure('too_short', 'Capture is too short for the 0.5-second black-frame check',
                            **metadata)
        numerator, _, denominator = str(stream.get('avg_frame_rate', '10/1')).partition('/')
        fps = float(numerator) / float(denominator or '1')
        if not math.isfinite(fps) or not 0 < fps <= 1000:
            fps = 10.0
        metadata['frames_per_second'] = fps
        analysis = subprocess.run([
            'ffmpeg', '-hide_banner', '-nostdin', '-nostats', '-loglevel', 'info',
            '-i', str(path), '-map', '0:v:0', '-an',
            '-vf', 'blackdetect=d=0.5:pix_th=0.05:pic_th=0.98',
            '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=60, check=False)
        if analysis.returncode:
            return _failure('decode_failed', 'ffmpeg could not decode capture frames', **metadata)
        intervals = []
        for start, end in re.findall(r'black_start:([\d.eE+\-]+)\s+black_end:([\d.eE+\-]+)',
                                     analysis.stderr.decode('utf-8', errors='replace')):
            start, end = float(start), float(end)
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValueError('Non-finite black frame timestamps')
            # blackdetect reports the last frame's PTS for a black run reaching
            # EOF, whereas container duration includes that frame's duration.
            if end >= duration - max(0.15, 1.0 / fps):
                end = duration
            intervals.append((max(0, start), min(duration, end)))
        total, previous_end = 0.0, 0.0
        for start, end in sorted(intervals):
            total += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
        metadata['black_fraction'] = min(1.0, total / duration)
        if metadata['black_fraction'] >= 0.97:
            return {'status': 'FAIL', 'reason': 'blank',
                    'detail': 'At least 97% of the capture is black', **metadata}
        return {'status': 'PASS', 'reason': 'validated',
                'detail': 'H.264 capture decoded; black image occupies less than 97% of the duration', **metadata}
    except FileNotFoundError as exc:
        return _failure('missing_capture_or_tool', str(exc)[:300], **metadata)
    except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError,
            subprocess.SubprocessError) as exc:
        return _failure('validation_error', type(exc).__name__ + ': ' + str(exc)[:300], **metadata)


class ScenarioWindow:
    """Observe scenario boundaries in the supervisor's command log using its own clock."""
    def __init__(self, logfile, scenario_id):
        if not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*',scenario_id) or len(scenario_id)>64:
            raise ValueError('Invalid scenario recording identifier')
        self.logfile=Path(logfile); self.scenario_id=scenario_id
        self.begin=self.end=None; self.invalid=False; self.offset=0; self.pending=b''

    def update(self):
        try:
            with self.logfile.open('rb') as handle:
                if os.fstat(handle.fileno()).st_size<self.offset:
                    self.invalid=True; return
                handle.seek(self.offset); data=handle.read(65536); self.offset+=len(data)
        except FileNotFoundError:
            return
        chunks=(self.pending+data).split(b'\n'); self.pending=chunks.pop()[-4096:]
        for line in chunks:
            line=line[-4096:].decode('utf-8',errors='replace').rstrip('\r')
            if re.search(r'(?:^|\s)LAB_ACCEPTANCE_BEGIN:'+re.escape(self.scenario_id)+r'$',line):
                if self.begin is not None or self.end is not None:self.invalid=True
                else:self.begin=time.monotonic()
            if re.search(r'(?:^|\s)LAB_ACCEPTANCE_END:'+re.escape(self.scenario_id)+r'$',line):
                if self.begin is None or self.end is not None:self.invalid=True
                else:self.end=time.monotonic()

    def ready(self):
        self.update()
        return self.begin is not None and not self.invalid

    def finish(self, metadata, monitor_started):
        # Drain a bounded backlog; the process has exited and cannot grow this log.
        for _ in range(128):
            before=self.offset; self.update()
            if self.offset==before:break
        complete=False
        if (self.begin is not None and self.end is not None and not self.invalid
                and metadata.get('status')=='PASS' and monitor_started is not None
                and metadata.get('start_delay_seconds') is not None):
            capture_start=monitor_started+metadata['start_delay_seconds']
            duration=metadata.get('duration_seconds',0)
            complete=(duration>=0.5 and capture_start<=self.begin+0.5
                      and capture_start+duration>=self.end-0.5)
        metadata.update(scenario_id=self.scenario_id,scenario_window_complete=complete,
            scenario_window_detail=('Зафиксированы начало и конец сценария в пределах записи.' if complete
                else 'Не подтверждено, что начало и конец сценария попали в запись.'))
        return complete


class CaptureMonitor:
    """Start a bounded root-owned recording only after a visible X11 window exists."""

    def __init__(self, output, display=':99', max_seconds=58, poll_seconds=0.1, start_when=None):
        if not 0 < max_seconds <= 58 or not 0.01 <= poll_seconds <= 1:
            raise ValueError('Invalid bounded capture monitor settings')
        if not re.fullmatch(r':[0-9]{1,3}', display):
            raise ValueError('Capture monitor accepts only a local X11 display')
        self.output, self.display = Path(output), display
        self.path = self.output / 'minecraft_client_capture.mp4'
        self.max_seconds, self.poll_seconds = max_seconds, poll_seconds
        self.start_when=start_when
        self.metadata = {'status': 'FAIL', 'reason': 'no_visible_window',
                         'detail': 'No visible X11 window of at least 200×120 appeared',
                         'trigger_source': None, 'window_width': None, 'window_height': None,
                         'start_delay_seconds': None, 'capture_seconds_limit': max_seconds}
        self._stop = threading.Event()
        self._process_lock = threading.Lock()
        self._recorder = None
        self._thread = None
        self._started = None
        self._finished = False
        self._capture_error = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError('Capture monitor has already started')
        # Fail explicitly before dispatch when Xlib is unavailable; never fabricate
        # a placeholder movie. All actual X requests belong to the monitor thread.
        _xlib()
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._watch, name='lab-x11-capture', daemon=True)
        self._thread.start()
        return self

    def _watch(self):
        connection = None
        try:
            connection = X11Display(self.display)
            while not self._stop.is_set():
                if self.start_when is not None and not self.start_when():
                    self._stop.wait(self.poll_seconds)
                    continue
                window = connection.visible_window()
                if window is not None:
                    with self._process_lock:
                        if self._stop.is_set():
                            return
                        self.metadata.update(trigger_source='x11-visible-top-level',
                                             start_delay_seconds=round(time.monotonic() - self._started, 4),
                                             **window)
                        with (self.output / 'ffmpeg.log').open('wb') as log:
                            self._recorder = subprocess.Popen([
                                'ffmpeg', '-y', '-hide_banner', '-nostdin', '-loglevel', 'error',
                                '-f', 'x11grab', '-framerate', '10',
                                '-video_size', f"{window['screen_width']}x{window['screen_height']}",
                                '-i', self.display, '-t', str(self.max_seconds),
                                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                                '-movflags', '+faststart', str(self.path)],
                                stdout=log, stderr=subprocess.STDOUT, close_fds=True)
                    if self.start_when is not None:
                        while not self._stop.is_set() and self._recorder.poll() is None:
                            self.start_when()
                            self._stop.wait(self.poll_seconds)
                    return
                self._stop.wait(self.poll_seconds)
        except Exception as exc:
            self._capture_error = type(exc).__name__ + ': ' + str(exc)[:300]
        finally:
            if connection is not None:
                connection.close()

    def finish(self):
        if self._finished:
            return self.metadata
        self._finished = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self._capture_error = 'X11 capture monitor did not stop within two seconds'
        # _watch never holds this lock while waiting for Xlib or a child process.
        with self._process_lock:
            process = self._recorder
        if process is not None:
            was_running = process.poll() is None
            if was_running:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            if process.returncode not in (0, 255):
                self._capture_error = f'ffmpeg recorder exited with code {process.returncode}'
        if self._capture_error is not None:
            self.metadata.update(status='ERROR', reason='recorder_error', detail=self._capture_error)
        elif process is not None:
            self.metadata.update(probe_capture(self.path))
            if self.metadata.get('status') == 'PASS':
                # Export an actual decoded nonblack frame, never a world icon or
                # an illustrative replacement. Keep the original movie intact.
                frames = self.output / 'screenshots'
                frames.mkdir(mode=0o700, exist_ok=True)
                preview = frames / 'capture-frame.png'
                try:
                    extraction = subprocess.run([
                        'ffmpeg', '-y', '-nostdin', '-hide_banner', '-loglevel', 'error',
                        '-i', str(self.path), '-vf',
                        'signalstats,metadata=select:key=lavfi.signalstats.YAVG:value=20:function=greater',
                        '-frames:v', '1', '-update', '1', str(preview)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
                    if extraction.returncode == 0 and preview.is_file() and preview.stat().st_size:
                        self.metadata['still_frame'] = 'screenshots/capture-frame.png'
                    else:
                        self.metadata['still_frame_note'] = 'No suitable nonblack preview frame was exported'
                except (OSError, subprocess.SubprocessError) as exc:
                    self.metadata['still_frame_note'] = type(exc).__name__ + ': ' + str(exc)[:200]
        return self.metadata
