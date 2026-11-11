"""Protected template storage policy; no Docker Engine or Minecraft is invoked."""
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

from runner.engine import EngineHTTPError
from runner.policy import container_config, validate_request
if os.name == 'posix':
    from runner.broker import Broker


class WorldCachePolicyTests(unittest.TestCase):
    def request(self, project='a' * 14):
        return {'project': project, 'job': 'b' * 14, 'verification': True,
                'timeout_seconds': 900, 'eula': False}

    def config(self, request):
        return container_config('sha256:' + 'f' * 64, 'private-runner-state',
                                'c' * 32, request, 'd' * 32)

    def test_verification_gets_only_fixed_global_volume_subpath(self):
        first = self.config(self.request())
        second = self.config(self.request(project='e' * 14))
        mounts = {row['Target']: row for row in first['HostConfig']['Mounts']}
        self.assertEqual(set(mounts), {'/workspace', '/output', '/cache', '/world-templates','/game-data'})
        world = mounts['/world-templates']
        self.assertEqual(world, {'Type': 'volume', 'Source': 'private-runner-state',
                                'Target': '/world-templates',
                                'VolumeOptions': {'NoCopy': True, 'Subpath': 'world-templates'}})
        other = next(row for row in second['HostConfig']['Mounts']
                     if row['Target'] == '/world-templates')
        self.assertEqual(world, other)
        game=mounts['/game-data']
        self.assertEqual(game['VolumeOptions']['Subpath'],'runs/'+('c'*32)+'/game-data')
        distinct=container_config('sha256:'+'f'*64,'private-runner-state','e'*32,self.request(),'d'*32)
        other_game=next(row for row in distinct['HostConfig']['Mounts'] if row['Target']=='/game-data')
        self.assertNotEqual(game['VolumeOptions']['Subpath'],other_game['VolumeOptions']['Subpath'])
        self.assertFalse(first['HostConfig'].get('Binds'))
        self.assertEqual(first['User'], '0:0')
        self.assertEqual(set(first['HostConfig']['CapAdd']), {'SETUID', 'SETGID'})
        self.assertIn('ALL', first['HostConfig']['CapDrop'])
        self.assertTrue(first['HostConfig']['ReadonlyRootfs'])

    def test_untrusted_commands_have_no_template_mount_or_extra_capabilities(self):
        request = {'project': 'a' * 14, 'job': 'b' * 14,
                   'argv': ['gradle', 'build'], 'record': False}
        config = self.config(request)
        self.assertEqual({row['Target'] for row in config['HostConfig']['Mounts']},
                         {'/workspace', '/output', '/cache'})
        self.assertEqual(config['User'], '1000:1000')
        self.assertFalse(config['HostConfig'].get('CapAdd'))

    def test_request_cannot_choose_template_paths_mounts_or_images(self):
        for field, value in [('world_cache', '/tmp/attacker'), ('world-templates', {}),
                             ('templates', '../projects'), ('mounts', []),
                             ('Image', 'attacker-image'), ('env', {'LAB_WORLD_ROOT': '/workspace'})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_request({**self.request(), field: value})


class FakeEngine:
    def __init__(self):
        self.created = []

    def api(self, method, path, body=None, **kwargs):
        if method == 'GET' and path.startswith('/networks/'):
            return {'Id': '9' * 64, 'Internal': True, 'Driver': 'bridge', 'Options': {
                'com.docker.network.bridge.gateway_mode_ipv4': 'isolated',
                'com.docker.network.bridge.gateway_mode_ipv6': 'isolated'}}
        if method == 'GET' and path.startswith('/images/'):
            return {'Id': 'sha256:' + 'f' * 64}
        if method == 'GET' and path.startswith('/containers/json?'):
            return []
        if method == 'GET' and path.endswith('/json') and '/codex-lab-run-' not in path:
            return {'Mounts': [{'Destination': '/state', 'Type': 'volume',
                                'Name': 'private-runner-state'}]}
        if method == 'GET' and path.startswith('/containers/'):
            raise EngineHTTPError(404, 'absent')
        if method == 'POST' and path.startswith('/containers/create?'):
            self.created.append(body)
            return {'Id': '8' * 64}
        if method == 'POST' and path.endswith('/start'):
            return None
        raise AssertionError((method, path))


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Actual protected volume ownership requires isolated Linux root')
class WorldCacheFilesystemTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.engine = FakeEngine()
        self.broker = Broker(self.folder / 'state', self.engine)

    def batch(self, project='a' * 14, job='b' * 14):
        return {'project': project, 'job': job, 'verification': True,
                'timeout_seconds': 900, 'eula': False}

    def pending(self, request=None):
        token = self.broker.create(request or self.batch())['Id']
        record = self.broker.load(token)
        record['uploaded'] = True
        self.broker.save(token, record)
        return token

    def test_cache_is_root_owned_private_sibling_of_all_project_caches(self):
        self.broker.root.chmod(0o755)  # A fresh Docker volume may have this mode.
        self.pending()
        cache = self.broker.root / 'world-templates'
        for path in (self.broker.root, cache):
            info = path.stat()
            self.assertEqual(info.st_uid, 0)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
        project_cache = self.broker.root / 'projects' / ('a' * 14) / 'cache'
        self.assertEqual(project_cache.stat().st_uid, 1000)
        self.assertFalse(cache.is_relative_to(project_cache))

    def test_each_verification_has_private_root_owned_traversable_game_data(self):
        first=self.pending()
        second=self.pending(self.batch(project='e'*14,job='f'*14))
        for token in (first,second):
            game=self.broker.directory(token)/'game-data'
            self.assertEqual(game.stat().st_uid,0)
            self.assertEqual(stat.S_IMODE(game.stat().st_mode),0o755)
        self.assertNotEqual(self.broker.directory(first)/'game-data',self.broker.directory(second)/'game-data')

    def test_untrusted_command_creation_does_not_prepare_world_cache(self):
        self.broker.create({'project': 'a' * 14, 'job': 'b' * 14,
                            'argv': ['gradle', 'build'], 'record': False})
        self.assertFalse((self.broker.root / 'world-templates').exists())

    def test_template_cache_survives_run_deletion_and_is_shared_without_project_access(self):
        first = self.pending()
        cache = self.broker.root / 'world-templates'
        (cache / 'template-manifest.json').write_text('{"trusted":true}')
        self.broker.delete(first)
        second = self.pending(self.batch(project='e' * 14, job='f' * 14))
        self.assertNotEqual(first, second)
        self.assertEqual((cache / 'template-manifest.json').read_text(), '{"trusted":true}')
        self.broker.start(second)
        mount = next(row for row in self.engine.created[-1]['HostConfig']['Mounts']
                     if row['Target'] == '/world-templates')
        self.assertEqual(mount['VolumeOptions']['Subpath'], 'world-templates')

    def test_parent_and_leaf_symlinks_are_rejected_without_touching_the_target(self):
        for replace_parent in (False, True):
            with self.subTest(replace_parent=replace_parent), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                broker = Broker(folder / 'state', FakeEngine())
                broker.prepare_world_cache()
                outside = folder / 'outside'
                outside.mkdir(mode=0o755)
                sentinel = outside / 'sentinel'
                sentinel.write_text('unchanged')
                before = (outside.stat().st_mode, sentinel.read_bytes())
                target = broker.root if replace_parent else broker.root / 'world-templates'
                shutil.rmtree(target)
                target.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(OSError):
                    broker.prepare_world_cache()
                self.assertEqual((outside.stat().st_mode, sentinel.read_bytes()), before)

    def test_nonroot_parent_or_leaf_is_not_adopted_or_repaired(self):
        for leaf in (False, True):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as temporary:
                broker = Broker(Path(temporary) / 'state', FakeEngine())
                cache = broker.prepare_world_cache()
                target = cache if leaf else broker.root
                os.chown(target, 1000, 1000)
                before = target.stat()
                with self.assertRaisesRegex(RuntimeError, 'root|ownership'):
                    broker.prepare_world_cache()
                after = target.stat()
                self.assertEqual((after.st_uid, after.st_gid, after.st_mode),
                                 (before.st_uid, before.st_gid, before.st_mode))

    def test_untrusted_ancestor_is_rejected_before_creating_the_cache(self):
        for wrong_owner in (False, True):
            with self.subTest(wrong_owner=wrong_owner), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                ancestor = folder / 'untrusted'
                ancestor.mkdir()
                broker = Broker(ancestor / 'state', FakeEngine())
                if wrong_owner:
                    os.chown(ancestor, 1000, 1000)
                else:
                    ancestor.chmod(0o777)
                with self.assertRaisesRegex(RuntimeError, 'ancestor'):
                    broker.prepare_world_cache()
                self.assertFalse((broker.root / 'world-templates').exists())

    def test_existing_cache_permissions_are_not_silently_accepted(self):
        cache = self.broker.prepare_world_cache()
        cache.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, 'permissions'):
            self.broker.prepare_world_cache()
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o755)

    def test_start_revalidates_cache_after_creation_before_asking_engine(self):
        token = self.pending()
        cache = self.broker.root / 'world-templates'
        cache.rmdir()
        outside = self.folder / 'outside'
        outside.mkdir()
        cache.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.broker.start(token)
        self.assertEqual(self.engine.created, [])
        self.assertIsNone(self.broker.load(token)['started'])

    def test_unsafe_cache_prevents_run_creation_without_leaving_pending_run(self):
        cache = self.broker.prepare_world_cache()
        cache.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'permissions'):
            self.broker.create(self.batch())
        self.assertEqual(list((self.broker.root / 'runs').iterdir()), [])

    def test_prepare_never_traverses_template_contents(self):
        cache = self.broker.prepare_world_cache()
        outside = self.folder / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_text('unchanged')
        (cache / 'malicious-content').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.broker.prepare_world_cache(), cache)
        self.assertEqual(sentinel.read_text(), 'unchanged')


if __name__ == '__main__':
    unittest.main()
