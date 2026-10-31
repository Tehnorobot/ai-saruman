"""Run in Linux as root with SETUID/SETGID, without DAC override or CHOWN."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from worker import sandbox_entry as entry


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'seteuid'),
                     'Linux identity and descriptor boundaries')
class AcceptanceRequestAccessTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid() != 0:
            self.skipTest('Requires the trusted root supervisor identity')
        status = Path('/proc/self/status').read_text()
        effective = int(next(line.split()[1] for line in status.splitlines()
                             if line.startswith('CapEff:')), 16)
        if effective & ((1 << 1) | (1 << 2)):
            self.skipTest('Run without DAC_OVERRIDE and DAC_READ_SEARCH')
        self.identity = os.geteuid(), os.getegid()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o777)
        self.workspace = self.root / 'workspace'
        self.request = self.workspace / 'acceptance-request.json'
        self.as_project(lambda: self.workspace.mkdir(mode=0o755))
        # Cleanup must run as the owner, too: the supervisor has no DAC bypass.
        self.addCleanup(self.cleanup_project)

    def as_project(self, operation):
        os.setegid(1000)
        os.seteuid(1000)
        try:
            return operation()
        finally:
            os.seteuid(self.identity[0])
            os.setegid(self.identity[1])

    def cleanup_project(self):
        self.as_project(lambda: shutil.rmtree(self.workspace))

    def assert_identity_restored(self):
        self.assertEqual((os.geteuid(), os.getegid()), self.identity)
        output = self.root / 'supervisor-only'
        output.mkdir(mode=0o700, exist_ok=True)
        (output / 'result').write_bytes(b'trusted output remains writable')

    def test_private_project_request_is_read_without_changing_permissions(self):
        self.as_project(lambda: self.request.write_bytes(b'{"requirements": []}'))
        self.as_project(lambda: self.request.chmod(0o600))
        with self.assertRaises(PermissionError):
            self.request.read_bytes()
        self.assertEqual(entry._read_acceptance_request(self.workspace), b'{"requirements": []}')
        self.assertEqual(self.request.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.request.stat().st_uid, 1000)
        self.assert_identity_restored()

    def test_missing_request_is_optional_and_identity_is_restored(self):
        self.assertIsNone(entry._read_acceptance_request(self.workspace))
        self.assert_identity_restored()

    def test_symlink_is_rejected_and_identity_is_restored(self):
        self.as_project(lambda: self.request.symlink_to('/etc/passwd'))
        with self.assertRaises(OSError):
            entry._read_acceptance_request(self.workspace)
        self.assert_identity_restored()

    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        self.as_project(lambda: os.mkfifo(self.request, 0o600))
        started = time.monotonic()
        with self.assertRaises(ValueError):
            entry._read_acceptance_request(self.workspace)
        self.assertLess(time.monotonic() - started, 1)
        self.assert_identity_restored()

    def test_directory_is_rejected_and_identity_is_restored(self):
        self.as_project(lambda: self.request.mkdir())
        with self.assertRaises((OSError, ValueError)):
            entry._read_acceptance_request(self.workspace)
        self.assert_identity_restored()

    def test_oversized_request_is_rejected_and_identity_is_restored(self):
        self.as_project(lambda: self.request.write_bytes(b' ' * (1024 * 1024 + 1)))
        with self.assertRaises(ValueError):
            entry._read_acceptance_request(self.workspace)
        self.assert_identity_restored()

    def test_full_supervisor_copies_private_request_before_verification(self):
        self.as_project(lambda: self.request.write_bytes(b'{"requirements": []}'))
        self.as_project(lambda: self.request.chmod(0o600))
        home = self.root / 'home'
        self.as_project(lambda: home.mkdir(mode=0o700))
        output = self.root / 'output'
        output.mkdir(mode=0o700)
        cache = self.root / 'cache'
        cache.mkdir(mode=0o777)
        cache.chmod(0o777)
        self.as_project(lambda: (cache / 'project-gradle').mkdir())
        self.as_project(lambda: (cache / 'build').mkdir())
        self.addCleanup(lambda: self.as_project(lambda: shutil.rmtree(cache)))
        self.addCleanup(lambda: self.as_project(lambda: home.rmdir()))
        real_path = Path

        def fixture_path(value):
            return {'/workspace': self.workspace, '/output': output,
                    '/tmp/lab': home, '/cache/project-gradle': cache / 'project-gradle',
                    '/cache/build': cache / 'build'}.get(str(value), real_path(value))

        def verified(*args, **kwargs):
            self.assertEqual((os.geteuid(), os.getegid()), self.identity)
            self.assertEqual((output / 'acceptance-request.json').read_bytes(), b'{"requirements": []}')
            self.assertEqual((output / 'acceptance-request.json').stat().st_uid, 0)
            return {'status': 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED'}

        with patch.object(entry, 'Path', side_effect=fixture_path), \
             patch.dict(os.environ, {'HOME': str(home)}), \
             patch.object(entry.sys, 'argv', ['supervisor', json.dumps({'verification': True, 'timeout_seconds': 60})]), \
             patch.object(entry, 'run_verification', side_effect=verified) as verify:
            self.assertEqual(entry.main(), 0)
            verify.assert_called_once()
        self.assert_identity_restored()


if __name__ == '__main__':
    unittest.main()
