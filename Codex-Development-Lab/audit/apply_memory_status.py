"""Install tested memory reconciliation while idle; leave jobs paused."""
import json,os,subprocess,tempfile
from pathlib import Path
from urllib.request import urlopen
root=Path(__file__).resolve().parents[1]
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=180)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    return result.stdout.strip()
def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=30) as r:return json.load(r)
jobs=api('jobs')
assert all(j['status'] in ('USER_PAUSED','FAILED','STOPPED','FINISHED') for j in jobs)
assert not list((root/'projects').glob('*/.lab-runner.json'))
for box in ('agent-tasks','planner-tasks'):
    for state in ('requests','running'):assert not list((root/box/state).glob('*.json'))
before=api('jobs/5babdfba708646');assert before['status']=='USER_PAUSED'
run(docker,'image','tag','codex-lab-worker:local','codex-lab-worker:before-memory-status')
run(docker,'image','tag','codex-lab-worker:memory-status','codex-lab-worker:local')
run(docker,'image','tag','codex-lab-worker:memory-status','codex-lab-worker:model-catalogue')
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','runner','worker')
# Migrate only controller error history from independent completed reports.
code=r'''
import copy,json
from pathlib import Path
from collections import Counter
from common import db
from common.history import _atomic
from worker.round_context import _apply_report,_read
j=db.job_row('5babdfba708646');assert j['status']=='USER_PAUSED'
root=Path('/artifacts')/j['project_id']/j['id']
memory=_read(root,'round-memory.json');assert memory and memory['schema_version']==1
_atomic(root/'round-memory-before-status-migration.json',memory)
updated=copy.deepcopy(memory)
for folder in sorted(root.glob('round-*')):
    if folder.is_symlink() or not folder.is_dir() or not folder.name[6:].isdigit():continue
    number=int(folder.name[6:])
    if number>j['round']:continue
    report=_read(folder,'verification_complete.json')
    if report is not None:_apply_report(updated,number,report)
assert db.job_row(j['id'])['status']=='USER_PAUSED'
_atomic(root/'round-memory.json',updated)
workspace=Path('/projects')/j['project_id']/'workspace'
assert not workspace.is_symlink()
_atomic(workspace/'round-memory.json',updated)
print(json.dumps(dict(Counter(e['status'] for e in updated['errors']))))
'''
counts=json.loads(run(compose,'exec','-T','worker','python3','-c',code))
after=api('jobs/5babdfba708646')
for key in ('status','phase','round','provider','model','reasoning_effort','role_models','resume_generation'):
    assert before.get(key)==after.get(key),key
result={'installed':True,'status':after['status'],'round':after['round'],'errors':counts,'model_invoked':False}
(root/'audit/memory-status-deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
