"""Operator duration reaches isolated recording without broadening runner rights."""
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import db, recording_settings
from runner.policy import container_config, validate_request
from worker.sandbox import DockerSandbox, RunnerAPIError


class Submitted(Exception):
    pass


class RecordingTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = self.root / 'projects'
        self.workspace = self.projects / ('a' * 14) / 'workspace'
        self.workspace.mkdir(parents=True)
        (self.workspace / 'source.py').write_text('value = 1\n')
        self.log = self.root / 'run.log'
        self.setting = patch.object(db, 'DB', self.root / 'lab.sqlite3')
        self.setting.start()
        self.addCleanup(self.setting.stop)
        self.paths = patch.dict('os.environ', {'LAB_PROJECTS': str(self.projects)})
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.sandbox = DockerSandbox()

    def request(self, *, verification=False, record=True):
        captured = []

        def api(method, path, body=None, **kwargs):
            if path.startswith('/runs/by-job/'):
                raise RunnerAPIError(404, 'No previous run')
            if method == 'POST' and path == '/runs':
                captured.append(body)
                raise Submitted
            self.fail((method, path))

        with patch.object(self.sandbox, 'api', side_effect=api), \
                patch.object(self.sandbox, 'recover'), self.assertRaises(Submitted):
            self.sandbox._execute(['echo', 'capture'], self.workspace, self.log, 120,
                                  'b' * 14, lambda: None, record=record,
                                  verification={'timeout_seconds': 120, 'eula': False} if verification else None)
        return captured[0]

    def test_saved_value_is_refreshed_at_each_test_run(self):
        for duration in (75, 1200, 100001):
            recording_settings.set_max_seconds(duration)
            for batch in (False, True):
                request = self.request(verification=batch)
                self.assertEqual(request['capture_max_seconds'], duration)
                validate_request(request)
                config = container_config('trusted-image', 'private-volume', 'c' * 32, request, 'd' * 32)
                self.assertIn('LAB_VIDEO_MAX_SECONDS=' + str(duration), config['Env'])
                self.assertFalse(config['HostConfig'].get('Binds'))
                self.assertTrue(all(m['Type'] == 'volume' for m in config['HostConfig']['Mounts']))

    def test_setting_read_failure_does_not_cancel_tests(self):
        with patch.object(recording_settings, 'max_seconds', side_effect=sqlite3.OperationalError('unavailable')):
            request = self.request(verification=True)
        self.assertEqual(request['capture_max_seconds'], recording_settings.DEFAULT_MAX_SECONDS)

    def test_commands_without_recording_do_not_read_recording_settings(self):
        with patch.object(recording_settings, 'max_seconds') as read:
            request = self.request(record=False)
        read.assert_not_called()
        self.assertNotIn('capture_max_seconds', request)

    def test_duration_does_not_allow_extra_docker_or_environment_fields(self):
        base = {'project': 'a' * 14, 'job': 'b' * 14, 'argv': ['echo'],
                'record': True, 'capture_max_seconds': 600}
        for key in ('env', 'HostConfig', 'mounts', 'image'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_request({**base, key: {}})
        for invalid in (0, -1, True, '600', 2.5):
            with self.subTest(duration=invalid), self.assertRaises(ValueError):
                validate_request({**base, 'capture_max_seconds': invalid})


if __name__ == '__main__':
    unittest.main()
