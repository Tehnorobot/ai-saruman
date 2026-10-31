"""Independent bounded retry/recovery regressions; no Docker, models or games."""
import errno
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from common import safe_archive
from common.project_lock import project_lock
from worker.sandbox import DockerSandbox, RunnerAPIError, RunnerCleanupError


DELAYS = [0.05, 0.1, 0.2, 0.4, 0.8]


def snapshot():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        content = b'new source bytes\n'
        member = tarfile.TarInfo('source.txt')
        member.size = len(content)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(content))
    stream.seek(0)
    return stream


class FilesystemRetryPolicy(unittest.TestCase):
    def test_temporary_errors_retry_with_same_arguments_and_return_value(self):
        for number in (errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY):
            with self.subTest(errno=number):
                operation = Mock(side_effect=[OSError(number, 'temporary'),
                                              OSError(number, 'temporary'), 'done'])
                with patch.object(safe_archive.time, 'sleep') as sleep:
                    value = safe_archive._retry_fs(operation, 'relative-name', dir_fd=123)
                self.assertEqual(value, 'done')
                self.assertEqual(operation.call_args_list,
                                 [call('relative-name', dir_fd=123)] * 3)
                self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])

    def test_persistent_temporary_error_is_bounded_and_preserves_exception(self):
        failure = OSError(errno.EBUSY, 'permanent sharing violation')
        operation = Mock(side_effect=failure)
        with patch.object(safe_archive.time, 'sleep') as sleep:
            with self.assertRaises(OSError) as caught:
                safe_archive._retry_fs(operation, 'relative-name', dir_fd=123)
        self.assertIs(caught.exception, failure)
        self.assertEqual(operation.call_count, 6)
        self.assertEqual(sleep.call_args_list, [call(delay) for delay in DELAYS])

    def test_other_errors_are_not_retried(self):
        for number in (errno.ENOENT, errno.ELOOP, errno.ENOSPC, errno.EIO,
                       errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EXDEV):
            with self.subTest(errno=number):
                failure = OSError(number, 'not a sharing violation')
                operation = Mock(side_effect=failure)
                with patch.object(safe_archive.time, 'sleep') as sleep:
                    with self.assertRaises(OSError) as caught:
                        safe_archive._retry_fs(operation)
                self.assertIs(caught.exception, failure)
                operation.assert_called_once_with()
                sleep.assert_not_called()

    def test_success_and_validation_failure_never_sleep(self):
        with patch.object(safe_archive.time, 'sleep') as sleep:
            operation = Mock(return_value=None)
            self.assertIsNone(safe_archive._retry_fs(operation))
            failure = ValueError('unsafe path')
            with self.assertRaises(ValueError) as caught:
                safe_archive._retry_fs(Mock(side_effect=failure))
        self.assertIs(caught.exception, failure)
        operation.assert_called_once_with()
        sleep.assert_not_called()


@unittest.skipUnless(sys.platform.startswith('linux'),
                     'Real descriptor-relative replacement requires Linux')
class WorkspaceRetryRecovery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / ('a' * 14)
        self.project.mkdir()
        self.workspace = self.project / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'source.txt').write_bytes(b'old source bytes\n')
        self.external = self.base / 'outside'
        self.external.mkdir()
        self.sentinel = self.external / 'sentinel.txt'
        self.sentinel.write_bytes(b'outside bytes must survive\n')
        self.journal = self.project / '.lab-workspace-swap.json'

    def assert_clean(self):
        self.assertEqual((self.workspace / 'source.txt').read_bytes(), b'new source bytes\n')
        self.assertEqual(self.sentinel.read_bytes(), b'outside bytes must survive\n')
        self.assertFalse(self.journal.exists())
        self.assertFalse(list(self.project.glob('.lab-incoming-*')))
        self.assertFalse(list(self.project.glob('.lab-previous-*')))

    def test_permissive_bind_directory_never_requires_chmod_ownership(self):
        library=self.workspace/'libs';library.mkdir();library.chmod(0o777)
        (library/'supplied.jar').write_bytes(b'user supplied bytes')
        real_chmod=os.fchmod
        def owner_only(fd,mode):
            if os.fstat(fd).st_ino==library_inode:
                raise PermissionError(errno.EPERM,'Simulated non-owned Windows bind directory')
            return real_chmod(fd,mode)
        library_inode=library.stat().st_ino
        with self.force_journal(),patch.object(safe_archive.os,'fchmod',side_effect=owner_only):
            safe_archive.replace_tree(snapshot(),self.workspace)
        self.assert_clean()

    def test_owned_read_only_directory_is_made_removable(self):
        directory=self.workspace/'readonly';directory.mkdir()
        (directory/'source').write_bytes(b'old')
        directory.chmod(0o500)
        with self.force_journal():safe_archive.replace_tree(snapshot(),self.workspace)
        self.assert_clean()

    def test_truly_denied_permission_keeps_recovery_journal_and_new_source(self):
        directory=self.workspace/'restricted';directory.mkdir();directory.chmod(0o500)
        inode=directory.stat().st_ino;real_chmod=os.fchmod
        def denied(fd,mode):
            if os.fstat(fd).st_ino==inode:raise PermissionError(errno.EPERM,'Required chmod denied')
            return real_chmod(fd,mode)
        with self.force_journal(),patch.object(safe_archive.os,'fchmod',side_effect=denied):
            with self.assertRaises(PermissionError):safe_archive.replace_tree(snapshot(),self.workspace)
        self.assertTrue(self.journal.exists())
        self.assertEqual((self.workspace/'source.txt').read_bytes(),b'new source bytes\n')
        safe_archive.recover_tree(self.workspace)
        self.assert_clean()

    def test_old_workspace_symlink_cleanup_never_deletes_external_target(self):
        (self.workspace/'external-link').symlink_to(self.external,target_is_directory=True)
        with self.force_journal():safe_archive.replace_tree(snapshot(),self.workspace)
        self.assert_clean()

    def force_journal(self):
        return patch.object(safe_archive, '_exchange',
                            side_effect=OSError(errno.EOPNOTSUPP, 'no exchange support'))

    def test_temporary_descriptor_relative_rename_recovers(self):
        original = os.rename
        attempts = []

        def sharing_violation(source, destination, *args, **kwargs):
            if source == 'workspace':
                attempts.append((source, destination, kwargs.copy()))
                self.assertIsInstance(kwargs['src_dir_fd'], int)
                self.assertEqual(kwargs['src_dir_fd'], kwargs['dst_dir_fd'])
                if len(attempts) <= 2:
                    raise OSError(errno.EACCES, 'Windows sharing violation')
            return original(source, destination, *args, **kwargs)

        with self.force_journal(), \
             patch.object(safe_archive.os, 'rename', side_effect=sharing_violation), \
             patch.object(safe_archive.time, 'sleep') as sleep:
            safe_archive.replace_tree(snapshot(), self.workspace)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])
        self.assert_clean()

    def test_temporary_empty_previous_rmdir_recovers(self):
        original = os.rmdir
        attempts = []

        def sharing_violation(name, *args, **kwargs):
            if str(name).startswith('.lab-previous-'):
                attempts.append(name)
                self.assertIsInstance(kwargs['dir_fd'], int)
                if len(attempts) <= 2:
                    raise OSError(errno.ENOTEMPTY, 'Transient directory sharing state')
            return original(name, *args, **kwargs)

        with self.force_journal(), \
             patch.object(safe_archive.os, 'rmdir', side_effect=sharing_violation), \
             patch.object(safe_archive.time, 'sleep') as sleep:
            safe_archive.replace_tree(snapshot(), self.workspace)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])
        self.assert_clean()

    def test_temporary_exchange_errors_retry_before_unsupported_fallback(self):
        failures = [OSError(errno.EBUSY, 'Temporary exchange sharing violation'),
                    OSError(errno.EACCES, 'Temporary exchange sharing violation'),
                    OSError(errno.EINVAL, 'Exchange unsupported on this filesystem')]
        with patch.object(safe_archive, '_exchange', side_effect=failures) as exchange, \
             patch.object(safe_archive.time, 'sleep') as sleep:
            safe_archive.replace_tree(snapshot(), self.workspace)
        self.assertEqual(exchange.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])
        self.assert_clean()

    def test_temporary_journal_unlink_finishes_recovery(self):
        original = os.unlink
        attempts = []

        def sharing_violation(name, *args, **kwargs):
            if name == '.lab-workspace-swap.json':
                attempts.append(name)
                self.assertIsInstance(kwargs['dir_fd'], int)
                if len(attempts) <= 2:
                    raise OSError(errno.EACCES, 'Temporary journal sharing violation')
            return original(name, *args, **kwargs)

        with self.force_journal(), \
             patch.object(safe_archive.os, 'unlink', side_effect=sharing_violation), \
             patch.object(safe_archive.time, 'sleep') as sleep:
            safe_archive.replace_tree(snapshot(), self.workspace)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])
        self.assert_clean()

    def test_permanent_rename_keeps_both_trees_and_recovery_journal(self):
        original = os.rename
        attempts = []

        def denied(source, destination, *args, **kwargs):
            if source == 'workspace':
                attempts.append(source)
                raise OSError(errno.EPERM, 'Persistent sharing violation')
            return original(source, destination, *args, **kwargs)

        with self.force_journal(), patch.object(safe_archive.os, 'rename', side_effect=denied), \
             patch.object(safe_archive.time, 'sleep') as sleep:
            with self.assertRaises(OSError):
                safe_archive.replace_tree(snapshot(), self.workspace)
        self.assertEqual(len(attempts), 6)
        self.assertEqual(sleep.call_args_list, [call(delay) for delay in DELAYS])
        marker = json.loads(self.journal.read_text())
        self.assertEqual((self.workspace / 'source.txt').read_bytes(), b'old source bytes\n')
        self.assertEqual((self.project / marker['incoming'] / 'source.txt').read_bytes(),
                         b'new source bytes\n')
        self.assertFalse((self.project / marker['previous']).exists())
        safe_archive.recover_tree(self.workspace)
        self.assert_clean()

    def test_permanent_rmdir_preserves_journal_and_runner_quarantine(self):
        token = 'b' * 32
        runner_marker = self.project / '.lab-runner.json'
        runner_marker.write_text(json.dumps({'container': token}))
        transport_calls = []
        original = os.rmdir
        attempts = []

        def denied(name, *args, **kwargs):
            if str(name).startswith('.lab-previous-'):
                attempts.append(name)
                raise OSError(errno.EBUSY, 'Persistent directory sharing violation')
            return original(name, *args, **kwargs)

        def transport(method, path, body=None, **kwargs):
            transport_calls.append((method, path))
            if method == 'GET' and path.endswith('/workspace'):
                kwargs['download'].write(snapshot().getvalue())
                kwargs['download'].seek(0)
                return None
            if method == 'GET':
                return {'Uploaded': True, 'Config': {'Labels': {
                    'codex-lab.project': self.project.name}}}
            if method == 'POST' and path.endswith('/stop'):
                return None
            self.fail('Unconfirmed recovery must not DELETE its run')

        sandbox = DockerSandbox()
        with self.force_journal(), patch.object(sandbox, 'api', side_effect=transport), \
             patch.object(safe_archive.os, 'rmdir', side_effect=denied), \
             patch.object(safe_archive.time, 'sleep') as sleep:
            with self.assertRaises(RunnerCleanupError) as caught:
                sandbox.recover(self.project)
        self.assertEqual(caught.exception.__cause__.errno, errno.EBUSY)
        self.assertEqual(len(attempts), 6)
        self.assertEqual(sleep.call_args_list, [call(delay) for delay in DELAYS])
        journal = json.loads(self.journal.read_text())
        self.assertEqual((self.workspace / 'source.txt').read_bytes(), b'new source bytes\n')
        self.assertFalse((self.project / journal['incoming']).exists())
        self.assertEqual(list((self.project / journal['previous']).iterdir()), [])
        self.assertTrue(runner_marker.exists())
        self.assertFalse(any(method == 'DELETE' for method, _ in transport_calls))
        for shared in (False, True):
            with self.subTest(shared=shared), self.assertRaisesRegex(RuntimeError, 'controller cleanup'):
                with project_lock(self.project, shared=shared):
                    self.fail('Unconfirmed workspace allowed another project writer/backup')
        safe_archive.recover_tree(self.workspace)
        self.assert_clean()
        self.assertTrue(runner_marker.exists(), 'Journal recovery must not clear runner quarantine')

    def test_retry_unlinks_old_symlink_without_following_it(self):
        (self.workspace / 'external-link').symlink_to(self.external, target_is_directory=True)
        original = os.unlink
        attempts = []

        def sharing_violation(name, *args, **kwargs):
            if name == 'external-link':
                attempts.append(name)
                self.assertIsInstance(kwargs['dir_fd'], int)
                if len(attempts) <= 2:
                    raise OSError(errno.EACCES, 'Temporary link sharing violation')
            return original(name, *args, **kwargs)

        with self.force_journal(), \
             patch.object(safe_archive.os, 'unlink', side_effect=sharing_violation), \
             patch.object(safe_archive.time, 'sleep') as sleep:
            safe_archive.replace_tree(snapshot(), self.workspace)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])
        self.assert_clean()

    def test_execute_records_cleanup_traceback_and_retains_failed_runner(self):
        token = 'b' * 32
        transport_calls = []
        original = os.rmdir
        attempts = []
        logfile = self.project / 'runner.log'

        def denied(name, *args, **kwargs):
            if str(name).startswith('.lab-previous-'):
                attempts.append(name)
                raise OSError(errno.EBUSY, 'diagnostic rmdir sharing violation')
            return original(name, *args, **kwargs)

        def transport(method, path, body=None, **kwargs):
            transport_calls.append((method, path))
            if path.startswith('/runs/by-job/'):
                raise RunnerAPIError(404, 'Runner GET by-job: 404: no prior run')
            if method == 'POST' and path == '/runs':
                return {'Id': token}
            if method == 'GET' and path.endswith('/workspace'):
                kwargs['download'].write(snapshot().getvalue())
                kwargs['download'].seek(0)
                return None
            if method == 'GET' and path.endswith('/logs'):
                return b'synthetic command completed successfully\n'
            if method == 'GET' and path == '/runs/' + token:
                return {'Uploaded': True, 'State': {'Running': False, 'ExitCode': 0}}
            if method == 'PUT' or (method == 'POST' and path.endswith(('/start', '/stop'))):
                return None
            self.fail('Unexpected API call after failed cleanup: ' + method + ' ' + path)

        sandbox = DockerSandbox()
        with self.force_journal(), patch.object(sandbox, 'api', side_effect=transport), \
             patch.object(sandbox, 'config', return_value={}), \
             patch.object(safe_archive.os, 'rmdir', side_effect=denied), \
             patch.object(safe_archive.time, 'sleep') as sleep:
            with self.assertRaises(RunnerCleanupError) as caught:
                sandbox.execute(['synthetic'], self.workspace, logfile, 30,
                                'c' * 14, lambda: None)
        self.assertEqual(caught.exception.__cause__.errno, errno.EBUSY)
        self.assertIn('diagnostic rmdir sharing violation', str(caught.exception))
        self.assertEqual(len(attempts), 12, 'Each of the two restore attempts must be bounded')
        self.assertEqual(sleep.call_args_list, [call(delay) for delay in DELAYS] * 2)
        log = logfile.read_text()
        self.assertIn('synthetic command completed successfully', log)
        self.assertIn('[runner transport/cleanup failure]', log)
        self.assertIn('Traceback (most recent call last)', log)
        self.assertIn('diagnostic rmdir sharing violation', log)
        self.assertIn('_remove_tree', log)
        self.assertTrue(self.journal.exists())
        self.assertTrue((self.project / '.lab-runner.json').exists())
        self.assertFalse(any(method == 'DELETE' for method, _ in transport_calls))
        safe_archive.recover_tree(self.workspace)
        self.assert_clean()


if __name__ == '__main__':
    unittest.main()
