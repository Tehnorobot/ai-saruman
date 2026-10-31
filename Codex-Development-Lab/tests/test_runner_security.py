"""Independent boundaries for the runner broker and its untrusted file transfer."""
import errno
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from common import safe_archive
from common.safe_archive import pack_tree, replace_tree, unpack_tree
from runner.policy import container_config, validate_request


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as result:
        for name, kind, value in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
            if kind == tarfile.REGTYPE:
                data = value.encode()
                item.size = len(data)
                result.addfile(item, io.BytesIO(data))
            else:
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    item.linkname = value
                result.addfile(item)
    stream.seek(0)
    return stream


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'),
                     'Secure descriptor traversal requires Linux/POSIX')
class ArchiveSecurity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.external = self.base / 'outside'
        self.external.mkdir()
        self.sentinel = self.external / 'sentinel.txt'
        self.sentinel.write_text('untouched')

    def assert_rejected(self, entries):
        with self.assertRaises((ValueError, OSError, RuntimeError, tarfile.TarError)):
            unpack_tree(archive(entries), self.root)
        self.assertEqual(self.sentinel.read_text(), 'untouched')

    def test_absolute_and_parent_paths_cannot_escape(self):
        for name in ('../outside/sentinel.txt', '/outside/sentinel.txt',
                     'nested/../../outside/sentinel.txt'):
            with self.subTest(name=name):
                self.assert_rejected([(name, tarfile.REGTYPE, 'overwritten')])

    def test_links_cannot_escape_or_redirect_later_members(self):
        self.assert_rejected([
            ('escape', tarfile.SYMTYPE, '../outside'),
            ('escape/sentinel.txt', tarfile.REGTYPE, 'overwritten'),
        ])

    def test_absolute_symlink_is_rejected(self):
        self.assert_rejected([('escape', tarfile.SYMTYPE, str(self.external))])

    def test_chained_symlinks_cannot_escape(self):
        self.assert_rejected([
            ('first', tarfile.SYMTYPE, 'nested/second'),
            ('nested', tarfile.DIRTYPE, ''),
            ('nested/second', tarfile.SYMTYPE, '../../outside'),
        ])

    def test_hardlinks_and_special_files_are_rejected(self):
        for kind in (tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            with self.subTest(kind=kind):
                self.assert_rejected([('unsafe', kind, '../outside/sentinel.txt')])

    def test_safe_npm_symlink_and_executable_survive_round_trip(self):
        executable = self.root / 'node_modules' / 'tool' / 'bin.js'
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/usr/bin/env node\n')
        executable.chmod(0o755)
        link = self.root / 'node_modules' / '.bin' / 'tool'
        link.parent.mkdir()
        link.symlink_to('../tool/bin.js')
        stream = io.BytesIO()
        pack_tree(self.root, stream)
        stream.seek(0)
        target = self.base / 'copy'
        target.mkdir()
        unpack_tree(stream, target)
        copied = target / executable.relative_to(self.root)
        self.assertEqual(copied.read_text(), executable.read_text())
        self.assertTrue(copied.stat().st_mode & 0o111)
        self.assertEqual(os.readlink(target / link.relative_to(self.root)), '../tool/bin.js')

    def test_pack_does_not_follow_external_symlink(self):
        (self.root / 'escape').symlink_to(self.external, target_is_directory=True)
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            pack_tree(self.root, io.BytesIO())
        self.assertEqual(self.sentinel.read_text(), 'untouched')

    def test_unpack_rejects_symlink_destination_root(self):
        empty_external = self.external / 'empty'
        empty_external.mkdir()
        link = self.base / 'destination'
        link.symlink_to(empty_external, target_is_directory=True)
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            unpack_tree(archive([('written.txt', tarfile.REGTYPE, 'escaped')]), link)
        self.assertFalse((empty_external / 'written.txt').exists())
        self.assertEqual(self.sentinel.read_text(), 'untouched')

    def test_malicious_replace_preserves_existing_workspace(self):
        source = self.root / 'source.txt'
        source.write_text('original source')
        with self.assertRaises((ValueError, OSError, RuntimeError, tarfile.TarError)):
            replace_tree(archive([('../outside/sentinel.txt', tarfile.REGTYPE,
                                  'overwritten')]), self.root)
        self.assertEqual(source.read_text(), 'original source')
        self.assertEqual(self.sentinel.read_text(), 'untouched')

    def test_replace_removes_stale_files_and_preserves_new_build_output(self):
        (self.root / 'stale.txt').write_text('stale')
        (self.root / 'build').symlink_to(self.external, target_is_directory=True)
        replace_tree(archive([
            ('source.txt', tarfile.REGTYPE, 'new source'),
            ('build/libs/result.jar', tarfile.REGTYPE, 'synthetic artifact'),
        ]), self.root)
        self.assertFalse((self.root / 'stale.txt').exists())
        self.assertFalse((self.root / 'build').is_symlink())
        self.assertEqual((self.root / 'build/libs/result.jar').read_text(), 'synthetic artifact')
        self.assertEqual(self.sentinel.read_text(), 'untouched')

    def test_unsupported_exchange_uses_recoverable_replacement(self):
        (self.root / 'source.txt').write_text('original source')
        unsupported = OSError(errno.EINVAL, 'Filesystem does not support exchange')
        with patch.object(safe_archive, '_exchange', side_effect=unsupported):
            replace_tree(archive([
                ('source.txt', tarfile.REGTYPE, 'updated source'),
                ('build/libs/result.jar', tarfile.REGTYPE, 'new build result'),
            ]), self.root)
        self.assertEqual((self.root / 'source.txt').read_text(), 'updated source')
        self.assertEqual((self.root / 'build/libs/result.jar').read_text(), 'new build result')
        self.assertFalse((self.base / '.lab-workspace-swap.json').exists())
        self.assertFalse(list(self.base.glob('.lab-incoming-*')))
        self.assertFalse(list(self.base.glob('.lab-previous-*')))

    def crash_replacement_after_fsync(self, stage):
        """Kill a real child, bypassing Python cleanup, at a durable rename."""
        (self.root / 'source.txt').write_text('original source')
        pid = os.fork()
        if pid == 0:
            real_rename = os.rename
            real_fsync = os.fsync
            completed = 0

            def rename(source, destination, *args, **kwargs):
                nonlocal completed
                result = real_rename(source, destination, *args, **kwargs)
                if source == 'workspace' or destination == 'workspace':
                    completed += 1
                return result

            def fsync(descriptor):
                result = real_fsync(descriptor)
                if completed == stage:
                    os._exit(73)
                return result

            try:
                with patch.object(safe_archive, '_exchange',
                                  side_effect=OSError(errno.EINVAL, 'Unsupported exchange')), \
                     patch.object(safe_archive.os, 'rename', side_effect=rename), \
                     patch.object(safe_archive.os, 'fsync', side_effect=fsync):
                    replace_tree(archive([
                        ('source.txt', tarfile.REGTYPE, 'updated source'),
                        ('build/libs/result.jar', tarfile.REGTYPE, 'new build result'),
                    ]), self.root)
            except BaseException:
                os._exit(74)
            os._exit(75)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 73, 'Child did not reach durable rename crash point')
        marker = self.base / '.lab-workspace-swap.json'
        self.assertTrue(marker.is_file())
        safe_archive.recover_tree(self.root)
        self.assertEqual((self.root / 'source.txt').read_text(), 'updated source')
        self.assertEqual((self.root / 'build/libs/result.jar').read_text(), 'new build result')
        self.assertEqual(self.sentinel.read_text(), 'untouched')
        self.assertFalse(marker.exists())
        self.assertFalse(list(self.base.glob('.lab-incoming-*')))
        self.assertFalse(list(self.base.glob('.lab-previous-*')))
        # Recovery remains safe and idempotent after its journal is gone.
        safe_archive.recover_tree(self.root)

    def test_restart_after_first_durable_rename_keeps_new_result(self):
        self.crash_replacement_after_fsync(1)

    def test_restart_after_second_durable_rename_keeps_new_result(self):
        self.crash_replacement_after_fsync(2)

    def test_recovery_restores_original_source_when_staged_tree_is_missing(self):
        token = 'a' * 32
        (self.root / 'source.txt').write_text('original source')
        previous = self.base / ('.lab-previous-' + token)
        self.root.rename(previous)
        marker = self.base / '.lab-workspace-swap.json'
        marker.write_text(json.dumps({'version': 1, 'workspace': 'workspace',
                                      'incoming': '.lab-incoming-' + token,
                                      'previous': previous.name}))
        safe_archive.recover_tree(self.root)
        self.assertEqual((self.root / 'source.txt').read_text(), 'original source')
        self.assertFalse(previous.exists())
        self.assertFalse(marker.exists())
        self.assertEqual(self.sentinel.read_text(), 'untouched')

    def test_recovery_rejects_journal_paths_without_deleting_arbitrary_trees(self):
        original = self.root / 'source.txt'
        original.write_text('original source')
        token = 'a' * 32
        valid = {'version': 1, 'workspace': 'workspace',
                 'incoming': '.lab-incoming-' + token,
                 'previous': '.lab-previous-' + token}
        marker = self.base / '.lab-workspace-swap.json'
        for changed in ({'incoming': 'outside'}, {'previous': '../outside'},
                        {'workspace': 'outside'},
                        {'previous': '.lab-previous-' + 'b' * 32},
                        {'extra': 'outside'}):
            with self.subTest(changed=changed):
                marker.write_text(json.dumps({**valid, **changed}))
                with self.assertRaises((ValueError, OSError, RuntimeError)):
                    safe_archive.recover_tree(self.root)
                self.assertEqual(self.sentinel.read_text(), 'untouched')
                self.assertEqual(original.read_text(), 'original source')
                self.assertTrue(marker.exists())

    def test_recovery_rejects_symlink_journal_without_applying_valid_contents(self):
        token = 'a' * 32
        incoming = self.base / ('.lab-incoming-' + token)
        incoming.mkdir()
        (incoming / 'source.txt').write_text('new source')
        original = self.root / 'source.txt'
        original.write_text('original source')
        data = json.dumps({'version': 1, 'workspace': 'workspace',
                           'incoming': incoming.name, 'previous': '.lab-previous-' + token})
        external_journal = self.external / 'valid-journal.json'
        external_journal.write_text(data)
        marker = self.base / '.lab-workspace-swap.json'
        marker.symlink_to(external_journal)
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            safe_archive.recover_tree(self.root)
        self.assertEqual(original.read_text(), 'original source')
        self.assertEqual((incoming / 'source.txt').read_text(), 'new source')
        self.assertEqual(external_journal.read_text(), data)
        self.assertTrue(marker.is_symlink())


class BrokerPolicySecurity(unittest.TestCase):
    def request(self):
        return {'project': 'a' * 14, 'job': 'b' * 14,
                'argv': ['sh', '-c', 'printf synthetic'], 'record': False}

    def test_only_high_level_fields_are_accepted(self):
        for field, value in (
                ('Image', 'attacker/image'), ('HostConfig', {'Privileged': True}),
                ('mounts', [{'Source': '/', 'Target': '/host'}]),
                ('container', 'unrelated-container'), ('workspace', '/other-project')):
            with self.subTest(field=field):
                request = {**self.request(), field: value}
                with self.assertRaises((ValueError, TypeError)):
                    validate_request(request)

    def test_project_and_job_are_identifiers_not_paths(self):
        for field in ('project', 'job'):
            for value in ('../outside', '/host', 'a' * 13, 'A' * 14, 'a' * 14 + '/x'):
                with self.subTest(field=field, value=value):
                    request = {**self.request(), field: value}
                    with self.assertRaises((ValueError, TypeError)):
                        validate_request(request)

    def test_malformed_command_and_record_are_rejected(self):
        for fields in ({'argv': 'sh'}, {'argv': []}, {'argv': ['sh', 3]},
                       {'argv': ['sh\0bad']}, {'record': 'false'}, {'record': 1}):
            with self.subTest(fields=fields):
                with self.assertRaises((ValueError, TypeError)):
                    validate_request({**self.request(), **fields})

    def test_run_config_cannot_mount_host_or_control_socket(self):
        request = validate_request(self.request())
        image = 'sha256:' + 'c' * 64
        volume = 'private-runner-staging'
        token = 'd' * 32
        config = container_config(image, volume, token, request, 'e' * 32)
        self.assertEqual(config['Image'], image)
        self.assertEqual(config['User'], '1000:1000')
        host = config['HostConfig']
        self.assertTrue(host['ReadonlyRootfs'])
        self.assertIn('ALL', host['CapDrop'])
        self.assertIn('no-new-privileges:true', host['SecurityOpt'])
        self.assertEqual(host['NetworkMode'], 'codex-development-lab_test_internal')
        self.assertFalse(host.get('Privileged', False))
        self.assertFalse(host.get('Binds'))
        self.assertGreater(host['Memory'], 0)
        self.assertGreater(host['NanoCpus'], 0)
        self.assertGreater(host['PidsLimit'], 0)
        mounts = host['Mounts']
        self.assertEqual({mount['Target'] for mount in mounts}, {'/workspace', '/output', '/cache'})
        for mount in mounts:
            self.assertEqual(mount['Type'], 'volume')
            self.assertEqual(mount['Source'], volume)
            subpath = mount['VolumeOptions']['Subpath']
            if mount['Target'] == '/cache':
                self.assertEqual(subpath, 'projects/' + request['project'] + '/cache')
            else:
                self.assertIn(token, subpath.split('/'))
            self.assertNotIn('..', subpath.split('/'))
        environment = '\n'.join(config.get('Env', []))
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', environment)
        self.assertNotIn('AGENT_TASKS', environment)
        self.assertNotIn('DOCKER_HOST', environment)


if __name__ == '__main__':
    unittest.main()
