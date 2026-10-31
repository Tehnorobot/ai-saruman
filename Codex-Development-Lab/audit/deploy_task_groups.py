"""Apply grouped tasks at an idle boundary, preserving source and paid usage."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
out=root/'audit/task-groups';out.mkdir(exist_ok=True)
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
jid='89cd3a16ad2c4e'

def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=120)
    if result.returncode:raise RuntimeError('Update/check failed: '+result.stderr[-1000:])
    return result.stdout.strip()

def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=20) as response:return json.load(response)

def idle():
    rows=api('jobs')
    assert all(r['status'] in ('USER_PAUSED','FINISHED','FAILED','STOPPED') for r in rows)
    assert not list((root/'agent-tasks/requests').glob('*.json'))
    assert not list((root/'agent-tasks/running').glob('*.json'))
    assert not list((root/'projects').glob('*/.lab-runner.json'))
    return rows

def inspect(role,fmt):return run(docker,'inspect','codex-development-lab-'+role+'-1','--format',fmt)

roles=('dashboard','worker','agent','agent-dslab','runner','provider-gateway','storage','backup','egress')
before_jobs=idle();usage=api('jobs/'+jid+'/usage')['totals']
before_state=api('jobs/'+jid+'/acceptance')
before={r:inspect(r,'{{.Id}}') for r in roles}
for image,role in (('service','dashboard'),('worker','worker')):
    run(docker,'image','tag',inspect(role,'{{.Image}}'),'codex-lab-'+image+':before-task-groups')
    run(docker,'image','tag','codex-lab-'+image+':task-groups','codex-lab-'+image+':local')
idle()
run(compose,'restart','runner')  # Refresh immutable worker image ID pin.
# Normal Compose consumes .env internally. Never read/print its credentials.
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard','worker','agent','agent-dslab')
for _ in range(30):
    try:
        state=api('jobs/'+jid+'/acceptance')
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('Acceptance API not ready')
assert len(state['tasks'])==29 and len(state['requirements'])==240
assert state['summary']==before_state['summary']
assert state['history_summary']==before_state['history_summary']
assert {r['id']:r['status'] for r in state['requirements']}=={r['id']:r['status'] for r in before_state['requirements']}
seed='''import json
from pathlib import Path
from common import db,acceptance
from common.project_lock import project_lock
from worker.round_context import prepare_context
job=db.job_row("89cd3a16ad2c4e");assert job['status']=='USER_PAUSED'
ws=Path('/projects')/job['project_id']/'workspace'
out=Path('/artifacts')/job['project_id']/job['id']
with project_lock(ws.parent):
 before=acceptance.source_fingerprint(ws)
 report=acceptance.read_json(out,'latest_verification.json',8*1024*1024)
 context=prepare_context(ws,out,job['round']+1,report)
 document=acceptance.read_json(ws,'acceptance-tasks.json')
 assert acceptance.source_fingerprint(ws)==before
 ids=[rid for task in document['tasks'] for rid in task['requirement_ids']]
 assert len(ids)==len(set(ids))==240 and len(document['tasks'])==29
 assert len(context['task_index'])==29 and context['task_index_omitted']==0
 print(json.dumps({'tasks':document['tasks'],'source_sha256':before,
  'agent_context_groups':len(context['task_index']),'focus_task_ids':context['focus_task_ids'],
  'upcoming_scenarios':context['upcoming_scenarios']}))
'''
context=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',seed))
(out/'task-map.json').write_text(json.dumps(context,ensure_ascii=False,indent=2),encoding='utf-8')
for path in ('app/index.html','app/main.py','common/acceptance_tasks.py','common/acceptance.py',
             'worker/engine.py','worker/round_context.py'):
    role='dashboard' if path.startswith(('app/','common/')) else 'worker'
    code='import hashlib;print(hashlib.sha256(open('+repr('/opt/lab/'+path)+',"rb").read()).hexdigest())'
    assert run(docker,'exec','codex-development-lab-'+role+'-1','python3','-c',code)==hashlib.sha256((root/path).read_bytes()).hexdigest(),path
with urlopen('http://localhost:8080/',timeout=15) as response:
    assert response.read()==(root/'app/index.html').read_text(encoding='utf-8').encode()
assert api('jobs/'+jid+'/usage')['totals']==usage
after_jobs=idle();by_id={r['id']:r for r in after_jobs}
for row in before_jobs:
    assert all(by_id[row['id']].get(k)==row.get(k) for k in
        ('status','phase','round','max_rounds','model','reasoning_effort','resume_generation','provider'))
after={r:inspect(r,'{{.Id}}') for r in roles}
assert all(before[r]==after[r] for r in ('runner','provider-gateway','storage','backup','egress'))
result={'paid_usage_unchanged':True,'job_status':by_id[jid]['status'],'round':by_id[jid]['round'],
 'runner_pin_refreshed':True,'source_requirements_unchanged':True,'summary':state['summary'],
 'task_summary':state['task_summary'],'agent_context_groups':context['agent_context_groups'],
 'source_sha256':context['source_sha256'],'before':before,'after':after}
(out/'deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:result[k] for k in ('paid_usage_unchanged','job_status','round','summary','task_summary','agent_context_groups')},ensure_ascii=False))
