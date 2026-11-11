"""Independent offline client check of an isolated Mage Home build.

This runs the actual Fabric/UnionClef/addon JARs and retains machine results.
It does not change the live project, its acceptance state or its task queues.
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
import tempfile
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0,'/opt/lab')
from worker.sandbox_entry import recorded_run
from worker.world_fixture import WorldFixtureSession


def junit(folder):
    results=[]
    for path in sorted(folder.glob('*.xml')):
        raw=path.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('Unsafe JUnit document')
        tree=ET.fromstring(raw)
        cases=[]
        for case in tree.iter('testcase'):
            failure=case.find('failure')
            if failure is None:failure=case.find('error')
            skip=case.find('skipped')
            status='FAIL' if failure is not None else 'SKIPPED' if skip is not None else 'PASS'
            cases.append({'name':case.get('name'),'status':status,
                          'detail':failure.get('message') if failure is not None else
                                   skip.get('message') if skip is not None else ''})
        results.append({'file':path.name,'sha256':hashlib.sha256(raw).hexdigest(),'cases':cases})
    return results


def client_bytes(path):
    """Read private test-client files as their UID without widening capabilities."""
    code='import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())'
    result=subprocess.run([sys.executable,'-c',code,str(path)],user=1000,group=1000,
                          extra_groups=[],capture_output=True,timeout=30)
    if result.returncode:raise RuntimeError(result.stderr.decode('utf-8','replace'))
    return result.stdout


def run(args):
    if os.environ.get('MINECRAFT_EULA_ACCEPTED')!='true':
        raise ValueError('Existing platform Minecraft consent required')
    if not args.jar.is_file() or args.jar.is_symlink():raise ValueError('Pinned addon JAR missing')
    addon_bytes=args.jar.read_bytes()
    addon_sha256=hashlib.sha256(addon_bytes).hexdigest()
    inventory=json.loads((args.runtime/'runtime-inventory.json').read_text('utf-8'))
    dependencies=json.loads((args.dependencies/'inventory.json').read_text('utf-8'))
    for entry in dependencies:
        path=args.dependencies/entry['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'],entry['path']
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'tested-addon.jar').write_bytes(addon_bytes)
    records=[]
    with tempfile.TemporaryDirectory(prefix='lab-mage-audit-',dir='/tmp') as temporary:
        root=Path(temporary);root.chmod(0o755)
        workspace=root/'workspace';workspace.mkdir(mode=0o755)
        (workspace/'gradle.properties').write_text('minecraft_version=1.21.11\n')
        session=WorldFixtureSession(workspace,args.output/'world-preparation',300,eula=True,store=args.store)
        try:
            assert session.finish()['status']=='TEMPLATE_READY'
            for sid in args.scenario:
                output=args.output/sid;output.mkdir()
                _,_,fixture=session.launch(['audit-client'],sid,output)
                game=Path(fixture['game_directory'])
                mods=game/'mods';mods.mkdir(mode=0o777);mods.chmod(0o777)
                for entry in dependencies:
                    if entry['path'].startswith('mods/'):
                        shutil.copyfile(args.dependencies/entry['path'],mods/Path(entry['path']).name)
                (mods/'mage-home-under-test.jar').write_bytes(addon_bytes)
                results=game/'test-results';results.mkdir(mode=0o777);results.chmod(0o777)
                natives=game/'natives';natives.mkdir(mode=0o777);natives.chmod(0o777)
                classpath=[str(args.runtime/'client.jar')]
                classpath += [str(args.runtime/e['path']) for e in inventory['files'] if e['path'].startswith('libraries/')]
                classpath += [str(args.dependencies/e['path']) for e in dependencies if e['path'].startswith('loader/')]
                helper=Path('/opt/lab/fixtures/minecraft-client/runtime/dependency-bootstrap.jar')
                journal=root/(uuid.uuid4().hex+'.jsonl');journal.touch();journal.chmod(0o666)
                execution_id='RUN-'+uuid.uuid4().hex
                phases=['seed','reload'] if sid=='persistence-after-exit' else ['normal']
                executions=[]
                for phase in phases:
                    target=output/phase;target.mkdir()
                    previous_reports={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in results.glob('*.xml')}
                    argv=['/opt/java/openjdk/bin/java','-Xms256M','-Xmx2G','-XX:ActiveProcessorCount=2',
                        '-Djava.io.tmpdir='+str(natives),'-Dorg.lwjgl.system.SharedLibraryExtractPath='+str(natives),
                        '-Dfabric.addMods='+str(helper),'-Dlab.client.setup.adapter=altoclef-title-1.21.11-0.95.61',
                        '-Dlab.client.setup.world=lab-fixture',
                        '-Dlab.client.setup.output='+str(game/'client-dependency-preparation.json'),
                        '-Dlab.acceptance.scenario='+sid,'-Dlab.acceptance.fixture.phase='+phase,
                        '-Dlab.acceptance.results.dir='+str(results),
                        '-cp',':'.join(classpath),'net.fabricmc.loader.impl.launch.knot.KnotClient',
                        '--username','LabMageHome','--version','1.21.11',
                        '--uuid','00000000-0000-0000-0000-000000000003','--accessToken','0','--userType','legacy',
                        '--assetsDir',str(args.runtime/'assets'),'--assetIndex',inventory['asset_index'],
                        '--gameDir',str(game),'--width','1280','--height','720']
                    env={'HOME':str(game),'JAVA_TOOL_OPTIONS':'','JDK_JAVA_OPTIONS':'','JAVA_OPTS':'',
                         'LAB_GAMEPLAY_EXECUTION_JOURNAL':str(journal),'LAB_GAMEPLAY_EXECUTION_ID':execution_id}
                    command=recorded_run(argv,game,target/'test.log',args.timeout,scenario_id=sid,env=env)
                    for report in results.glob('*.xml'):
                        if previous_reports.get(report.name)!=hashlib.sha256(report.read_bytes()).hexdigest():
                            shutil.copyfile(report,target/report.name)
                    for name in ('houses.json','lab-persistence-seed.json'):
                        receipt=game/'config'/'mage_home'/name
                        if receipt.is_file() and not receipt.is_symlink():(target/name).write_bytes(client_bytes(receipt))
                    parsed=junit(target)
                    record={'phase':phase,'command':command,'junit':parsed}
                    executions.append(record)
                    (target/'result.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
                    if command['status']!='PASS':break
                shutil.copyfile(journal,output/'gameplay-journal.jsonl')
                setup=game/'client-dependency-preparation.json'
                if setup.is_file():shutil.copyfile(setup,output/setup.name)
                record={'scenario_id':sid,'fixture':fixture,'execution_id':execution_id,'executions':executions}
                records.append(record)
                (output/'scenario-result.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
                (args.output/'gameplay-check.json').write_text(json.dumps({'scenarios':records},indent=2),encoding='utf-8')
                session.release()
        finally:session.close(cancel=True)
    result={'status':'EXECUTION_OBSERVED','addon_sha256':addon_sha256,
            'scenarios':records,'network':'none','host_game_directory_mounted':False,
            'live_project_changed':False,'acceptance_status_changed':False}
    (args.output/'gameplay-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'scenarios':[
        {'id':s['scenario_id'],'commands':[e['command']['status'] for e in s['executions']],
         'cases':[c for e in s['executions'] for r in e['junit'] for c in r['cases']]}
        for s in records]}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,default=Path('/audit/world-client-runtime'))
    parser.add_argument('--dependencies',type=Path,default=Path('/audit/client-dependency-runtime'))
    parser.add_argument('--output',type=Path,default=Path('/output'))
    parser.add_argument('--store',type=Path,default=Path('/world-templates'))
    parser.add_argument('--timeout',type=int,default=420)
    parser.add_argument('--scenario',action='append',default=[])
    arguments=parser.parse_args()
    if not arguments.scenario:arguments.scenario=['union-task-regression']
    run(arguments)
