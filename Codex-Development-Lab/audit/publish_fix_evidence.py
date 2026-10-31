"""Publish the independently built partial mod and its actual build evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

root=Path(__file__).resolve().parents[1]
project='b8fff539803e4d'
job='16c911fc0fbf4d'
workspace=root/'projects'/project/'workspace'
build=root/'audit/saved-mod-check/build-workspace'
destination=root/'artifacts'/project/job/'manual-fix-2026-10-21'
with sqlite3.connect((root/'data/lab.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    assert db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()[0]=='FAILED'
manifest=json.loads((build/'source-manifest.json').read_text(encoding='utf-8-sig'))
for item in manifest:
    assert hashlib.sha256((workspace/item['path']).read_bytes()).hexdigest()==item['sha256'],item['path']
result=json.loads((build/'verification-result.json').read_text(encoding='utf-8'))
assert all(item['exit_code']==0 for item in result['commands'])
assert sum(int(item['tests']) for item in result['junit'])==9
assert all(int(item['failures'])==0 and int(item['errors'])==0 for item in result['junit'])
destination.mkdir(parents=True,exist_ok=True)
for name in ('build.log','dry-run.log','source-manifest.json','verification-result.json'):
    shutil.copy2(build/name,destination/name)
for jar in result['jars']:
    source=build/jar['file']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==jar['sha256']
    target=destination/'builds'/source.name
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(source,target)
    current=workspace/'build/libs'/source.name
    if current.exists():
        previous=root/'audit/runtime-fix-2026-10-21/previous-jars'/current.name
        previous.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(current,previous)
    current.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,current)
for report in (build/'build/test-results/test').glob('TEST-*.xml'):
    target=destination/'test-results'/report.name
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(report,target)
result.update(status='BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',source_files_matched=len(manifest),
    origin='Independent scratch Gradle build of manual source fixes; not an autonomous worker round',
    gameplay_scenarios=[],model_requests=0,
    scope='Lifecycle scaffold only; combat and Saruman integration remain unimplemented')
(destination/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'artifact_directory':str(destination),'unit_tests':9,
                  'jars':[jar['file'] for jar in result['jars']],'gameplay_verified':False}))
