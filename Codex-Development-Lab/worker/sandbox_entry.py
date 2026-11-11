"""Image-owned supervisor. Verification children have a separate, unprivileged UID."""
import contextlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time


# Policy starts Python with -I, which ignores PYTHONPATH. Both the worker
# helpers and their common package live in the immutable image. Never add the
# workspace/cwd: project code must not shadow a supervisor dependency.
_IMAGE_ROOT = Path(__file__).resolve().parent.parent
for _TRUSTED in (str(_IMAGE_ROOT), str(_IMAGE_ROOT / 'worker')):
    if _TRUSTED not in sys.path:
        sys.path.insert(0, _TRUSTED)
from verification_plan import run_command, run_verification
from video_capture import CaptureMonitor, ScenarioWindow, configured_capture_max_seconds


def _finish(process, graceful=signal.SIGTERM):
    if process is None:
        return
    if process.poll() is None:
        process.send_signal(graceful)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@contextlib.contextmanager
def recording(output, window=None):
    display = ':99'
    xserver = capture = None
    recorder_home = Path('/tmp/lab-recorder')
    recorder_home.mkdir(mode=0o700, exist_ok=True)
    recorder_env = {**os.environ, 'HOME': str(recorder_home)}
    with (output / 'xvfb.log').open('wb') as xlog:
        try:
            xserver = subprocess.Popen(['Xvfb', display, '-screen', '0', '1280x720x24', '-nolisten', 'tcp'],
                                       stdout=xlog, stderr=subprocess.STDOUT, env=recorder_env, close_fds=True)
            deadline = time.monotonic() + 3
            while not Path('/tmp/.X11-unix/X99').exists():
                if xserver.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Xvfb failed to start')
                time.sleep(0.05)
            options = {'start_when': window.ready} if window is not None else {}
            capture = CaptureMonitor(output, display=display,
                                     max_seconds=configured_capture_max_seconds(), **options)
            try:
                capture.start()
            except Exception as exc:
                # The graphical child can still use the valid Xvfb display.
                # Optional capture setup is not a reason to skip its real checks.
                capture.metadata.update(status='ERROR', reason='recorder_error',
                                        detail=type(exc).__name__ + ': ' + str(exc)[:300])
            yield capture
        finally:
            try:
                if capture is not None:
                    try:
                        capture.finish()
                        if window is not None:
                            window.finish(capture.metadata,capture._started)
                    except Exception as exc:
                        capture.metadata.update(status='ERROR', reason='recorder_error',
                                                detail=type(exc).__name__ + ': ' + str(exc)[:300])
            finally:
                _finish(xserver)


def recorded_run(argv, cwd, logfile, timeout, *, scenario_id=None, env=None):
    started = time.monotonic()
    try:
        window = ScenarioWindow(logfile,scenario_id) if scenario_id is not None else None
        context = recording(logfile.parent,window=window) if window is not None else recording(logfile.parent)
        with context as capture:
            remaining = max(0.01, timeout - (time.monotonic() - started))
            command_env = dict(os.environ)
            command_env.update(env or {})
            # Loom 1.14 checks only presence: even CI='false' enables a second
            # xvfb-run display, while our recorder is watching the first one.
            command_env.pop('CI', None)
            command_env['DISPLAY'] = capture.display
            result = run_command(argv, cwd, logfile, remaining,
                                 env=command_env)
        # Capturing blank pixels is a separate failure from the client command.
        # The controller publishes a video only when this quality check passes.
        result['video_capture'] = dict(capture.metadata)
        result['duration_seconds'] = round(time.monotonic() - started, 3)
        return result
    except Exception as exc:
        logfile.write_text(f'{type(exc).__name__}: {exc}', encoding='utf-8')
        return {'status': 'ERROR', 'exit_code': None,
                'duration_seconds': round(time.monotonic() - started, 3),
                'tail': f'{type(exc).__name__}: {exc}'}


def _read_acceptance_request(workspace):
    """Read project-owned input without granting the supervisor DAC bypass."""
    original_uid, original_gid = os.geteuid(), os.getegid()
    try:
        os.setegid(1000)
        os.seteuid(1000)
        request = workspace / 'acceptance-request.json'
        if not os.path.lexists(request):
            return None
        descriptor = os.open(request, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                raise ValueError('Unsafe acceptance request')
            raw = source.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('Acceptance request exceeds size limit')
            return raw
    finally:
        os.seteuid(original_uid)
        os.setegid(original_gid)


def main():
    options = json.loads(sys.argv[1])
    home = Path(os.environ['HOME'])
    if options.get('verification'):
        if (set(options) != {'verification', 'timeout_seconds'} or options['verification'] is not True
                or type(options['timeout_seconds']) is not int
                or options['timeout_seconds'] < 60):
            raise ValueError('Invalid trusted verification options')
        if os.geteuid() != 0:
            raise RuntimeError('Verification requires the trusted root supervisor')
        if home != Path('/tmp/lab'):
            raise RuntimeError('Verification HOME must be the private temporary project home')
        # CAP_CHOWN is deliberately absent. Create project HOME as uid 1000,
        # then restore our saved root identity before supervising any commands.
        if not home.exists():
            os.setegid(1000)
            os.seteuid(1000)
            try:
                home.mkdir(mode=0o700)
            finally:
                os.seteuid(0)
                os.setegid(0)
        if home.is_symlink() or not home.is_dir() or home.stat().st_uid != 1000:
            raise RuntimeError('Verification project HOME has an unsafe owner or type')
        workspace = Path('/workspace')
        for name, target in (('.gradle', '/cache/project-gradle'), ('build', '/cache/build')):
            if os.path.lexists(workspace / name):
                raise RuntimeError('Unexpected generated workspace path: ' + name)
            if Path(target).is_symlink() or not Path(target).is_dir():
                raise RuntimeError('Private generated cache directory is missing or unsafe')
        os.setegid(1000)
        os.seteuid(1000)
        try:
            for name, target in (('.gradle', '/cache/project-gradle'), ('build', '/cache/build')):
                (workspace / name).symlink_to(target, target_is_directory=True)
        finally:
            os.seteuid(0)
            os.setegid(0)
        output = Path('/output')
        if output.is_symlink() or output.stat().st_uid != 0:
            raise RuntimeError('Verification output must belong to the trusted supervisor')
        os.chmod(output, 0o700)
        raw = _read_acceptance_request(workspace)
        if raw is not None:
            (output / 'acceptance-request.json').write_bytes(raw)
        report = run_verification(options['timeout_seconds'], recorded_run=recorded_run)
        return 1 if report['status'] == 'FAIL' else 0
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not options.get('record'):
        os.execvp(options['argv'][0], options['argv'])
    with recording(Path('/output')) as capture:
        command_env = dict(os.environ)
        command_env.pop('CI', None)
        command_env['DISPLAY'] = capture.display
        return subprocess.run(options['argv'], env=command_env).returncode


if __name__ == '__main__':
    sys.exit(main())
