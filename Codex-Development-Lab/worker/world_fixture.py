"""Image-owned world preparation, independent of the mod and its Gradle build.

Only vanilla Minecraft generates templates. A separate trusted process can do
this while unprivileged project compilation runs; no process-wide UID changes
are made by a background thread. Templates never establish product PASS.
"""
from __future__ import annotations

import argparse
import errno
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

_IMAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_IMAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMAGE_ROOT))
from worker.world_templates import TemplateStore

SERVER_SHA256 = 'f83b8e093865806f931c7e34aae41b177d4c076335263dd124c75d6d65dd1726'
SERVER_JAR = _IMAGE_ROOT / 'fixtures/minecraft-client/runtime/server-1.21.11.jar'
ADAPTER = _IMAGE_ROOT / 'fixtures/minecraft-client/quickplay.init.gradle'
CLIENT_BOOTSTRAP = _IMAGE_ROOT / 'fixtures/minecraft-client/runtime/dependency-bootstrap.jar'
PROFILE = {'minecraft_version':'1.21.11', 'world_profile':'flat-survival-v1',
           'bootstrap_revision':'vanilla-quickplay-v1', 'allow_commands':True,
           'gamemode':'survival','difficulty':'normal',
           'display_name':'Lab Fixture',
           'worldgen_fingerprint':'vanilla-flat-plains-seed-0',
           'dependency_fingerprint':'vanilla', 'server_sha256':SERVER_SHA256}


def _stop(process):
    if process is not None and process.poll() is None:
        try:os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait()


def _die_with_parent(expected_parent):
    # The generator JVM has its own process group for narrow cancellation, but
    # must not survive a killed preparation supervisor.
    import ctypes
    if os.getppid()!=expected_parent:
        os.kill(os.getpid(),signal.SIGKILL)
    if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)!=0:
        raise OSError('Unable to bind generator lifetime to its supervisor')
    if os.getppid()!=expected_parent:
        os.kill(os.getpid(),signal.SIGKILL)


def generate_world(destination, logfile, timeout, *, server=SERVER_JAR):
    """Generate and close a real vanilla world, with no project code/classpath."""
    server = Path(server)
    if not server.is_file() or server.is_symlink() or hashlib.sha256(server.read_bytes()).hexdigest() != SERVER_SHA256:
        raise ValueError('Pinned Minecraft 1.21.11 server asset is missing or changed')
    destination = Path(destination).absolute()
    with tempfile.TemporaryDirectory(prefix='vanilla-server-', dir=destination.parent) as parent:
        cwd = Path(parent)
        # Called only after the existing platform consent is checked. Never
        # consult/write the user's game folder or alter the platform setting.
        (cwd/'eula.txt').write_text('eula=true\n', encoding='ascii')
        settings = {'server-ip':'127.0.0.1', 'server-port':'0', 'online-mode':'false',
            'enable-rcon':'false', 'enable-query':'false', 'max-players':'1',
            'view-distance':'2', 'simulation-distance':'2', 'gamemode':'survival',
            'difficulty':PROFILE['difficulty'], 'level-name':str(destination), 'level-seed':'0',
            'level-type':'minecraft:flat', 'generate-structures':'false',
            'generator-settings':json.dumps({'layers':[{'block':'minecraft:bedrock','height':1},
                {'block':'minecraft:dirt','height':2},{'block':'minecraft:grass_block','height':1}],
                'biome':'minecraft:plains','lakes':False,'features':False,'structure_overrides':[]})}
        (cwd/'server.properties').write_text(''.join(k+'='+v+'\n' for k,v in settings.items()),encoding='utf-8')
        env = {k:v for k,v in os.environ.items() if k not in
               ('JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','JAVA_OPTS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH')}
        # A fixed trusted executable, private extraction directory and no
        # project/native cache keep preparation independent of malicious builds.
        java = Path(os.environ.get('JAVA_HOME','/opt/java/openjdk'))/'bin/java'
        args = [str(java), '-Xms256M', '-Xmx1G', '-XX:ActiveProcessorCount=2',
                '-Djava.io.tmpdir='+str(cwd), '-jar', str(server), 'nogui']
        process = None
        started = time.monotonic()
        done = False
        try:
            with Path(logfile).open('ab') as log:
                position=log.tell()
                process = subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.PIPE,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True,
                    preexec_fn=partial(_die_with_parent,os.getpid()))
                pending = b''
                while time.monotonic()-started < timeout:
                    with Path(logfile).open('rb') as reader:
                        reader.seek(position)
                        block=reader.read(1024*1024)
                        position=reader.tell()
                    pending=(pending+block)[-2*1024*1024:]
                    if re.search(rb'\bDone \([0-9.,]+s\)!',pending):
                        done=True
                        process.stdin.write(b'save-all flush\nstop\n')
                        process.stdin.flush()
                        process.wait(timeout=max(1,timeout-(time.monotonic()-started)))
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                if not done or process.poll() != 0:
                    raise RuntimeError('Vanilla world generation did not finish and close cleanly; see prepare.log')
        finally:
            _stop(process)
            if process is not None and process.stdin is not None:process.stdin.close()
    return {'generator':'vanilla-server','minecraft_version':'1.21.11',
            'server_sha256':SERVER_SHA256,'clean_shutdown':True,
            'duration_seconds':round(time.monotonic()-started,3)}


def prepare(store, output, timeout, *, eula=False):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    result={'status':'NOT_TESTED','profile':PROFILE,
            'provenance':'Platform world setup only; not client readiness or product PASS.'}
    if not eula:
        result['reason']='Minecraft EULA has not already been accepted in platform configuration'
    else:
        started=time.monotonic()
        retries=[]
        transient={errno.EINTR,errno.EAGAIN,errno.ESTALE,errno.ENODATA}
        while True:
            try:
                generation={}
                def generate(world):
                    remaining=max(.01,timeout-(time.monotonic()-started))
                    generation.update(generate_world(world,output/'prepare.log',remaining))
                descriptor=TemplateStore(store).ensure(PROFILE,generate)
                result.update(status='TEMPLATE_READY',template=descriptor,generation=generation)
                if retries:result['recovered_filesystem_errors']=retries
                break
            except Exception as exc:
                # Keep full diagnostics. Permission/validation/asset errors are
                # never retried or bypassed; only exact transient OS codes use
                # the existing user-selected verification time budget.
                try:
                    with (output/'error.log').open('a',encoding='utf-8') as errors:
                        traceback.print_exc(file=errors)
                except OSError:
                    traceback.print_exception(type(exc),exc,exc.__traceback__,file=sys.stderr)
                remaining=timeout-(time.monotonic()-started)
                if isinstance(exc,OSError) and exc.errno in transient and remaining>0:
                    retries.append({'exception':type(exc).__name__,'errno':exc.errno,'message':str(exc)})
                    time.sleep(min(2.0,.1*2**min(len(retries)-1,5),remaining))
                    continue
                result.update(status='ERROR',reason=type(exc).__name__+': '+str(exc))
                if retries:result['filesystem_retries']=retries
                break
    temporary=output/'.world-fixture.tmp'
    temporary.write_text(json.dumps(result,indent=2),encoding='utf-8')
    temporary.replace(output/'world-fixture.json')
    return result


def _mode(workspace):
    """Only a small declared mode/version, never paths or commands from projects."""
    path=Path(workspace)/'gradle.properties'
    properties={}
    if path.exists():
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as handle:
            import stat
            info=os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>1024*1024:
                raise ValueError('Unsafe Gradle properties for world profile')
            for line in handle.read().decode('utf-8').splitlines():
                if '=' in line and not line.lstrip().startswith(('#','!')):
                    key,value=line.split('=',1);properties[key.strip()]=value.strip()
    mode=properties.get('lab_world_fixture_mode','quickplay')
    if mode not in ('quickplay','managed','off'):
        raise ValueError('lab_world_fixture_mode must be quickplay, managed or off')
    version=properties.get('minecraft_version',properties.get('minecraftVersion'))
    if version is None:
        for filename in ('build.gradle','build.gradle.kts'):
            source=Path(workspace)/filename
            if source.is_file() and not source.is_symlink():
                with source.open('r',encoding='utf-8') as text:
                    match=re.search(r'com\.mojang:minecraft:([0-9]+\.[0-9]+(?:\.[0-9]+)?)',
                                    text.read(1024*1024))
                if match:version=match[1];break
    if mode=='quickplay' and version is None:
        return 'managed','Minecraft version not established; scenario keeps world lifecycle'
    if version is not None and version != PROFILE['minecraft_version']:
        return 'managed','Unsupported platform world profile for Minecraft '+version
    return mode,None


class WorldFixtureSession:
    """One private game directory per physical scenario; no world/config reuse."""
    def __init__(self, workspace, output, timeout, *, eula=False, store=Path('/world-templates')):
        self.workspace,self.output,self.timeout=Path(workspace),Path(output),timeout
        self.store=Path(store)
        self.process=None
        self._prepared=None
        self.temp=None
        self.active=[]
        self.mode='off'
        try:
            self.mode,reason=_mode(workspace)
            if reason:self._prepared={'status':'MANAGED','mode':self.mode,'reason':reason}
            elif self.mode!='quickplay':self._prepared={'status':'MANAGED','mode':self.mode}
            elif not eula:self._prepared={'status':'NOT_TESTED','reason':'Existing platform Minecraft consent is required'}
            else:
                self.output.mkdir(mode=0o700,parents=True,exist_ok=True)
                (self.output/'world-fixture.json').unlink(missing_ok=True)
                log=(self.output/'process.log').open('wb')
                try:
                    self.process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),
                        'prepare','--store',str(self.store),'--output',str(self.output),
                        '--timeout',str(timeout),'--existing-eula-consent'],
                        cwd=str(_IMAGE_ROOT),stdout=log,stderr=subprocess.STDOUT,
                        start_new_session=True,close_fds=True,
                        preexec_fn=partial(_die_with_parent,os.getpid()))
                finally:log.close()
        except Exception as exc:
            self._prepared={'status':'ERROR','reason':type(exc).__name__+': '+str(exc)}

    def finish(self):
        if self._prepared is not None:return self._prepared
        try:
            code=self.process.wait(timeout=self.timeout+20)
            value=json.loads((self.output/'world-fixture.json').read_text(encoding='utf-8'))
            if (not isinstance(value,dict) or value.get('profile')!=PROFILE or
                    value.get('status') not in ('TEMPLATE_READY','ERROR','NOT_TESTED')):
                raise ValueError('Invalid world-preparation receipt')
            if code!=0 or value['status']!='TEMPLATE_READY':
                raise RuntimeError(value.get('reason','World-preparation process failed: '+str(code)))
            self._prepared=value
        except Exception as exc:
            _stop(self.process)
            self._prepared={'status':'ERROR','reason':type(exc).__name__+': '+str(exc)}
        return self._prepared

    def launch(self,argv,scenario_id,directory):
        prepared=self.finish()
        if prepared['status']=='MANAGED' or (prepared['status']=='NOT_CONFIGURED' and self.mode=='managed'):
            return argv,{}, {'status':'MANAGED','detail':prepared.get('reason','Scenario owns its world lifecycle')}
        if prepared['status']!='TEMPLATE_READY':
            raise RuntimeError('World fixture unavailable: '+prepared.get('reason',prepared['status']))
        if self.temp is None:
            parent=Path('/game-data') if Path('/game-data').is_dir() else None
            self.temp=Path(tempfile.mkdtemp(prefix='lab-world-runs-',dir=parent))
            self.temp.chmod(0o755)
        # Never use a scenario ID as a filesystem component from a project.
        run_id=uuid.uuid4().hex
        root=self.temp/run_id
        root.mkdir(mode=0o755)
        game=root/'game';game.mkdir(mode=0o777);game.chmod(0o777)
        self.active.append((root,game))
        saves=game/'saves';saves.mkdir(mode=0o777);saves.chmod(0o777)
        name='lab-fixture'
        clone=TemplateStore(self.store).clone(PROFILE,saves/name,file_mode=0o666,directory_mode=0o777)
        (game/'options.txt').write_text('tutorialStep:none\nsoundCategory_master:0.0\n'
            'onboardAccessibility:false\npauseOnLostFocus:false\n',encoding='utf-8')
        (game/'options.txt').chmod(0o666)
        evidence={'status':'CLONE_READY','scenario_id':scenario_id,'run_id':run_id,
            'template_key':clone['key'],'manifest_sha256':clone['manifest_sha256'],
            'world_name':name,'game_directory':str(game),
            'provenance':'Fresh saved world and gameDir; no assertion of loaded client or product success.'}
        (Path(directory)/'world-fixture.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        env={'LAB_WORLD_FIXTURE_MODE':'quickplay','LAB_WORLD_FIXTURE_NAME':name,
             'LAB_WORLD_FIXTURE_GAME_DIR':str(game),
             'LAB_CLIENT_DEPENDENCY_BOOTSTRAP_JAR':str(CLIENT_BOOTSTRAP)}
        return argv+['--init-script',str(ADAPTER)],env,evidence

    def collect_client_setup(self, directory):
        """Retain per-process setup observations before deleting the clone.

        This client-written receipt is diagnostic data, not trusted product
        assertions or authorization to overwrite real JUnit failures.
        """
        if not self.active:return None
        game=self.active[-1][1]
        source=game/'client-dependency-preparation.json'
        import stat
        try:
            fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:return None
        with os.fdopen(fd,'rb') as handle:
            info=os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>65536:
                raise ValueError('Unsafe client dependency preparation receipt')
            raw=handle.read(65537)
        if len(raw)>65536:raise ValueError('Oversized client dependency preparation receipt')
        try:value=json.loads(raw.decode('utf-8'))
        except (ValueError,RecursionError) as exc:
            raise ValueError('Invalid client dependency preparation JSON') from exc
        if not isinstance(value,dict) or value.get('status') not in ('WAITING','READY','WORLD_OPENING','WORLD_LOADED','UNAVAILABLE','NOT_REQUIRED'):
            raise ValueError('Invalid client dependency preparation receipt')
        (Path(directory)/'client-dependency-preparation.json').write_bytes(raw)
        return {'file':'client-dependency-preparation.json','sha256':hashlib.sha256(raw).hexdigest(),
                'status':value['status'],'detail':value.get('reason') or value.get('detail'),
                'provenance':'Client setup diagnostics only; not product acceptance proof.'}

    def release(self):
        """Remove only this session's clones after their client has stopped."""
        for root,game in list(self.active):
            # UID1000 can remove its own files and the world copies in writable
            # clone directories. The supervisor only removes its empty parents.
            script='''import os,shutil,sys
from pathlib import Path
p=Path(sys.argv[1])
for entry in os.scandir(p):
    q=p/entry.name
    if entry.is_dir(follow_symlinks=False): shutil.rmtree(q)
    else: q.unlink()
'''
            identity=({'user':1000,'group':1000,'extra_groups':[]} if os.geteuid()==0 else {})
            result=subprocess.run([sys.executable,'-I','-c',script,str(game)],
                cwd=str(_IMAGE_ROOT),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                timeout=20,close_fds=True,**identity)
            if result.returncode==0:
                game.rmdir();root.rmdir();self.active.remove((root,game))

    def close(self,*,cancel=False):
        if cancel and self._prepared is None and self.process.poll() is None:
            _stop(self.process)
            self._prepared={'status':'NOT_EXECUTED','reason':'No gameplay run: preparation canceled; build result is preserved'}
        result=self.finish()
        try:self.release()
        except (OSError,subprocess.SubprocessError):pass
        # If a hostile project denied cleanup, the broker removes the private
        # run volume subtree after the whole container is confirmed stopped.
        return result


def main():
    def interrupted(signum,frame):
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,interrupted)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare'])
    parser.add_argument('--store',type=Path,default=Path('/world-templates'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--existing-eula-consent',action='store_true')
    args=parser.parse_args()
    if args.timeout<1:parser.error('timeout must be positive')
    if os.geteuid()!=0:parser.error('World preparation requires the trusted supervisor')
    # A flag alone cannot create authorization: existing platform configuration
    # must independently confirm it in this container.
    accepted=args.existing_eula_consent and os.environ.get('MINECRAFT_EULA_ACCEPTED')=='true'
    result=prepare(args.store,args.output,args.timeout,eula=accepted)
    print(json.dumps(result))
    return 0 if result['status']=='TEMPLATE_READY' else 1


if __name__=='__main__':
    raise SystemExit(main())
