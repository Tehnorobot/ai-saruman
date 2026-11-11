"""Stage cached official client assets, then exercise real vanilla QuickPlay.

This is an explicit audit command, not a worker module or product-test adapter.
The stage action only reads existing game caches. The run action requires the
platform's pre-existing Minecraft consent and uses disposable world copies.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid


REMOTE_STAGE = r'''
from pathlib import Path
import hashlib,json,tarfile,sys
root=Path('/home/lab/.gradle')
cache=root/'caches/fabric-loom'
raw=(cache/'1.21.11/mojang_minecraft_info.json').read_bytes()
meta=json.loads(raw)
client=cache/'1.21.11/minecraft-client.jar'
assert hashlib.sha1(client.read_bytes()).hexdigest()==meta['downloads']['client']['sha1']
entries=[(client,'client.jar',meta['downloads']['client']['sha1'])]
for lib in meta['libraries']:
    allowed=not lib.get('rules')
    for rule in lib.get('rules',[]):
        host=rule.get('os',{})
        if host.get('name','linux')=='linux' and host.get('arch','x86_64') in ('x86_64','amd64'):
            allowed=rule['action']=='allow'
    if not allowed:continue
    artifact=lib.get('downloads',{}).get('artifact')
    if not artifact:continue
    group,name,version,*extra=lib['name'].split(':')
    candidates=list((root/'caches/modules-2/files-2.1'/group/name/version).glob('*/'+Path(artifact['path']).name))
    candidates=[p for p in candidates if p.is_file() and not p.is_symlink() and hashlib.sha1(p.read_bytes()).hexdigest()==artifact['sha1']]
    assert candidates,lib['name']
    entries.append((candidates[0],'libraries/'+artifact['path'],artifact['sha1']))
index=cache/'assets/indexes/1.21.11-29.json'
assert hashlib.sha1(index.read_bytes()).hexdigest()==meta['assetIndex']['sha1']
entries.append((index,'assets/indexes/29.json',meta['assetIndex']['sha1']))
seen=set()
for item in json.loads(index.read_text())['objects'].values():
    digest=item['hash']
    if digest in seen:continue
    seen.add(digest)
    p=cache/'assets/objects'/digest[:2]/digest
    assert p.is_file() and not p.is_symlink() and hashlib.sha1(p.read_bytes()).hexdigest()==digest,str(p)
    entries.append((p,'assets/objects/'+digest[:2]+'/'+digest,digest))
target=Path(sys.argv[1])
inventory={'minecraft_version':'1.21.11','main_class':meta['mainClass'],'asset_index':'29',
 'provenance':'Official cached client/library/asset files; SHA-1 checked against version/asset manifests.',
 'files':[{'path':arc,'sha1':digest,'bytes':p.stat().st_size} for p,arc,digest in entries],
 'client_manifest_sha256':hashlib.sha256(raw).hexdigest()}
with target.open('xb') as handle,tarfile.open(fileobj=handle,mode='w') as archive:
    import io
    for p,arc,digest in entries:
        info=tarfile.TarInfo(arc);info.size=p.stat().st_size;info.mode=0o644
        with p.open('rb') as source:archive.addfile(info,source)
    for name,content in [('runtime-inventory.json',json.dumps(inventory,indent=2).encode()),('version-manifest.json',raw)]:
        info=tarfile.TarInfo(name);info.size=len(content);info.mode=0o644;archive.addfile(info,io.BytesIO(content))
print(json.dumps({'files':len(entries),'libraries':len([x for x in entries if x[1].startswith('libraries/')]),'asset_objects':len(seen),'archive_bytes':target.stat().st_size}))
'''


def stage(args):
    runtime=args.runtime.resolve()
    allowed=Path(__file__).resolve().parent
    if allowed not in runtime.parents:
        raise ValueError('Stage destination must stay inside the Lab audit directory')
    if runtime.exists():
        raise ValueError('Use a new runtime directory rather than overwriting an existing one')
    archive=runtime.with_suffix('.tar')
    remote='/tmp/lab-world-client-runtime-'+uuid.uuid4().hex+'.tar'
    started=time.monotonic()
    try:
        result=subprocess.run([args.docker,'exec','-i',args.source_container,'python3','-',remote],
                              input=REMOTE_STAGE,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        subprocess.run([args.docker,'cp',args.source_container+':'+remote,str(archive)],check=True)
        runtime.mkdir(parents=True)
        with tarfile.open(archive) as source:
            for member in source:
                name=Path(member.name)
                if member.issym() or member.islnk() or not member.isfile() or name.is_absolute() or '..' in name.parts:
                    raise ValueError('Unsafe staged archive entry')
                target=runtime/name
                target.parent.mkdir(parents=True,exist_ok=True)
                with source.extractfile(member) as handle,target.open('xb') as output:
                    shutil.copyfileobj(handle,output)
        inventory=json.loads((runtime/'runtime-inventory.json').read_text())
        for entry in inventory['files']:
            p=runtime/entry['path']
            if p.stat().st_size!=entry['bytes'] or hashlib.sha1(p.read_bytes()).hexdigest()!=entry['sha1']:
                raise ValueError('Staged asset verification failed: '+entry['path'])
        receipt={'status':'STAGED','runtime':str(runtime),'duration_seconds':round(time.monotonic()-started,3),
                 'source_cache_read_only':True,'credential_files_copied':False,**json.loads(result.stdout)}
        (runtime/'staging-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        print(json.dumps(receipt))
    finally:
        subprocess.run([args.docker,'exec',args.source_container,'python3','-c',
                        'from pathlib import Path; Path('+repr(remote)+').unlink(missing_ok=True)'],check=False)
        if archive.exists():archive.unlink()


def close_window(display_name):
    """Request GLFW's ordinary window-close handler, letting Minecraft save."""
    lib=ctypes.CDLL('libX11.so.6')
    lib.XOpenDisplay.argtypes=[ctypes.c_char_p];lib.XOpenDisplay.restype=ctypes.c_void_p
    lib.XDefaultRootWindow.argtypes=[ctypes.c_void_p];lib.XDefaultRootWindow.restype=ctypes.c_ulong
    lib.XQueryTree.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),ctypes.POINTER(ctypes.c_uint)]
    lib.XFetchName.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_char_p)]
    lib.XInternAtom.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_int];lib.XInternAtom.restype=ctypes.c_ulong
    lib.XSendEvent.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_long,ctypes.c_void_p]
    lib.XFlush.argtypes=[ctypes.c_void_p];lib.XFree.argtypes=[ctypes.c_void_p];lib.XCloseDisplay.argtypes=[ctypes.c_void_p]
    class Data(ctypes.Union):
        _fields_=[('b',ctypes.c_char*20),('s',ctypes.c_short*10),('l',ctypes.c_long*5)]
    class Event(ctypes.Structure):
        _fields_=[('type',ctypes.c_int),('serial',ctypes.c_ulong),('send_event',ctypes.c_int),
                  ('display',ctypes.c_void_p),('window',ctypes.c_ulong),('message_type',ctypes.c_ulong),
                  ('format',ctypes.c_int),('data',Data),('padding',ctypes.c_byte*96)]
    display=lib.XOpenDisplay(display_name.encode())
    if not display:raise RuntimeError('Cannot open the audit X display')
    try:
        top=lib.XDefaultRootWindow(display)
        root,parent=ctypes.c_ulong(),ctypes.c_ulong()
        children=ctypes.POINTER(ctypes.c_ulong)();count=ctypes.c_uint()
        if not lib.XQueryTree(display,top,ctypes.byref(root),ctypes.byref(parent),ctypes.byref(children),ctypes.byref(count)):
            raise RuntimeError('Cannot inspect audit X windows')
        found=[]
        try:
            for index in range(count.value):
                name=ctypes.c_char_p()
                if lib.XFetchName(display,children[index],ctypes.byref(name)) and name.value:
                    title=name.value.decode(errors='replace')
                    if 'Minecraft' in title:found.append((children[index],title))
                    lib.XFree(name)
        finally:
            if children:lib.XFree(children)
        if len(found)!=1:raise RuntimeError('Expected exactly one Minecraft audit window: '+repr(found))
        window,title=found[0]
        event=Event();event.type=33;event.send_event=1;event.display=display;event.window=window
        event.message_type=lib.XInternAtom(display,b'WM_PROTOCOLS',0);event.format=32
        event.data.l[0]=lib.XInternAtom(display,b'WM_DELETE_WINDOW',0)
        if not lib.XSendEvent(display,window,0,0,ctypes.byref(event)):raise RuntimeError('Window-close request failed')
        lib.XFlush(display)
        return title
    finally:lib.XCloseDisplay(display)


def stop(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.wait()


def run(args):
    sys.path.insert(0,'/opt/lab')
    from worker.world_fixture import WorldFixtureSession,PROFILE
    from worker.world_templates import TemplateStore
    if os.environ.get('MINECRAFT_EULA_ACCEPTED')!='true':
        raise RuntimeError('Existing platform Minecraft consent is required')
    runtime=args.runtime
    inventory=json.loads((runtime/'runtime-inventory.json').read_text())
    if inventory['minecraft_version']!=PROFILE['minecraft_version']:raise ValueError('Client/template version mismatch')
    args.output.mkdir(parents=True,exist_ok=True)
    receipts=[]
    with tempfile.TemporaryDirectory(prefix='client-smoke-',dir='/tmp') as temp:
        scratch=Path(temp)
        (scratch/'gradle.properties').write_text('minecraft_version=1.21.11\n',encoding='ascii')
        session=WorldFixtureSession(scratch,args.output/'preparation',300,eula=True,store=args.store)
        xserver=None
        try:
            prep_started=time.monotonic();prepared=session.finish()
            if prepared['status']!='TEMPLATE_READY':raise RuntimeError(json.dumps(prepared))
            preparation_seconds=round(time.monotonic()-prep_started,3)
            access_probe=subprocess.run([sys.executable,'-I','-c','import os,sys; os.listdir(sys.argv[1])',str(args.store)],
                                        user=1000,group=1000,extra_groups=[],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            if access_probe.returncode==0:raise AssertionError('The tested client can read protected templates')
            xlog=(args.output/'xvfb.log').open('wb')
            xserver=subprocess.Popen(['Xvfb',':99','-screen','0','1280x720x24','-nolisten','tcp','-ac'],
                                     stdout=xlog,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+10
            while not Path('/tmp/.X11-unix/X99').exists():
                if xserver.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Audit display did not start')
                time.sleep(.05)
            classpath=':'.join([str(runtime/'client.jar')]+[str(runtime/e['path']) for e in inventory['files'] if e['path'].startswith('libraries/')])
            previous_game=None
            for number in (1,2):
                output=args.output/('run-'+str(number));output.mkdir()
                _,fixture_env,evidence=session.launch(['vanilla-client'],'vanilla-quickplay-'+str(number),output)
                game=Path(evidence['game_directory'])
                if previous_game is not None and game==previous_game:raise AssertionError('World/game directory was reused')
                if (game/'saves/lab-fixture/lab-contamination-probe.txt').exists():raise AssertionError('Prior world leaked')
                previous_game=game
                natives=game/'natives';natives.mkdir(mode=0o777);natives.chmod(0o777)
                env={k:v for k,v in os.environ.items() if k not in ('JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','JAVA_OPTS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH')}
                env.update(DISPLAY=':99',HOME=str(game))
                argv=['/opt/java/openjdk/bin/java','-Xms256M','-Xmx1G','-XX:ActiveProcessorCount=2',
                      '-Djava.io.tmpdir='+str(natives),'-Dorg.lwjgl.system.SharedLibraryExtractPath='+str(natives),
                      '-cp',classpath,inventory['main_class'],'--username','LabFixture','--version','1.21.11',
                      '--uuid','00000000-0000-0000-0000-000000000001','--accessToken','0','--userType','legacy',
                      '--assetsDir',str(runtime/'assets'),'--assetIndex',inventory['asset_index'],
                      '--gameDir',str(game),'--quickPlaySingleplayer',evidence['world_name'],'--width','1280','--height','720']
                process=None;started=time.monotonic()
                try:
                    with (output/'client.log').open('wb') as log:
                        process=subprocess.Popen(argv,cwd=game,env=env,stdout=log,stderr=subprocess.STDOUT,
                                                 user=1000,group=1000,extra_groups=[],start_new_session=True)
                        deadline=time.monotonic()+180
                        while time.monotonic()<deadline:
                            text=(output/'client.log').read_text(errors='replace')
                            if 'LabFixture joined the game' in text and 'logged in with entity id' in text:break
                            if process.poll() is not None:raise RuntimeError('Client exited before player join: '+str(process.returncode))
                            time.sleep(.25)
                        else:raise RuntimeError('Client/world join deadline elapsed')
                        joined_seconds=round(time.monotonic()-started,3)
                        time.sleep(3)
                        subprocess.run(['ffmpeg','-nostdin','-y','-loglevel','error','-f','x11grab','-video_size','1280x720',
                                        '-i',':99','-frames:v','1',str(output/'world.png')],check=True,timeout=20)
                        title=close_window(':99')
                        process.wait(timeout=45)
                    text=(output/'client.log').read_text(errors='replace')
                    if process.returncode!=0 or 'Stopping server' not in text:
                        raise RuntimeError('Client did not save/stop cleanly: '+str(process.returncode))
                    level=game/'saves'/evidence['world_name']/'level.dat'
                    saved_level=output/'saved-level.dat'
                    # Minecraft creates replacement level.dat files with mode
                    # 0600. The root supervisor deliberately has no DAC bypass;
                    # read through the tested UID into a supervisor-opened FD.
                    with saved_level.open('wb') as target:
                        subprocess.run([sys.executable,'-I','-c',
                            'from pathlib import Path; import sys; sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())',str(level)],
                            stdout=target,user=1000,group=1000,extra_groups=[],check=True)
                    (game/'saves/lab-fixture/lab-contamination-probe.txt').write_text('This must not appear in another clone.')
                    receipts.append({'run':number,'status':'CLIENT_WORLD_OBSERVED','fixture':evidence,'window_title':title,
                                     'player_joined':True,'join_seconds':joined_seconds,'normal_exit_code':process.returncode,
                                     'saved_level_sha256':hashlib.sha256(saved_level.read_bytes()).hexdigest(),
                                     'screenshot':'run-'+str(number)+'/world.png','product_tests_executed':False})
                finally:stop(process)
            def unexpected_regeneration(destination):
                raise AssertionError('A client run changed or invalidated the protected template')
            descriptor=TemplateStore(args.store).ensure(PROFILE,unexpected_regeneration)
            result={'status':'CLIENT_WORLD_OBSERVED','minecraft_version':'1.21.11','runs':receipts,
                    'preparation_wait_seconds':preparation_seconds,'template_reused':not bool(prepared.get('generation')),
                    'template_key':descriptor['key'],'template_manifest_sha256':descriptor['manifest_sha256'],
                    'tested_client_uid':1000,'protected_template_access_denied':True,
                    'network':'none','host_game_directory_mounted':False,'product_tests_executed':False}
            (args.output/'client-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in result.items() if k!='runs'}))
        finally:
            session.close(cancel=True)
            stop(xserver)


def loom(args):
    """Run actual Loom RunGameTask with a recording launcher, not product code."""
    args.output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='actual-loom-smoke-',dir='/tmp') as temp:
        root=Path(temp);home=root/'gradle-home';project=root/'project';project.mkdir()
        copied=0
        for relative in ('caches/modules-2','caches/fabric-loom'):
            source=args.cache/relative;target=home/relative
            def ignore(directory,names):
                return [name for name in names if name=='assets' or name.endswith('.lock')]
            shutil.copytree(source,target,ignore=ignore)
            copied+=sum(p.stat().st_size for p in target.rglob('*') if p.is_file())
        (project/'settings.gradle').write_text("""pluginManagement {
 repositories { maven { url = 'https://maven.fabricmc.net/' }; gradlePluginPortal(); mavenCentral() }
}
rootProject.name = 'actual-loom-world-launch'
""",encoding='utf-8')
        (project/'gradle.properties').write_text('org.gradle.jvmargs=-Xmx1G -XX:ActiveProcessorCount=2\norg.gradle.workers.max=2\n',encoding='ascii')
        java=root/'Main.java';classes=root/'classes';classes.mkdir()
        java.write_text(r'''package net.fabricmc.devlaunchinjector;
import java.nio.file.*;
public class Main {
 public static void main(String[] args) throws Exception {
  StringBuilder json = new StringBuilder("[");
  for (String arg : args) {
   if (json.length() > 1) json.append(",");
   json.append("\"").append(arg.replace("\\", "\\\\").replace("\"", "\\\"")).append("\"");
  }
  json.append("]");
  Files.writeString(Path.of(System.getProperty("fixture.output")), json.toString());
  Files.writeString(Path.of(System.getProperty("fixture.output")+".cwd"), Path.of("").toAbsolutePath().toString());
 }
}
''',encoding='utf-8')
        subprocess.run(['javac','--release','21','-d',str(classes),str(java)],check=True)
        dummy=root/'recording-launcher.jar'
        subprocess.run(['jar','--create','--file',str(dummy),'-C',str(classes),'.'],check=True)
        observed=args.output/'actual-loom-arguments.json'
        (project/'build.gradle').write_text("""plugins { id 'net.fabricmc.fabric-loom-remap' version '1.15.5' }
repositories { mavenCentral(); maven { url = 'https://maven.fabricmc.net/' } }
dependencies {
 minecraft 'com.mojang:minecraft:1.21.11'
 mappings 'net.fabricmc:yarn:1.21.11+build.3:v2'
 modImplementation 'net.fabricmc:fabric-loader:0.19.2'
}
loom { runs { client {
 client()
 runDir 'old-run'
 programArgs '--gameDir', 'old-run', '--quickPlayMultiplayer', 'old.example:25565'
} } }
tasks.named('runClient') {
 setDependsOn([])
 classpath = files('"""+str(dummy)+"""')
 // The recording launcher replaces Loom's normal argument-file classpath.
 jvmArguments.set(['-cp', '"""+str(dummy)+"""', '-Dfabric.dli.env=client', '-Dfixture.output="""+str(observed)+"""'])
}
""",encoding='utf-8')
        game=root/'separate-game';(game/'saves/lab-fixture').mkdir(parents=True)
        (game/'saves/lab-fixture/level.dat').write_bytes(b'launcher-contract-only')
        env=dict(os.environ,GRADLE_USER_HOME=str(home),LAB_WORLD_FIXTURE_MODE='quickplay',
                 LAB_WORLD_FIXTURE_NAME='lab-fixture',LAB_WORLD_FIXTURE_GAME_DIR=str(game))
        env.pop('CI',None)
        with (args.output/'gradle.log').open('wb') as log:
            result=subprocess.run(['/opt/gradle/bin/gradle','--offline','--no-daemon','--console=plain',
                '--init-script','/opt/lab/fixtures/minecraft-client/quickplay.init.gradle','runClient'],
                cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
        if result.returncode!=0:raise RuntimeError('Actual Loom task failed; see gradle.log')
        arguments=json.loads(observed.read_text())
        if arguments.count('--gameDir')!=1 or arguments[arguments.index('--gameDir')+1]!=str(game):
            raise AssertionError('Actual Loom task did not select one private game directory')
        if arguments.count('--quickPlaySingleplayer')!=1 or 'old.example:25565' in arguments:
            raise AssertionError('Actual Loom task retained conflicting launch options')
        cwd=Path(Path(str(observed)+'.cwd').read_text()).resolve()
        if cwd!=game:raise AssertionError('Actual Loom exec overrode the private working directory: '+str(cwd))
        receipt={'status':'ACTUAL_LOOM_LAUNCH_CONTRACT_OBSERVED','loom_version':'1.15.5','minecraft_version':'1.21.11',
                 'task':'net.fabricmc.loom.task.RunGameTask','arguments':arguments,'working_directory':str(cwd),
                 'copied_cache_bytes':copied,'live_cache_written':False,
                 'duration_seconds':round(time.monotonic()-started,3),'product_tests_executed':False,
                 'provenance':'Real Loom plugin/task with a recording launcher; actual vanilla world loading is checked separately.'}
        (args.output/'actual-loom-smoke.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        print(json.dumps(receipt))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage','run','loom'])
    parser.add_argument('--runtime',type=Path,default=Path(__file__).resolve().parent/'world-client-runtime')
    parser.add_argument('--output',type=Path,default=Path('/output/client-smoke'))
    parser.add_argument('--store',type=Path,default=Path('/world-templates'))
    parser.add_argument('--cache',type=Path,default=Path('/source-cache'))
    parser.add_argument('--source-container',default='codex-development-lab-agent-dslab-1')
    parser.add_argument('--docker',default='docker')
    args=parser.parse_args()
    return stage(args) if args.action=='stage' else loom(args) if args.action=='loom' else run(args)


if __name__=='__main__':main()
