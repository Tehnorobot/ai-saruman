"""Independent client batching and policy checks; no Engine/model/game calls."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from common.safe_archive import pack_tree
from runner.policy import container_config, validate_request
from worker.sandbox import DockerSandbox, RunnerAPIError, RunnerCleanupError


@unittest.skipUnless(os.name == 'posix', 'Real safe archive descriptors require Linux/POSIX')
class WorkspaceBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = self.root / 'projects'
        self.pid, self.jid, self.token = 'a' * 14, 'b' * 14, 'c' * 32
        self.workspace = self.projects / self.pid / 'workspace'
        self.workspace.mkdir(parents=True)
        (self.workspace / 'source.txt').write_text('source remains available')
        self.output = self.root / 'evidence'
        self.output.mkdir()
        context = patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects)})
        context.start()
        self.addCleanup(context.stop)
        self.calls = []
        self.uploaded = None
        self.payload = None
        self.stop_failure = False
        self.report = {'status': 'FAIL', 'checks': [
            {'label': 'gradle_build', 'status': 'PASS', 'duration_seconds': 1, 'exit_code': 0, 'log': 'build.log'},
            {'label': 'minecraft_client_gametest', 'status': 'FAIL', 'duration_seconds': 2,
             'exit_code': 9, 'log': 'client_gametest.log'}],
            'duration_seconds': 3, 'video': None, 'gameplay_scenarios': [],
            'gameplay_coverage_note': 'Client failed; gameplay acceptance is not established.'}

    def fake_broker(self, method, path, body=None, *, raw=False, upload=None, download=None):
        self.calls.append((method, path))
        if method == 'GET' and path.startswith('/runs/by-job/'):
            raise RunnerAPIError(404, 'Runner GET: 404: absent')
        if method == 'POST' and path == '/runs':
            self.payload = body
            return {'Id': self.token}
        if method == 'PUT' and path.endswith('/workspace'):
            upload.seek(0)
            self.uploaded = upload.read()
            return None
        if method == 'POST' and path.endswith('/stop'):
            if self.stop_failure:
                raise RuntimeError('Synthetic broker stop is unconfirmed')
            return None
        if method == 'POST' and path.endswith('/start'):
            return None
        if method == 'GET' and path == '/runs/' + self.token:
            return {'Id': self.token, 'Uploaded': True,
                    'Config': {'Labels': {'codex-lab.project': self.pid}},
                    'State': {'Running': False, 'ExitCode': 1, 'OOMKilled': False}}
        if method == 'GET' and path.endswith('/logs'):
            return b'Trusted pipeline exited after a failed client phase\n'
        if method == 'GET' and path.endswith('/workspace'):
            download.write(self.uploaded)
            download.seek(0)
            return None
        if method == 'GET' and path.endswith('/output'):
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                (folder / 'verification.json').write_text(json.dumps(self.report))
                (folder / 'build.log').write_text('BUILD SUCCESSFUL\n')
                (folder / 'client_gametest.log').write_text('Client assertion failed\n')
                pack_tree(folder, download)
            download.seek(0)
            return None
        if method == 'DELETE' and path == '/runs/' + self.token:
            return None
        self.fail('Unexpected broker operation: ' + method + ' ' + path)

    def test_batch_transfers_workspace_once_and_preserves_failed_phase_evidence(self):
        sandbox = DockerSandbox()
        with patch.object(sandbox, 'api', side_effect=self.fake_broker):
            report = sandbox.verify(self.workspace, self.output, 120, self.jid, lambda: None, eula=False)
        self.assertTrue(self.payload['verification'])
        self.assertFalse(self.payload['eula'])
        self.assertNotIn('argv', self.payload)
        for operation in (('POST', '/runs'), ('PUT', '/runs/' + self.token + '/workspace'),
                          ('POST', '/runs/' + self.token + '/start'),
                          ('GET', '/runs/' + self.token + '/workspace'),
                          ('GET', '/runs/' + self.token + '/output')):
            self.assertEqual(self.calls.count(operation), 1, self.calls)
        self.assertEqual(report['status'], 'FAIL', 'Batching must not hide a failed phase')
        self.assertTrue(any(c['status'] == 'FAIL' for c in report['checks']))
        self.assertEqual((self.output / 'build.log').read_text(), 'BUILD SUCCESSFUL\n')
        self.assertIn('failed', (self.output / 'client_gametest.log').read_text().lower())
        self.assertEqual((self.workspace / 'source.txt').read_text(), 'source remains available')
        self.assertFalse((self.workspace.parent / '.lab-runner.json').exists())
        self.assertIsInstance(report['timings'], dict)
        self.assertIsInstance(report['metrics'], dict)

    def test_batch_unconfirmed_cleanup_quarantines_project(self):
        sandbox = DockerSandbox()
        self.stop_failure = True
        with patch.object(sandbox, 'api', side_effect=self.fake_broker):
            with self.assertRaises(RunnerCleanupError):
                sandbox.verify(self.workspace, self.output, 120, self.jid, lambda: None)
        self.assertTrue((self.workspace.parent / '.lab-runner.json').exists())
        self.assertNotIn(('DELETE', '/runs/' + self.token), self.calls)

    def test_batch_filters_only_top_level_private_cache_and_build_paths(self):
        for name in ('.lab-cache', '.gradle', 'build'):
            folder = self.workspace / name
            folder.mkdir()
            (folder / 'private.dat').write_bytes(b'cache data must never transfer' * 1000)
        nested_source = self.workspace / 'src' / 'build' / 'Generated.java'
        nested_source.parent.mkdir(parents=True)
        nested_source.write_text('class Generated {}')
        sandbox = DockerSandbox()
        with patch.object(sandbox, 'api', side_effect=self.fake_broker):
            sandbox.verify(self.workspace, self.output, 120, self.jid, lambda: None)
        with tarfile.open(fileobj=io.BytesIO(self.uploaded), mode='r:*') as snapshot:
            names = [member.name for member in snapshot]
        for name in ('.lab-cache', '.gradle', 'build'):
            self.assertFalse(any(member == name or member.startswith(name + '/') for member in names), names)
        self.assertIn('src/build/Generated.java', names)
        self.assertEqual((self.workspace / 'src/build/Generated.java').read_text(), 'class Generated {}')

    def test_filtered_private_directory_symlink_is_not_followed_or_exported(self):
        if os.name != 'posix':
            self.skipTest('Linux real symlink boundary')
        outside = self.root / 'private-control'
        outside.mkdir()
        sentinel = outside / 'control.json'
        sentinel.write_text('unmodified broker control sentinel')
        (self.workspace / '.gradle').symlink_to(outside, target_is_directory=True)
        sandbox = DockerSandbox()
        with patch.object(sandbox, 'api', side_effect=self.fake_broker):
            sandbox.verify(self.workspace, self.output, 120, self.jid, lambda: None)
        self.assertEqual(sentinel.read_text(), 'unmodified broker control sentinel')
        with tarfile.open(fileobj=io.BytesIO(self.uploaded), mode='r:*') as snapshot:
            names = [member.name for member in snapshot]
        self.assertFalse(any(name.startswith('.gradle') for name in names), names)
        self.assertFalse((self.workspace / '.gradle').exists())

    def test_workspace_symlink_escape_rejected_before_broker_create(self):
        if os.name != 'posix':
            self.skipTest('Linux real symlink boundary')
        outside = self.root / 'outside'
        outside.mkdir()
        secret = outside / 'sentinel.txt'
        secret.write_text('outside remains untouched')
        (self.workspace / 'escape').symlink_to(outside, target_is_directory=True)
        sandbox = DockerSandbox()
        with patch.object(sandbox, 'api', side_effect=self.fake_broker):
            with self.assertRaises((ValueError, RuntimeError, OSError)):
                sandbox.verify(self.workspace, self.output, 120, self.jid, lambda: None)
        self.assertNotIn(('POST', '/runs'), self.calls)
        self.assertEqual(secret.read_text(), 'outside remains untouched')


class BatchContainerPolicyTests(unittest.TestCase):
    def batch(self, pid='a' * 14):
        return {'project': pid, 'job': 'b' * 14, 'verification': True,
                'timeout_seconds': 120, 'eula': False}

    def config(self, payload):
        return container_config('sha256:trusted-image', 'private-runner-state', 'c' * 32, payload, 'd' * 32)

    def test_batch_rejects_untrusted_docker_and_command_configuration(self):
        for key, value in (('image', 'untrusted'), ('mounts', []), ('env', {'AWS_SECRET_ACCESS_KEY': 'synthetic'}),
                           ('privileged', True), ('HostConfig', {}), ('argv', ['sh']),
                           ('record', True), ('ci', 'false')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_request({**self.batch(), key: value})
        for timeout in (-1, 0, 59, True, '120'):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                validate_request({**self.batch(), 'timeout_seconds': timeout})

    def test_batch_and_command_have_only_private_volume_subpaths(self):
        batch = self.config(self.batch())
        self.assertEqual(batch['User'], '0:0')
        host = batch['HostConfig']
        self.assertEqual(set(host['CapDrop']), {'ALL'})
        self.assertEqual(set(host['CapAdd']), {'SETUID', 'SETGID'})
        self.assertTrue(host['ReadonlyRootfs'])
        self.assertIn('no-new-privileges:true', host['SecurityOpt'])
        mounts = host['Mounts']
        self.assertEqual({m['Target'] for m in mounts}, {'/workspace', '/output', '/cache'})
        self.assertTrue(all(m['Type'] == 'volume' and m['Source'] == 'private-runner-state' for m in mounts))
        self.assertFalse(host.get('Binds'))
        self.assertFalse(any('sock' in m['Target'] or m['Target'] == '/state' for m in mounts))
        command = self.config({'project': 'a' * 14, 'job': 'b' * 14,
                               'argv': ['python3', '-c', 'pass'], 'record': False})
        self.assertEqual(command['User'], '1000:1000')
        self.assertFalse(command['HostConfig'].get('CapAdd'))

    def test_cache_is_shared_only_between_runs_of_same_project(self):
        configs = [self.config(self.batch(pid)) for pid in ('a' * 14, 'e' * 14)]
        cache = [next(m for m in cfg['HostConfig']['Mounts'] if m['Target'] == '/cache') for cfg in configs]
        self.assertNotEqual(cache[0]['VolumeOptions']['Subpath'], cache[1]['VolumeOptions']['Subpath'])
        for pid, mount in zip(('a' * 14, 'e' * 14), cache):
            self.assertEqual(mount['VolumeOptions']['Subpath'], 'projects/' + pid + '/cache')
        environment = dict(value.split('=', 1) for value in configs[0]['Env'])
        self.assertTrue(environment['GRADLE_USER_HOME'].startswith('/cache/'))
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', environment)
        self.assertNotIn('OPENAI_API_KEY', environment)


class ArtifactResponseSecurityTests(unittest.TestCase):
    def test_mp4_range_keeps_exact_bytes_and_allows_native_media_document(self):
        from fastapi.testclient import TestClient
        from app import main
        from common import db
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(db, 'DB', root / 'lab.sqlite'), \
                 patch.object(main, 'ARTIFACTS', root / 'artifacts'), \
                 patch.dict(os.environ, {'LAB_PROJECTS': str(root / 'projects')}):
                pid, jid = db.create_project('Media policy', 'spec.md', b'# Temporary isolated media policy verification')
                folder = root / 'artifacts' / pid / jid
                folder.mkdir(parents=True)
                content = bytes(range(256)) * 8
                (folder / 'capture.mp4').write_bytes(content)
                client = TestClient(main.app)
                route = '/api/jobs/' + jid + '/files/capture.mp4'
                response = client.get(route, headers={'Range': 'bytes=100-199'})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, content[100:200])
                self.assertEqual(response.headers['content-range'], 'bytes 100-199/2048')
                self.assertEqual(response.headers['content-type'], 'video/mp4')
                self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                self.assertEqual(response.headers['cache-control'], 'no-cache')
                policy = response.headers['content-security-policy']
                self.assertIn("default-src 'none'", policy)
                self.assertIn("media-src 'self'", policy)
                self.assertIn("frame-ancestors 'none'", policy)
                self.assertNotIn('sandbox', policy)
                self.assertNotIn('allow-scripts', policy)
                self.assertEqual(client.get(route).content, content)
                self.assertEqual(client.get(route, headers={'Range': 'bytes=-20'}).content, content[-20:])
                self.assertEqual(client.get(route, headers={'Range': 'bytes=3000-4000'}).status_code, 416)
                (folder / 'unsafe.exe').write_bytes(content)
                self.assertEqual(client.get('/api/jobs/' + jid + '/files/unsafe.exe').status_code, 403)

    def test_project_html_xml_downloads_cannot_execute_as_dashboard_origin(self):
        from fastapi.testclient import TestClient
        from app import main
        from common import db
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(db, 'DB', root / 'lab.sqlite'), \
                 patch.object(main, 'ARTIFACTS', root / 'artifacts'), \
                 patch.dict(os.environ, {'LAB_PROJECTS': str(root / 'projects')}):
                pid, jid = db.create_project('Untrusted artifact', 'spec.md', b'# A temporary isolated specification')
                folder = root / 'artifacts' / pid / jid
                folder.mkdir(parents=True)
                client = TestClient(main.app)
                for extension in ('html', 'xml', 'log'):
                    with self.subTest(extension=extension):
                        artifact = folder / ('project-report.' + extension)
                        artifact.write_text('<script>fetch("/api/jobs",{method:"POST"})</script>')
                        response = client.get('/api/jobs/' + jid + '/files/' + artifact.name)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers.get('x-content-type-options'), 'nosniff')
                        policy = response.headers.get('content-security-policy', '').lower()
                        self.assertIn('sandbox', policy)
                        self.assertNotIn('allow-scripts', policy)
                        if extension in ('html', 'xml'):
                            self.assertIn('attachment', response.headers.get('content-disposition', '').lower())


if __name__ == '__main__':
    unittest.main()
