"""Exercise actual Fabric/UnionClef startup in a disposable, offline client.

Stage reads cached official dependencies only. The container action loads no
generated addon or project source and cannot establish product acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UNION_SHA256 = '76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b'
REMOTE = r'''
from pathlib import Path
import hashlib,io,json,tarfile,sys
root=Path('/home/lab/.gradle/caches/modules-2/files-2.1')
files=[]
for group,name,version,kind in [
 ('net.fabricmc','fabric-loader','0.19.2','loader'),
 ('net.fabricmc','intermediary','1.21.11','loader'),
 ('net.fabricmc','sponge-mixin','0.17.2+mixin.0.8.7','loader'),
 ('net.fabricmc.fabric-api','fabric-api','0.141.3+1.21.11','mods'),
 *[('org.ow2.asm',name,'9.9','loader') for name in ('asm','asm-analysis','asm-commons','asm-tree','asm-util')]]:
 candidates=list((root/group/name/version).glob('*/'+name+'-'+version+'.jar'))
 assert len(candidates)==1,(group,name,version,candidates)
 p=candidates[0];assert not p.is_symlink() and p.is_file()
 files.append((p,kind+'/'+p.name))
inventory=[{'path':arc,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p,arc in files]
with tarfile.open(sys.argv[1],'w') as archive:
 for p,arc in files:
  info=tarfile.TarInfo(arc);info.size=p.stat().st_size;info.mode=0o644
  with p.open('rb') as handle:archive.addfile(info,handle)
 data=json.dumps(inventory,indent=2).encode();info=tarfile.TarInfo('inventory.json');info.size=len(data);info.mode=0o644
 archive.addfile(info,io.BytesIO(data))
print(json.dumps({'files':len(files),'bytes':sum(p.stat().st_size for p,_ in files)}))
'''


def stage(args):
    target=args.dependencies.resolve()
    if ROOT/'audit' not in target.parents:
        raise ValueError('Staged dependencies must remain inside Lab audit')
    if target.exists():
        inventory=json.loads((target/'inventory.json').read_text('utf-8'))
        assert all(hashlib.sha256((target/e['path']).read_bytes()).hexdigest()==e['sha256'] for e in inventory)
        add_loader_mixinextras(target,inventory)
        print(json.dumps({'status':'STAGED_REUSED','files':len(inventory)}));return
    temporary='/tmp/lab-client-dependencies-'+uuid.uuid4().hex+'.tar'
    archive=target.with_suffix('.tar')
    try:
        subprocess.run([args.docker,'exec','-i',args.source_container,'python3','-',temporary],
                       input=REMOTE,text=True,check=True)
        subprocess.run([args.docker,'cp',args.source_container+':'+temporary,str(archive)],check=True)
        target.mkdir(parents=True)
        with tarfile.open(archive) as contents:
            for member in contents:
                name=Path(member.name)
                assert member.isfile() and not member.issym() and not member.islnk()
                assert not name.is_absolute() and '..' not in name.parts
                destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
                with contents.extractfile(member) as source,destination.open('xb') as output:shutil.copyfileobj(source,output)
        inventory=json.loads((target/'inventory.json').read_text('utf-8'))
        for source in [args.project/'libs/unionclef-1.21.11-0.95.61.jar',
                       *(args.project/'libs/nested').glob('*.jar')]:
            assert source.is_file() and not source.is_symlink()
            raw=source.read_bytes()
            if source.name.startswith('unionclef-'):assert hashlib.sha256(raw).hexdigest()==UNION_SHA256
            destination=target/'mods'/source.name;destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(raw)
            inventory.append({'path':'mods/'+source.name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
        (target/'inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
        add_loader_mixinextras(target,inventory)
        print(json.dumps({'status':'STAGED','files':len(inventory),'bytes':sum(e['bytes'] for e in inventory),
                          'project_sources_copied':False,'credential_files_copied':False}))
    finally:
        if archive.exists():archive.unlink()
        subprocess.run([args.docker,'exec',args.source_container,'python3','-c',
                        'from pathlib import Path; Path('+repr(temporary)+').unlink(missing_ok=True)'],check=False)


def add_loader_mixinextras(target,inventory):
    """Match Loom's Loader-provided runtime, rather than force Union's old copy."""
    name='mods/mixinextras-fabric-0.5.4.jar'
    if any(e['path']==name for e in inventory):return
    with zipfile.ZipFile(target/'loader/fabric-loader-0.19.2.jar') as loader:
        raw=loader.read('META-INF/jars/mixinextras-fabric-0.5.4.jar')
    digest=hashlib.sha256(raw).hexdigest()
    assert digest=='bd79ddb2f54b39a08deeb50cb12439bba01481264dd5a9f5ea830551bfef31ff'
    (target/name).write_bytes(raw)
    inventory.append({'path':name,'sha256':digest,'bytes':len(raw),
                      'provenance':'Pinned Fabric Loader bundled MixinExtras runtime'})
    (target/'inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')


def run(args):
    sys.path.insert(0,'/opt/lab')
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from worker.world_fixture import WorldFixtureSession
    from world_client_smoke import close_window,stop
    if os.environ.get('MINECRAFT_EULA_ACCEPTED')!='true':raise ValueError('Existing platform Minecraft consent required')
    args.output.mkdir(parents=True,exist_ok=True)
    inventory=json.loads((args.runtime/'runtime-inventory.json').read_text('utf-8'))
    dependencies=json.loads((args.dependencies/'inventory.json').read_text('utf-8'))
    for entry in dependencies:
        assert hashlib.sha256((args.dependencies/entry['path']).read_bytes()).hexdigest()==entry['sha256']
    with tempfile.TemporaryDirectory(prefix='lab-client-dependency-',dir='/tmp') as temporary:
        root=Path(temporary);root.chmod(0o755)
        workspace=root/'workspace';workspace.mkdir(mode=0o755)
        (workspace/'gradle.properties').write_text('minecraft_version=1.21.11\n')
        session=WorldFixtureSession(workspace,args.output/'preparation',300,eula=True,store=args.store)
        xserver=None;process=None;receipts=[]
        try:
            prepared=session.finish();assert prepared['status']=='TEMPLATE_READY',prepared
            with (args.output/'xvfb.log').open('wb') as xlog:
                xserver=subprocess.Popen(['Xvfb',':99','-screen','0','1280x720x24','-nolisten','tcp','-ac'],
                                         stdout=xlog,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+10
            while not Path('/tmp/.X11-unix/X99').exists():
                assert xserver.poll() is None and time.monotonic()<deadline,'X display did not start'
                time.sleep(.05)
            for number in range(1,args.runs+1):
                output=args.output/('run-'+str(number));output.mkdir()
                _,_,fixture=session.launch(['direct-fabric-client'],'dependency-smoke-'+str(number),output)
                game=Path(fixture['game_directory']);mods=game/'mods';mods.mkdir(mode=0o777);mods.chmod(0o777)
                for entry in dependencies:
                    if entry['path'].startswith('mods/'):
                        shutil.copyfile(args.dependencies/entry['path'],mods/Path(entry['path']).name)
                natives=game/'natives';natives.mkdir(mode=0o777);natives.chmod(0o777)
                classpath=[str(args.runtime/'client.jar')]
                classpath += [str(args.runtime/e['path']) for e in inventory['files'] if e['path'].startswith('libraries/')]
                classpath += [str(args.dependencies/e['path']) for e in dependencies if e['path'].startswith('loader/')]
                helper=Path('/opt/lab/fixtures/minecraft-client/runtime/dependency-bootstrap.jar')
                receipt_path=game/'client-dependency-preparation.json'
                env={k:v for k,v in os.environ.items() if k not in ('JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','JAVA_OPTS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH')}
                env.update(DISPLAY=':99',HOME=str(game))
                argv=['/opt/java/openjdk/bin/java','-Xms256M','-Xmx1500M','-XX:ActiveProcessorCount=2',
                    '-Djava.io.tmpdir='+str(natives),'-Dorg.lwjgl.system.SharedLibraryExtractPath='+str(natives),
                    '-Dfabric.addMods='+str(helper),'-Dlab.client.setup.adapter=altoclef-title-1.21.11-0.95.61',
                    '-Dlab.client.setup.world=lab-fixture','-Dlab.client.setup.output='+str(receipt_path),
                    '-cp',':'.join(classpath),'net.fabricmc.loader.impl.launch.knot.KnotClient',
                    '--username','LabDependency','--version','1.21.11',
                    '--uuid','00000000-0000-0000-0000-000000000002','--accessToken','0','--userType','legacy',
                    '--assetsDir',str(args.runtime/'assets'),'--assetIndex',inventory['asset_index'],
                    '--gameDir',str(game),'--width','1280','--height','720']
                log_path=output/'client.log';started=time.monotonic()
                with log_path.open('wb') as log:
                    process=subprocess.Popen(argv,cwd=game,env=env,stdout=log,stderr=subprocess.STDOUT,
                                             user=1000,group=1000,extra_groups=[],start_new_session=True)
                    deadline=time.monotonic()+args.timeout
                    while time.monotonic()<deadline:
                        if receipt_path.exists():
                            receipt=json.loads(receipt_path.read_text('utf-8'))
                            if receipt['status']=='UNAVAILABLE':raise RuntimeError(json.dumps(receipt))
                            if receipt['status']=='WORLD_LOADED':break
                        if process.poll() is not None:raise RuntimeError('Client exited before readiness: '+str(process.returncode))
                        time.sleep(.25)
                    else:raise RuntimeError('Explicit audit deadline elapsed; see client.log')
                    joined_seconds=round(time.monotonic()-started,3)
                    (output/'client-dependency-preparation.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
                    text=log_path.read_text(errors='replace')
                    events=[]
                    for line in text.splitlines():
                        marker='LAB_CLIENT_DEPENDENCY_PREPARATION:'
                        if marker in line:events.append(json.loads(line.split(marker,1)[1]))
                    statuses=[e['status'] for e in events]
                    assert statuses.index('READY')<statuses.index('WORLD_OPENING')<statuses.index('WORLD_LOADED'),statuses
                    ready=next(e for e in events if e['status']=='READY')
                    assert ready['facts']['title_screen_observed'] and ready['facts']['task_chain_present'] and ready['facts']['slot_handler_present']
                    assert 'LabDependency joined the game' in text
                    assert text.index('"status":"READY"')<text.index('LabDependency joined the game')
                    assert not receipt['product_assertions']
                    time.sleep(2)
                    subprocess.run(['ffmpeg','-nostdin','-y','-loglevel','error','-f','x11grab','-video_size','1280x720',
                                    '-i',':99','-frames:v','1',str(output/'world.png')],check=True,timeout=20)
                    window=close_window(':99');process.wait(timeout=60)
                assert process.returncode==0,process.returncode
                final_log=log_path.read_text(errors='replace');assert 'Stopping server' in final_log
                receipts.append({'run':number,'status':'DEPENDENCY_WORLD_OBSERVED','preparation':receipt,
                    'events':events,'normal_exit_code':process.returncode,'player_joined':True,
                    'dependency_ready_before_player_join':True,'duration_seconds':round(time.monotonic()-started,3),
                    'join_seconds':joined_seconds,'window_title':window,'fixture':fixture,
                    'screenshot':'run-'+str(number)+'/world.png','product_tests_executed':False})
                session.release();process=None
            result={'status':'DEPENDENCY_WORLD_OBSERVED','runs':receipts,'network':'none','client_uid':1000,
                    'minecraft_version':'1.21.11','unionclef_version':'1.21.11-0.95.61',
                    'host_game_directory_mounted':False,'project_sources_loaded':False,'product_tests_executed':False}
            (args.output/'client-dependency-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps({'status':result['status'],'runs':len(receipts),'join_seconds':[r['join_seconds'] for r in receipts]}))
        finally:
            stop(process);stop(xserver);session.close(cancel=True)


def loom(args):
    """Observe a real Loom task's command, preserving its actual mod classpath."""
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lab-dependency-loom-',dir='/tmp') as temporary:
        root=Path(temporary);home=root/'gradle-home';project=root/'project';project.mkdir()
        copied=0
        for relative in ('caches/modules-2','caches/fabric-loom'):
            def ignore(directory,names):
                return [name for name in names if name=='assets' or name.endswith('.lock')]
            shutil.copytree(args.cache/relative,home/relative,ignore=ignore)
            copied+=sum(p.stat().st_size for p in (home/relative).rglob('*') if p.is_file())
        (project/'settings.gradle').write_text("""pluginManagement {
 repositories { maven { url = 'https://maven.fabricmc.net/' }; gradlePluginPortal(); mavenCentral() }
}
rootProject.name = 'actual-loom-dependency-selection'
""",encoding='utf-8')
        (project/'gradle.properties').write_text('org.gradle.jvmargs=-Xmx1G -XX:ActiveProcessorCount=2\norg.gradle.workers.max=2\n',encoding='ascii')
        observed=args.output/'loom-arguments.json'
        source=project/'src/main/java/net/fabricmc/devlaunchinjector/Main.java'
        source.parent.mkdir(parents=True)
        source.write_text('''package net.fabricmc.devlaunchinjector;
import java.nio.file.*;
import java.lang.management.ManagementFactory;
import java.util.*;
public class Main {
 public static void main(String[] args) throws Exception {
  List<String> all=new ArrayList<>(ManagementFactory.getRuntimeMXBean().getInputArguments());
  all.addAll(List.of(args));
  StringBuilder json=new StringBuilder("{\\"argv\\":[");
  for (String arg:all) {
   if(json.length()>9)json.append(',');
   json.append('"').append(arg.replace("\\\\","\\\\\\\\").replace("\\\"","\\\\\\\"")).append('"');
  }
  json.append("],\\"cwd\\":\\"").append(Path.of("").toAbsolutePath()).append("\\"}");
  Files.writeString(Path.of("'''+str(observed)+'''"),json.toString());
 }
}
''',encoding='utf-8')
        (project/'build.gradle').write_text("""plugins { id 'net.fabricmc.fabric-loom-remap' version '1.15.5' }
repositories {
 maven { url = uri('"""+str(args.runtime/'libraries')+"""'); metadataSources { artifact() } }
 maven { url = 'https://maven.fabricmc.net/' }
 maven { url = 'https://maven-central.storage-download.googleapis.com/maven2/' }
 mavenCentral()
}
dependencies {
 minecraft 'com.mojang:minecraft:1.21.11'
 mappings 'net.fabricmc:yarn:1.21.11+build.3:v2'
 modImplementation 'net.fabricmc:fabric-loader:0.19.2'
 modImplementation 'net.fabricmc.fabric-api:fabric-api:0.141.3+1.21.11'
 modImplementation files('"""+str(args.dependencies/'mods/unionclef-1.21.11-0.95.61.jar')+"""')
}
loom { runs { client {
 client()
 runDir 'old-run'
 programArgs '--gameDir','old-run','--quickPlayMultiplayer','old.example:25565'
} } }
tasks.named('runClient') {
 // Project output supplies a recording DLI entry point, with the actual Loom
 // runtime classpath kept intact for dependency selection.
 setDependsOn([])
}
""",encoding='utf-8')
        classes=project/'build/classes/java/main';classes.mkdir(parents=True)
        subprocess.run(['javac','--release','21','-d',str(classes),str(source)],check=True)
        game=root/'private-game';(game/'saves/lab-fixture').mkdir(parents=True)
        (game/'saves/lab-fixture/level.dat').write_bytes(b'launcher-only-contract')
        env={k:v for k,v in os.environ.items() if k not in ('JAVA_TOOL_OPTIONS','CI')}
        env.update(GRADLE_USER_HOME=str(home),LAB_WORLD_FIXTURE_MODE='quickplay',
            LAB_WORLD_FIXTURE_NAME='lab-fixture',LAB_WORLD_FIXTURE_GAME_DIR=str(game),
            LAB_CLIENT_DEPENDENCY_BOOTSTRAP_JAR='/opt/lab/fixtures/minecraft-client/runtime/dependency-bootstrap.jar')
        with (args.output/'loom.log').open('wb') as log:
            result=subprocess.run(['/opt/gradle/bin/gradle','--offline','--no-daemon','--console=plain',
                '--init-script',str(args.adapter),'runClient'],cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
        if result.returncode!=0:raise RuntimeError('Real Loom dependency selection probe failed; see loom.log')
        observed_value=json.loads(observed.read_text('utf-8'));argv=observed_value['argv']
        assert any(x=='-Dlab.client.setup.adapter=altoclef-title-1.21.11-0.95.61' for x in argv),argv
        assert any(x.startswith('-Dfabric.addMods=') and 'dependency-bootstrap.jar' in x for x in argv),argv
        assert not any(x.startswith('--quickPlay') for x in argv),argv
        assert argv.count('--gameDir')==1 and argv[argv.index('--gameDir')+1]==str(game),argv
        assert observed_value['cwd']==str(game),observed_value
        value={'status':'ACTUAL_LOOM_DEPENDENCY_SELECTION_OBSERVED','task':'net.fabricmc.loom.task.RunGameTask',
            'native_quickplay_removed':True,'helper_selected':True,'private_game_directory':True,
            'live_cache_written':False,'copied_cache_bytes':copied,'observed':observed_value,
            'product_tests_executed':False}
        (args.output/'loom-dependency-smoke.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in value.items() if k!='observed'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage','run','loom'])
    parser.add_argument('--runtime',type=Path,default=Path(__file__).resolve().parent/'world-client-runtime')
    parser.add_argument('--dependencies',type=Path,default=Path(__file__).resolve().parent/'client-dependency-runtime')
    parser.add_argument('--project',type=Path,default=ROOT/'projects/7aa2e3592f1640/workspace')
    parser.add_argument('--output',type=Path,default=Path('/output'))
    parser.add_argument('--store',type=Path,default=Path('/world-templates'))
    parser.add_argument('--cache',type=Path,default=Path('/source-cache'))
    parser.add_argument('--adapter',type=Path,default=Path('/opt/lab/fixtures/minecraft-client/quickplay.init.gradle'))
    parser.add_argument('--runs',type=int,default=2)
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--source-container',default='codex-development-lab-agent-dslab-1')
    parser.add_argument('--docker',default='docker')
    arguments=parser.parse_args()
    stage(arguments) if arguments.action=='stage' else loom(arguments) if arguments.action=='loom' else run(arguments)
