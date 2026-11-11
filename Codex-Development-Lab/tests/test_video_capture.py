"""Independent capture regressions. Opt-in native fixtures never launch Minecraft."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from worker import sandbox_entry as entry
from worker import verification_plan as plan
from worker import video_capture as capture


class RecordedEnvironmentTests(unittest.TestCase):
    def test_ci_is_absent_not_false_and_parent_environment_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            monitor = SimpleNamespace(display=':99', metadata={'status': 'PASS', 'reason': 'fixture'})
            for value in ('true', 'false', ''):
                with self.subTest(ci=value), patch.dict(os.environ, {'CI': value, 'UNRELATED_CAPTURE_TEST': 'keep'}):
                    before = dict(os.environ)
                    with patch.object(entry, 'recording', return_value=contextlib.nullcontext(monitor)), \
                         patch.object(entry, 'run_command', return_value={'status': 'PASS', 'exit_code': 0, 'tail': 'command'}) as command:
                        result = entry.recorded_run(['synthetic-client'], output, output / 'client.log', 30)
                    child_env = command.call_args.kwargs['env']
                    self.assertNotIn('CI', child_env)
                    self.assertEqual(child_env['DISPLAY'], ':99')
                    self.assertEqual(child_env['UNRELATED_CAPTURE_TEST'], 'keep')
                    self.assertEqual(dict(os.environ), before)
                    self.assertEqual(result['status'], 'PASS')

    def test_capture_failure_is_separate_from_command_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            quality = {'status': 'FAIL', 'reason': 'No visible window appeared'}
            monitor = SimpleNamespace(display=':99', metadata=quality)
            with patch.object(entry, 'recording', return_value=contextlib.nullcontext(monitor)), \
                 patch.object(entry, 'run_command', return_value={'status': 'PASS', 'exit_code': 0, 'tail': 'client assertions completed'}):
                result = entry.recorded_run(['synthetic-client'], output, output / 'client.log', 30)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(result['tail'], 'client assertions completed')
            self.assertEqual(result['video_capture'], quality)

    def test_command_failure_is_not_hidden_by_good_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            monitor = SimpleNamespace(display=':99', metadata={'status': 'PASS'})
            with patch.object(entry, 'recording', return_value=contextlib.nullcontext(monitor)), \
                 patch.object(entry, 'run_command', return_value={'status': 'FAIL', 'exit_code': 7, 'tail': 'failed assertion'}):
                result = entry.recorded_run(['synthetic-client'], output, output / 'client.log', 30)
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['exit_code'], 7)
            self.assertEqual(result['video_capture']['status'], 'PASS')

    def test_pipeline_reports_video_failure_without_rewriting_client_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / 'workspace'
            output = Path(temporary) / 'output'
            workspace.mkdir()
            output.mkdir()
            (output / 'minecraft_client_capture.mp4').write_bytes(b'unit fixture, not a valid video')
            selected = {'label': 'gradle_build', 'argv': ['synthetic', 'build'],
                        'gradle': ['synthetic'], 'minecraft': True}
            def command(argv, cwd, logfile, timeout):
                logfile.write_text('runClientGameTest - Synthetic task\n' if 'tasks' in argv else '')
                return {'status': 'PASS', 'exit_code': 0, 'tail': '', 'duration_seconds': 0.01}
            def recording(*args):
                return {'status': 'PASS', 'exit_code': 0, 'tail': 'client assertions completed',
                        'duration_seconds': 0.02,
                        'video_capture': {'status': 'FAIL', 'reason': 'Entire capture is black'}}
            with patch.object(plan, 'select_plan', return_value=selected), \
                 patch.object(plan, 'run_command', side_effect=command), \
                 patch.object(plan, 'collect_artifacts', return_value=[{'filename': 'fixture.jar', 'size': 1, 'sha256': 'a' * 64}]), \
                 patch.dict(os.environ, {'MINECRAFT_EULA_ACCEPTED': 'true'}):
                report = plan.run_verification(30, workspace=workspace, output=output, recorded_run=recording)
            checks = {check['label']: check for check in report['checks']}
            self.assertEqual(checks['minecraft_client_gametest']['status'], 'PASS')
            self.assertEqual(checks['minecraft_client_gametest']['exit_code'], 0)
            self.assertNotIn('video_capture', checks)
            self.assertEqual(report['capture_diagnostics'][0]['status'], 'FAIL')
            self.assertEqual(report['status'], 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED')
            self.assertIsNone(report['video'])


class CaptureProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'fixture.mp4'
        self.path.write_bytes(b'unit probe fixture' * 300)

    def probe(self, *, duration='1.5', width=640, height=360, codec='h264', black_stderr=''):
        info = {'streams': [{'codec_type': 'video', 'codec_name': codec, 'width': width,
                             'height': height, 'duration': duration}], 'format': {'duration': duration}}
        def run(argv, *args, **kwargs):
            if Path(argv[0]).name.startswith('ffprobe'):
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(info).encode(), stderr=b'')
            return subprocess.CompletedProcess(argv, 0, stdout=b'', stderr=black_stderr.encode())
        with patch.object(capture.subprocess, 'run', side_effect=run):
            return capture.probe_capture(self.path)

    def test_valid_probe_keeps_codec_dimensions_duration_and_black_fraction(self):
        result = self.probe()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['codec'], 'h264')
        self.assertEqual((result['width'], result['height']), (640, 360))
        self.assertEqual(result['duration_seconds'], 1.5)
        self.assertEqual(result['bytes'], self.path.stat().st_size)
        self.assertLess(result['black_fraction'], 0.97)

    def test_black_clip_is_rejected(self):
        result = self.probe(black_stderr='[blackdetect] black_start:0 black_end:1.5 black_duration:1.5\n')
        self.assertEqual(result['status'], 'FAIL', result)
        self.assertGreaterEqual(result['black_fraction'], 0.97)

    def test_short_black_loading_interval_does_not_reject_a_nonblack_capture(self):
        result = self.probe(duration='3', black_stderr='[blackdetect] black_start:0 black_end:0.6 black_duration:0.6\n')
        self.assertEqual(result['status'], 'PASS', result)
        self.assertAlmostEqual(result['black_fraction'], 0.2, places=3)

    def test_invalid_duration_or_dimensions_never_produce_success(self):
        for fields in ({'duration': '0'}, {'duration': '-1'}, {'duration': 'nan'},
                       {'duration': 'inf'}, {'width': 0}, {'height': 0}):
            with self.subTest(fields=fields):
                self.assertNotEqual(self.probe(**fields)['status'], 'PASS')

    def test_missing_capture_is_not_validated(self):
        self.path.unlink()
        self.assertNotEqual(capture.probe_capture(self.path)['status'], 'PASS')


# This program is executed only inside the opt-in Linux test container, as uid
# 1000 through the normal trusted child launcher. It paints a real X11 window;
# it is deliberately a synthetic fixture and supplies no Minecraft evidence.
PAINT_WINDOW = r'''
import ctypes, os, time
assert os.getuid() == os.getgid() == 1000
assert 'CI' not in os.environ
time.sleep(0.4)
x = ctypes.CDLL('libX11.so.6')
pointer, ulong, integer = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
x.XOpenDisplay.argtypes = [ctypes.c_char_p]; x.XOpenDisplay.restype = pointer
x.XDefaultRootWindow.argtypes = [pointer]; x.XDefaultRootWindow.restype = ulong
x.XCreateSimpleWindow.argtypes = [pointer, ulong, integer, integer, ctypes.c_uint,
                                  ctypes.c_uint, ctypes.c_uint, ulong, ulong]
x.XCreateSimpleWindow.restype = ulong
x.XMapRaised.argtypes = [pointer, ulong]
x.XSetWindowBackground.argtypes = [pointer, ulong, ulong]
x.XClearWindow.argtypes = [pointer, ulong]
x.XFlush.argtypes = [pointer]
x.XDestroyWindow.argtypes = [pointer, ulong]
x.XCloseDisplay.argtypes = [pointer]
d = x.XOpenDisplay(None)
assert d, 'Synthetic painter could not open DISPLAY'
window = x.XCreateSimpleWindow(d, x.XDefaultRootWindow(d), 30, 30, 800, 500, 0, 0, 0x3388cc)
assert window
x.XMapRaised(d, window); x.XFlush(d)
deadline = time.monotonic() + 1.6
colors = [0x3388cc, 0xcc8833, 0x33cc88]
frame = 0
while time.monotonic() < deadline:
    x.XSetWindowBackground(d, window, colors[frame % len(colors)])
    x.XClearWindow(d, window); x.XFlush(d)
    time.sleep(0.1); frame += 1
x.XDestroyWindow(d, window); x.XCloseDisplay(d)
'''


NATIVE = (os.getenv('LAB_RUN_VIDEO_TESTS') == '1' and sys.platform.startswith('linux')
          and hasattr(os, 'geteuid') and os.geteuid() == 0
          and all(shutil.which(tool) for tool in ('Xvfb', 'ffmpeg', 'ffprobe')))


@unittest.skipUnless(NATIVE, 'Opt-in Linux root worker-image capture integration required')
class NativeCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o755)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir(mode=0o777)
        self.workspace.chmod(0o777)
        self.output = self.root / 'output'
        self.output.mkdir(mode=0o700)

    def test_real_visible_x11_window_triggers_nonblack_encoded_capture(self):
        with patch.dict(os.environ, {'CI': 'true'}):
            result = entry.recorded_run([sys.executable, '-I', '-c', PAINT_WINDOW], self.workspace,
                                        self.output / 'client.log', 12)
        self.assertEqual(result['status'], 'PASS', result)
        metadata = result['video_capture']
        self.assertEqual(metadata['status'], 'PASS', metadata)
        self.assertEqual(metadata['trigger_source'], 'x11-visible-top-level')
        self.assertGreaterEqual(metadata['start_delay_seconds'], 0.2)
        self.assertGreaterEqual(metadata['window_width'], 320)
        self.assertGreaterEqual(metadata['window_height'], 240)
        self.assertEqual(metadata['codec'], 'h264')
        self.assertGreater(metadata['duration_seconds'], 0)
        self.assertLessEqual(metadata['duration_seconds'], 60)
        self.assertLess(metadata['black_fraction'], 0.97)
        self.assertGreater((self.output / 'minecraft_client_capture.mp4').stat().st_size, 100)
        self.assertEqual(metadata['still_frame'], 'screenshots/capture-frame.png')
        preview = self.output / metadata['still_frame']
        self.assertTrue(preview.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))

    def test_no_window_preserves_command_success_but_rejects_capture(self):
        result = entry.recorded_run([sys.executable, '-I', '-c', 'import time; time.sleep(0.35)'],
                                    self.workspace, self.output / 'client.log', 8)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertNotEqual(result['video_capture']['status'], 'PASS')
        self.assertIsNone(result['video_capture']['trigger_source'])

    def test_recorder_start_failure_does_not_skip_the_graphical_child(self):
        with patch.object(entry.CaptureMonitor, 'start', side_effect=RuntimeError('synthetic recorder failure')):
            result = entry.recorded_run([sys.executable, '-I', '-c', PAINT_WINDOW], self.workspace,
                                        self.output / 'client.log', 12)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['video_capture']['status'], 'ERROR')
        self.assertIn('synthetic recorder failure', result['video_capture']['detail'])

    @unittest.skipUnless(os.getenv('LAB_RUN_LONG_VIDEO_TESTS') == '1', 'Opt-in 64-second capture fixture')
    def test_recording_over_one_minute_stops_at_the_supervisor_selected_limit(self):
        painter = PAINT_WINDOW.replace('+ 1.6', '+ 64.0')
        with patch.dict(os.environ, {'LAB_VIDEO_MAX_SECONDS': '62'}):
            result = entry.recorded_run([sys.executable, '-I', '-c', painter], self.workspace,
                                        self.output / 'client.log', 90)
        self.assertEqual(result['status'], 'PASS', result)
        metadata = result['video_capture']
        self.assertEqual(metadata['status'], 'PASS', metadata)
        self.assertEqual(metadata['capture_seconds_limit'], 62)
        self.assertGreater(metadata['duration_seconds'], 60)
        self.assertLessEqual(metadata['duration_seconds'], 62.25)

    def test_real_encoded_black_fixture_fails_quality_probe(self):
        path = self.output / 'synthetic-black.mp4'
        subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'color=c=black:s=320x240:r=10', '-t', '1.2',
                        '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
        metadata = capture.probe_capture(path)
        self.assertEqual(metadata['status'], 'FAIL', metadata)
        self.assertGreaterEqual(metadata['black_fraction'], 0.97)
        self.assertEqual(metadata['codec'], 'h264')
        self.assertEqual((metadata['width'], metadata['height']), (320, 240))
