"""Private project cache and strict verification-launch boundaries; no Engine calls."""
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from runner.engine import EngineHTTPError
from runner.policy import RUNNER_NETWORK,container_config,validate_request
if os.name=='posix':
    from runner.broker import Broker


class FakeEngine:
    def __init__(self,network=None):
        self.created=[]
        self.network=network or {'Id':'9'*64,'Internal':True,'Driver':'bridge',
                 'Options':{'com.docker.network.bridge.gateway_mode_ipv4':'isolated',
                            'com.docker.network.bridge.gateway_mode_ipv6':'isolated'}}

    def api(self,method,path,body=None,**kwargs):
        if method=='GET' and path.startswith('/networks/'):return self.network
        if method=='GET' and path.startswith('/images/'):return {'Id':'sha256:'+'f'*64}
        if method=='GET' and path.startswith('/containers/json?'):return []
        if method=='GET' and path.endswith('/json') and not '/codex-lab-run-' in path:
            return {'Mounts':[{'Destination':'/state','Type':'volume','Name':'private-runner-state'}]}
        if method=='GET' and path.startswith('/containers/'):
            raise EngineHTTPError(404, 'Engine 404: absent')
        if method=='POST' and path.startswith('/containers/create?'):
            self.created.append(body);return {'Id':'8'*64}
        if method=='POST' and path.endswith('/start'):return None
        raise AssertionError((method,path))


class RunnerCachePolicyTests(unittest.TestCase):
    def request(self,project='a'*14):
        return {'project':project,'job':'b'*14,'argv':['gradle','test'],'record':False}

    def batch(self):
        return {'project':'a'*14,'job':'b'*14,'verification':True,'timeout_seconds':900,'eula':False}

    def config(self,request):
        return container_config('sha256:'+'f'*64,'private-runner-state','c'*32,request,'d'*32)

    def test_cache_mount_is_only_project_leaf_and_run_mounts_stay_token_bound(self):
        first=self.config(self.request())
        second=self.config(self.request('e'*14))
        mounts={item['Target']:item for item in first['HostConfig']['Mounts']}
        self.assertEqual(set(mounts),{'/workspace','/output','/cache'})
        self.assertEqual(mounts['/cache']['VolumeOptions']['Subpath'],'projects/'+('a'*14)+'/cache')
        self.assertNotEqual(mounts['/cache']['VolumeOptions']['Subpath'],
                            second['HostConfig']['Mounts'][2]['VolumeOptions']['Subpath'])
        for target in ('/workspace','/output'):
            self.assertTrue(mounts[target]['VolumeOptions']['Subpath'].startswith('runs/'+('c'*32)+'/'))
        for mount in mounts.values():
            self.assertEqual((mount['Type'],mount['Source']),('volume','private-runner-state'))
            self.assertNotIn('..',mount['VolumeOptions']['Subpath'].split('/'))
        self.assertFalse(first['HostConfig'].get('Binds'))
        self.assertEqual(first['HostConfig']['NetworkMode'],RUNNER_NETWORK)

    def test_batch_cannot_supply_commands_paths_environment_or_invalid_budget(self):
        for field,value in (('argv',['sh']),('record',True),('ci','true'),('env',{}),
                            ('cache','/state'),('network','bridge'),('Image','other'),('mounts',[])):
            with self.subTest(field=field),self.assertRaises(ValueError):
                validate_request({**self.batch(),field:value})
        for timeout in (True,0,59,'900',None):
            with self.subTest(timeout=timeout),self.assertRaises(ValueError):
                validate_request({**self.batch(),'timeout_seconds':timeout})
        for eula in (0,'true',None):
            with self.subTest(eula=eula),self.assertRaises(ValueError):
                validate_request({**self.batch(),'eula':eula})
        for timeout in (60,1800,1801,7200):
            self.assertEqual(validate_request({**self.batch(),'timeout_seconds':timeout})['timeout_seconds'],timeout)

    def test_only_trusted_batch_parent_has_uid_switch_caps_and_protected_entrypoint(self):
        command=self.config(self.request())
        batch=self.config(self.batch())
        self.assertEqual(command['User'],'1000:1000')
        self.assertFalse(command['HostConfig'].get('CapAdd'))
        self.assertEqual(batch['User'],'0:0')
        self.assertEqual(batch['HostConfig']['CapDrop'],['ALL'])
        self.assertEqual(set(batch['HostConfig']['CapAdd']),{'SETUID','SETGID'})
        self.assertTrue(batch['HostConfig']['ReadonlyRootfs'])
        self.assertIn('no-new-privileges:true',batch['HostConfig']['SecurityOpt'])
        self.assertEqual(batch['Entrypoint'],['python3','-I','/opt/lab/worker/sandbox_entry.py'])
        self.assertEqual(json.loads(batch['Cmd'][0]),{'verification':True,'timeout_seconds':900})
        self.assertEqual(batch['HostConfig']['Memory'],6*1024**3)
        self.assertEqual(batch['HostConfig']['NanoCpus'],4*10**9)
        self.assertEqual(batch['HostConfig']['PidsLimit'],768)

    def test_cache_environment_is_fixed_and_all_external_requests_use_public_proxy(self):
        env=dict(item.split('=',1) for item in self.config(self.batch())['Env'])
        self.assertEqual(env['GRADLE_USER_HOME'],'/cache/gradle')
        self.assertEqual(env['npm_config_cache'],'/cache/npm')
        self.assertIn('/cache/maven',env['MAVEN_OPTS'])
        self.assertIn('ActiveProcessorCount=4',env['JAVA_TOOL_OPTIONS'])
        self.assertIn('-Dhttps.proxyHost=lab-egress',env['JAVA_TOOL_OPTIONS'])
        self.assertIn('-Djna.tmpdir=/cache/native',env['JAVA_TOOL_OPTIONS'])
        self.assertIn('-Dorg.lwjgl.system.SharedLibraryExtractPath=/cache/native',env['JAVA_TOOL_OPTIONS'])
        self.assertEqual(env['https_proxy'],'http://lab-egress:3128')
        self.assertEqual(env['MINECRAFT_EULA_ACCEPTED'],'false')
        self.assertFalse(any(name in env for name in ('AGENT_TASKS','DOCKER_HOST','AWS_SECRET_ACCESS_KEY')))


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,
                     'Actual broker cache ownership requires Linux root in an isolated test container')
class RunnerCacheFilesystemTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.engine=FakeEngine()
        self.broker=Broker(self.root/'state',self.engine)
        self.pid='a'*14
        self.request={'project':self.pid,'job':'b'*14,'argv':['gradle','test'],'record':False}

    def prepare_run(self,request=None):
        token=self.broker.create(request or self.request)['Id']
        data=self.broker.load(token);data['uploaded']=True;self.broker.save(token,data)
        return token

    def test_cache_survives_run_delete_and_does_not_enter_workspace_snapshot(self):
        first=self.prepare_run()
        cache=self.broker.root/'projects'/self.pid/'cache'
        (cache/'downloaded.jar').write_bytes(b'PRIVATE_CACHE_ONLY')
        (self.broker.directory(first)/'workspace'/'source.txt').write_text('source')
        stream=io.BytesIO();self.broker.archive(first,'workspace',stream);stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            self.assertFalse(any('cache' in name for name in archive.getnames()))
            self.assertFalse(any(b'PRIVATE_CACHE_ONLY' in archive.extractfile(member).read()
                                 for member in archive.getmembers() if member.isfile()))
        self.broker.delete(first)
        second=self.prepare_run()
        self.assertNotEqual(first,second)
        self.assertEqual((cache/'downloaded.jar').read_bytes(),b'PRIVATE_CACHE_ONLY')
        for relative in ('projects','projects/'+self.pid):
            info=(self.broker.root/relative).stat()
            self.assertEqual((info.st_uid,stat.S_IMODE(info.st_mode)),(0,0o700))
        for relative in ('','project-gradle','build','native'):
            info=(cache/relative).stat()
            self.assertEqual((info.st_uid,info.st_gid),(1000,1000))

    def test_same_project_cannot_have_two_unfinished_runs(self):
        token=self.prepare_run()
        with self.assertRaises(ValueError):self.broker.create({**self.request,'job':'e'*14})
        self.broker.create({**self.request,'project':'f'*14,'job':'e'*14})
        self.assertTrue(self.broker.directory(token).is_dir())

    def test_cache_parents_and_fixed_children_reject_symlinks_without_touching_targets(self):
        for relative in ('projects','projects/'+self.pid,'projects/'+self.pid+'/cache',
                         'projects/'+self.pid+'/cache/build','projects/'+self.pid+'/cache/project-gradle',
                         'projects/'+self.pid+'/cache/native'):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as scratch:
                broker=Broker(Path(scratch)/'state',FakeEngine())
                broker.prepare_cache(self.pid)
                target=Path(scratch)/'outside';target.mkdir()
                sentinel=target/'private.txt';sentinel.write_text('unchanged')
                info=sentinel.stat()
                path=broker.root/relative
                self.assertTrue(path.resolve().is_relative_to(Path(scratch).resolve()))
                shutil.rmtree(path)
                path.symlink_to(target,target_is_directory=True)
                with self.assertRaises(OSError):broker.prepare_cache(self.pid)
                self.assertEqual(sentinel.read_text(),'unchanged')
                self.assertEqual(sentinel.stat().st_uid,info.st_uid)

    def test_start_rechecks_cache_symlink_before_creating_engine_container(self):
        token=self.prepare_run()
        leaf=self.broker.root/'projects'/self.pid/'cache'/'build'
        leaf.rmdir();leaf.symlink_to(self.broker.root,target_is_directory=True)
        with self.assertRaises(OSError):self.broker.start(token)
        self.assertEqual(self.engine.created,[])

    def test_task_controlled_cache_contents_are_never_followed_by_broker(self):
        token=self.prepare_run()
        state=self.broker.root/'owner.json';before=state.read_bytes();mode=state.stat().st_mode
        cache=self.broker.root/'projects'/self.pid/'cache'
        (cache/'escape').symlink_to(self.broker.root,target_is_directory=True)
        self.broker.start(token)
        self.assertEqual(state.read_bytes(),before)
        self.assertEqual(state.stat().st_mode,mode)
        self.assertEqual(self.engine.created[0]['HostConfig']['NetworkMode'],'9'*64)

    def test_verification_output_is_root_owned_and_workspace_export_skips_build_links(self):
        request={'project':self.pid,'job':'b'*14,'verification':True,'timeout_seconds':900,'eula':False}
        token=self.prepare_run(request)
        info=(self.broker.directory(token)/'output').stat()
        self.assertEqual((info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)),(0,0,0o700))
        with patch('runner.broker.pack_tree') as pack:
            self.broker.archive(token,'workspace',io.BytesIO())
        self.assertEqual(pack.call_args.kwargs,{'exclude_top':('.lab-cache','.gradle','build')})

    def test_init_rejects_networks_with_host_gateway_or_external_connectivity(self):
        for fields in ({'Internal':False},{'Driver':'overlay'},
                        {'Options':{'com.docker.network.bridge.gateway_mode_ipv4':'nat'}},
                        {'Options':{'com.docker.network.bridge.gateway_mode_ipv4':'isolated',
                                    'com.docker.network.bridge.gateway_mode_ipv6':'nat'}},{'Options':{}}):
            with self.subTest(fields=fields),tempfile.TemporaryDirectory() as scratch:
                engine=FakeEngine({**self.engine.network,**fields})
                with self.assertRaises(RuntimeError):Broker(Path(scratch)/'state',engine)


if __name__=='__main__':unittest.main()
