"""Backup consistency and S3 destination changes, without external services."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backup import daemon as backup
from common import db
from common.project_lock import project_lock
from storage import s3_cli as s3
from storage import api
from fastapi import HTTPException


class BackupRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.projects = self.root / 'projects'
        for target, attr, value in ((db, 'DB', self.root / 'lab.sqlite3'),
                                    (backup, 'PROJECTS', self.projects),
                                    (backup, 'ARTIFACTS', self.root / 'artifacts'),
                                    (backup, 'OUTPUT', self.root / 'backups')):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects)})
        p.start()
        self.addCleanup(p.stop)
        self.pid, self.jid = db.create_project('Snapshot', 'spec.md', b'# Snapshot regression specification', 1)
        self.project = self.projects / self.pid

    def test_manifest_hashes_exact_archived_bytes(self):
        source = self.project / 'workspace' / 'module.py'
        source.write_bytes(b'version = 1\n')
        cache = self.project / 'workspace' / '.lab-cache' / 'gradle'
        cache.mkdir(parents=True)
        (cache / 'cache.txt').write_text('not project sources')
        original_addfile = tarfile.TarFile.addfile

        def mutate_after_read(tar, info, fileobj=None):
            if info.name == 'project/workspace/module.py':
                source.write_bytes(b'version = 2\n')
            return original_addfile(tar, info, fileobj)

        with patch.object(tarfile.TarFile, 'addfile', mutate_after_read):
            result = backup.create_backup(self.jid, 'race')
        with tarfile.open(result) as archive:
            manifest = json.load(archive.extractfile('backup_manifest.json'))
            for name, digest in manifest['files'].items():
                self.assertEqual(digest, hashlib.sha256(archive.extractfile(name).read()).hexdigest(), name)
            self.assertEqual(archive.extractfile('project/workspace/module.py').read(), b'version = 1\n')
            self.assertNotIn('project/.lab.lock', archive.getnames())
            self.assertNotIn('project/workspace/.lab-cache/gradle/cache.txt', archive.getnames())
        self.assertEqual(source.read_bytes(), b'version = 2\n')

    def test_snapshot_waits_for_project_writer(self):
        first = self.project / 'workspace' / 'first.txt'
        second = self.project / 'workspace' / 'second.txt'
        first.write_text('old')
        second.write_text('old')
        attempted = threading.Event()
        completed = threading.Event()

        @contextmanager
        def observed_lock(root, **kwargs):
            attempted.set()
            with project_lock(root, **kwargs):
                yield

        def snapshot():
            try:
                return backup.create_backup(self.jid, 'writer')
            finally:
                completed.set()

        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(backup, 'project_lock', observed_lock):
            with project_lock(self.project):
                first.write_text('new')
                future = pool.submit(snapshot)
                self.assertTrue(attempted.wait(5))
                self.assertFalse(completed.wait(0.15), 'snapshot bypassed the active writer')
                second.write_text('new')
            result = future.result(timeout=10)
        with tarfile.open(result) as archive:
            self.assertEqual(archive.extractfile('project/workspace/first.txt').read(), b'new')
            self.assertEqual(archive.extractfile('project/workspace/second.txt').read(), b'new')

    def test_shared_lock_works_with_read_only_project_mount(self):
        lock_file = self.project / '.lab.lock'
        lock_file.touch()
        original_open = Path.open

        def read_only_lock_open(path, mode='r', *args, **kwargs):
            if path == lock_file and mode != 'rb':
                raise PermissionError('project mount is read-only')
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', read_only_lock_open):
            result = backup.create_backup(self.jid, 'read-only')
        self.assertTrue(result.is_file())

    @unittest.skipIf(os.name == 'nt', 'POSIX file permissions')
    def test_new_lock_is_writable_by_cooperating_service_uids(self):
        project = self.root / 'legacy-project'
        original_umask = os.umask(0o077)
        try:
            with project_lock(project):
                self.assertEqual(stat.S_IMODE((project / '.lab.lock').stat().st_mode), 0o666)
        finally:
            os.umask(original_umask)
        (project / '.lab.lock').chmod(0o640)
        with project_lock(project):
            self.assertEqual(stat.S_IMODE((project / '.lab.lock').stat().st_mode), 0o640)


class FakeS3:
    def __init__(self):
        self.uploads = []
        self.signatures = []

    def upload_file(self, path, bucket, key, **kwargs):
        self.uploads.append((bucket, key, Path(path).stat().st_size))

    def head_object(self, **kwargs):
        return {'ContentLength': self.uploads[-1][2]}

    def generate_presigned_url(self, operation, **kwargs):
        self.signatures.append(kwargs)
        return 'https://example.invalid/confirmed-object'


class StorageRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = self.root / 'runs'
        self.runs.mkdir()
        self.src = self.runs / 'clip.mp4'
        self.src.write_bytes(b'synthetic recording')
        self.state = self.root / 'state'
        self.conf = {'endpoint': 'https://old.example.invalid', 'region': 'r1',
                     'bucket': 'old-bucket', 'prefix': 'old-prefix', 'seconds': 60.0, 'presign': 900}
        self.client = FakeS3()
        p = patch.object(s3, 'probe', return_value=(40.0, 'h264'))
        p.start()
        self.addCleanup(p.stop)

        def prepare(src, duration, codec, maximum, temp):
            if duration <= maximum:
                return src, duration, False
            out = temp / 'short.mp4'
            out.write_bytes(b'synthetic shorter clip')
            return out, maximum - 1, True

        p = patch.object(s3, 'prepare_video', side_effect=prepare)
        self.prepare = p.start()
        self.addCleanup(p.stop)

    def upload(self, conf=None):
        return s3.upload_one(self.src, self.runs, self.state, conf or self.conf, self.client)

    def test_destination_and_processing_changes_require_fresh_uploads(self):
        first = self.upload()
        self.assertEqual(self.upload()['status'], 'ALREADY_UPLOADED')
        variants = ({'bucket': 'new-bucket'}, {'prefix': 'new-prefix'},
                    {'endpoint': 'https://new.example.invalid'}, {'region': 'r2'}, {'seconds': 10.0})
        for delta in variants:
            conf = {**self.conf, **delta}
            with self.subTest(delta=delta):
                result = self.upload(conf)
                self.assertEqual(result['status'], 'UPLOADED')
                self.assertEqual(self.upload(conf)['status'], 'ALREADY_UPLOADED')
                self.assertEqual(result['bucket'], conf['bucket'])
                self.assertTrue(result['key'].startswith(conf['prefix'] + '/'))
                self.assertLessEqual(result['seconds'], conf['seconds'])
                if 'seconds' in delta:
                    self.assertNotEqual(first['key'], result['key'])
        self.assertEqual(len(self.client.uploads), 1 + len(variants))
        self.assertEqual(self.prepare.call_count, 1 + len(variants))

    def test_legacy_index_is_refreshed(self):
        identity = f'clip.mp4:{s3.sha256(self.src)}'
        s3.state_save(self.state / 'upload_index.json', {'schema_version': 1, 'objects': {
            identity: {'bucket': 'old-bucket', 'key': 'old-prefix/legacy.mp4'}}})
        self.assertEqual(self.upload()['status'], 'UPLOADED')
        self.assertEqual(len(self.client.uploads), 1)

    def test_signer_rejects_object_recorded_in_another_bucket(self):
        uploaded = self.upload()
        with patch.dict(os.environ, {'STORAGE_STATE': str(self.state)}), \
                patch.object(s3, 'config', return_value={**self.conf, 'bucket': 'new-bucket'}), \
                patch.object(s3, 's3client', return_value=self.client):
            with self.assertRaises(HTTPException) as error:
                api.presign(uploaded['key'])
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.client.signatures, [])

    def test_signer_uses_confirmed_destination(self):
        uploaded = self.upload()
        with patch.dict(os.environ, {'STORAGE_STATE': str(self.state)}), \
                patch.object(s3, 'config', return_value=self.conf), \
                patch.object(s3, 's3client', return_value=self.client):
            result = api.presign(uploaded['key'])
        self.assertEqual(result['expires_in'], 900)
        self.assertEqual(self.client.signatures[0]['Params']['Bucket'], uploaded['bucket'])
        self.assertEqual(self.client.signatures[0]['Params']['Key'], uploaded['key'])

    def test_signer_rejects_prior_duration_policy_before_and_after_refresh(self):
        old = self.upload()
        shorter_conf = {**self.conf, 'seconds': 10.0}
        with patch.dict(os.environ, {'STORAGE_STATE': str(self.state)}), \
                patch.object(s3, 'config', return_value=shorter_conf), \
                patch.object(s3, 's3client', return_value=self.client):
            for refreshed in (False, True):
                if refreshed:
                    current = self.upload(shorter_conf)
                    self.assertEqual(current['status'], 'UPLOADED')
                with self.subTest(refreshed=refreshed), self.assertRaises(HTTPException) as error:
                    api.presign(old['key'])
                self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(self.client.signatures, [])
            api.presign(current['key'])
        self.assertEqual(len(self.client.signatures), 1)
        self.assertEqual(self.client.signatures[0]['Params']['Key'], current['key'])


if __name__ == '__main__':
    unittest.main()
