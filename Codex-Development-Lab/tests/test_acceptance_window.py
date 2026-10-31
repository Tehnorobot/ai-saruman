"""Independent marker timing and real X11 capture tests, with synthetic pixels only."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from worker.video_capture import ScenarioWindow
from tests.test_video_capture import NATIVE, PAINT_WINDOW, entry


class ScenarioWindowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / 'test.log'
        self.window = ScenarioWindow(self.log, 'attack-one')

    def append(self, text, at):
        with self.log.open('ab') as handle:
            handle.write(text.encode())
        with patch('worker.video_capture.time.monotonic', return_value=at):
            self.window.update()

    def metadata(self, duration=3):
        return {'status': 'PASS', 'start_delay_seconds': 1, 'duration_seconds': duration}

    def test_start_waits_for_matching_complete_begin_line(self):
        self.assertFalse(self.window.ready())
        self.append('LAB_ACCEPTANCE_BEGIN:other\n', 11)
        self.assertFalse(self.window.ready())
        self.append('LAB_ACCEPTANCE_BEGIN:attack-one', 11)
        self.assertFalse(self.window.ready())
        self.append('\n', 11)
        self.assertTrue(self.window.ready())

    def test_interval_fully_inside_capture_is_complete(self):
        self.append('LAB_ACCEPTANCE_BEGIN:attack-one\n', 11)
        self.append('LAB_ACCEPTANCE_END:attack-one\n', 13)
        metadata = self.metadata()
        self.assertTrue(self.window.finish(metadata, 10))
        self.assertTrue(metadata['scenario_window_complete'])

    def test_truncated_capture_does_not_cover_end(self):
        self.append('LAB_ACCEPTANCE_BEGIN:attack-one\n', 11)
        self.append('LAB_ACCEPTANCE_END:attack-one\n', 20)
        self.assertFalse(self.window.finish(self.metadata(), 10))

    def test_missing_end_does_not_confirm_coverage(self):
        self.append('LAB_ACCEPTANCE_BEGIN:attack-one\n', 11)
        self.assertFalse(self.window.finish(self.metadata(), 10))

    def test_end_before_begin_and_duplicate_begin_are_invalid(self):
        self.append('LAB_ACCEPTANCE_END:attack-one\nLAB_ACCEPTANCE_BEGIN:attack-one\n', 11)
        self.assertFalse(self.window.finish(self.metadata(), 10))

    def test_bad_or_missing_capture_quality_does_not_confirm_coverage(self):
        self.append('LAB_ACCEPTANCE_BEGIN:attack-one\n', 11)
        self.append('LAB_ACCEPTANCE_END:attack-one\n', 13)
        for metadata in ({'status': 'FAIL'}, {'status': 'PASS'}, self.metadata(0.1)):
            with self.subTest(metadata=metadata):
                self.assertFalse(self.window.finish(metadata, 10))


@unittest.skipUnless(NATIVE, 'Opt-in Linux root worker-image capture integration required')
class NativeScenarioWindowTests(unittest.TestCase):
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

    def test_real_capture_waits_for_marker_and_covers_synthetic_interval(self):
        script = PAINT_WINDOW.replace('deadline = time.monotonic() + 1.6',
            'time.sleep(0.5)\nprint("LAB_ACCEPTANCE_BEGIN:attack-one", flush=True)\n'
            'deadline = time.monotonic() + 1.6').replace(
            'x.XDestroyWindow(d, window); x.XCloseDisplay(d)',
            'print("LAB_ACCEPTANCE_END:attack-one", flush=True)\ntime.sleep(0.3)\n'
            'x.XDestroyWindow(d, window); x.XCloseDisplay(d)')
        result = entry.recorded_run([sys.executable, '-I', '-c', script], self.workspace,
            self.output / 'test.log', 15, scenario_id='attack-one')
        self.assertEqual(result['status'], 'PASS', result)
        metadata = result['video_capture']
        self.assertEqual(metadata['status'], 'PASS', metadata)
        self.assertTrue(metadata['scenario_window_complete'], metadata)
        self.assertGreaterEqual(metadata['start_delay_seconds'], 0.8)

    def test_visible_pixels_without_matching_markers_do_not_become_scenario_proof(self):
        result = entry.recorded_run([sys.executable, '-I', '-c', PAINT_WINDOW], self.workspace,
            self.output / 'test.log', 12, scenario_id='attack-one')
        self.assertEqual(result['status'], 'PASS', result)
        self.assertFalse(result['video_capture']['scenario_window_complete'])
        self.assertNotEqual(result['video_capture']['status'], 'PASS')
        self.assertFalse((self.output / 'minecraft_client_capture.mp4').exists())


if __name__ == '__main__':
    unittest.main()
