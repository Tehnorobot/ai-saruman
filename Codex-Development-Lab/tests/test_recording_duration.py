"""Duration controls apply to the trusted recorder and real encoded media."""
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from worker import sandbox_entry as entry
from worker import video_capture as capture


class RecordingDurationTests(unittest.TestCase):
    def test_positive_operator_seconds_have_no_one_minute_or_other_duration_ceiling(self):
        for value in (1, 61, 300, 7200, 100000):
            with self.subTest(seconds=value):
                monitor = capture.CaptureMonitor(Path('.'), max_seconds=value)
                self.assertEqual(monitor.max_seconds, value)
                self.assertEqual(monitor.metadata['capture_seconds_limit'], value)
        for value in (0, -1, True, 1.5, '300', None):
            with self.subTest(invalid=value), self.assertRaises(ValueError):
                capture.CaptureMonitor(Path('.'), max_seconds=value)

    def test_supervisor_environment_is_parsed_without_project_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(capture.configured_capture_max_seconds(), 300)
        for value in ('1', '61', '7200'):
            with patch.dict(os.environ, {'LAB_VIDEO_MAX_SECONDS': value}):
                self.assertEqual(capture.configured_capture_max_seconds(), int(value))
        for value in ('', '0', '-1', 'true', '1.5', ' 120', '120;echo no'):
            with self.subTest(value=value), patch.dict(os.environ, {'LAB_VIDEO_MAX_SECONDS': value}), self.assertRaises(ValueError):
                capture.configured_capture_max_seconds()

    def test_project_child_environment_cannot_change_recorder_configuration(self):
        selected = []
        @contextlib.contextmanager
        def recording(*args, **kwargs):
            selected.append(entry.configured_capture_max_seconds())
            yield SimpleNamespace(display=':99', metadata={'status': 'PASS'})
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'LAB_VIDEO_MAX_SECONDS': '180'}):
            output = Path(temporary)
            with patch.object(entry, 'recording', recording), patch.object(entry, 'run_command',
                    return_value={'status': 'PASS', 'exit_code': 0}):
                result = entry.recorded_run(['fixture'], output, output / 'fixture.log', 10,
                                            env={'LAB_VIDEO_MAX_SECONDS': '1'})
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(selected, [180])

    def test_selected_duration_reaches_real_ffmpeg_argument_position(self):
        with tempfile.TemporaryDirectory() as temporary:
            monitor = capture.CaptureMonitor(Path(temporary), max_seconds=185)
            monitor._started = time.monotonic()
            display = SimpleNamespace(visible_window=lambda: {'screen_width': 640, 'screen_height': 480}, close=lambda: None)
            with patch.object(capture, 'X11Display', return_value=display), patch.object(capture.subprocess, 'Popen') as spawn:
                monitor._watch()
            argv = spawn.call_args.args[0]
            self.assertEqual(argv[argv.index('-t') + 1], '185')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Real ffmpeg/ffprobe required')
class RealLongDurationProbeTests(unittest.TestCase):
    def test_real_clip_longer_than_one_minute_is_decoded_and_checked_against_its_selected_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'synthetic-61-seconds.mp4'
            subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                            '-i', 'color=c=blue:s=64x48:r=1', '-t', '61', '-c:v', 'libx264',
                            '-threads', '1', '-pix_fmt', 'yuv420p', str(path)],
                           check=True, capture_output=True, timeout=30)
            result = capture.probe_capture(path, max_seconds=120)
            self.assertEqual(result['status'], 'PASS', result)
            self.assertGreater(result['duration_seconds'], 60)
            self.assertNotEqual(capture.probe_capture(path, max_seconds=58)['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
