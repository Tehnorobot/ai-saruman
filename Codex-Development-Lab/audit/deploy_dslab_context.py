"""Install bounded agent context and compatibility input at an idle boundary."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
out=root/'audit/dslab-context';out.mkdir(exist_ok=True)
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
jid='5babdfba708646'

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

before_jobs=idle();usage=api('jobs/'+jid+'/usage')['totals']
roles=('dashboard','worker','agent','agent-dslab','runner','provider-gateway','storage','backup','egress')
before={r:inspect(r,'{{.Id}}') for r in roles}
for image,role in (('service','dashboard'),('worker','worker')):
    run(docker,'image','tag',inspect(role,'{{.Image}}'),'codex-lab-'+image+':before-dslab-context')
    run(docker,'image','tag','codex-lab-'+image+':dslab-context','codex-lab-'+image+':local')
idle()
run(compose,'restart','runner')  # Refresh immutable image pin after retagging.
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard','worker','agent','agent-dslab')
for _ in range(30):
    try:
        state=api('jobs/'+jid)
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('Dashboard not ready')
seed='''import json,hashlib
from pathlib import Path
from common import db,acceptance
from common.project_lock import project_lock
from worker.round_context import prepare_context
from worker.engine import prompt
from agent.dslab import MAX_HISTORY_BYTES,json_size
rows=[]
for jid in ('5babdfba708646','89cd3a16ad2c4e'):
 job=db.job_row(jid);assert job['status']=='USER_PAUSED'
 ws=Path('/projects')/job['project_id']/'workspace';out=Path('/artifacts')/job['project_id']/jid
 out.mkdir(parents=True,exist_ok=True)
 with project_lock(ws.parent):
  before=acceptance.source_fingerprint(ws)
  try:report=acceptance.read_json(out,'latest_verification.json',8*1024*1024)
  except FileNotFoundError:report={}
  context=prepare_context(ws,out,job['round']+1,report)
  request=prompt(ws,job['round']+1,report,context)
  size=json_size([{'role':'user','content':request}]);assert size<MAX_HISTORY_BYTES
  assert acceptance.source_fingerprint(ws)==before
  row={'job':jid,'context_bytes':size,'assigned_task':context.get('current_task'),
   'compatibility_inputs':context.get('compatibility_inputs')}
  if jid=='5babdfba708646':
   data=acceptance.read_json(ws,'compatibility-inputs.json');mod=data['mods'][0]
   assert hashlib.sha256((ws/mod['path']).read_bytes()).hexdigest()==mod['sha256']
   assert context['compatibility_inputs']
   row['mod']=mod
  rows.append(row)
print(json.dumps(rows))
'''
contexts=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',seed))
for path,role in (('agent/dslab.py','agent-dslab'),('worker/engine.py','worker'),
                  ('worker/round_context.py','worker'),('worker/progress_guard.py','worker'),
                  ('common/task_assignment.py','worker'),('app/index.html','dashboard'),('app/main.py','dashboard')):
    code='import hashlib;print(hashlib.sha256(open('+repr('/opt/lab/'+path)+',"rb").read()).hexdigest())'
    assert run(docker,'exec','codex-development-lab-'+role+'-1','python3','-c',code)==hashlib.sha256((root/path).read_bytes()).hexdigest(),path
after_jobs=idle();by_id={r['id']:r for r in after_jobs}
for row in before_jobs:
    assert all(by_id[row['id']].get(k)==row.get(k) for k in ('status','phase','round','max_rounds',
        'model','provider','resume_generation','dslab_token_budget','dslab_max_output_tokens','dslab_max_calls'))
assert api('jobs/'+jid+'/usage')['totals']==usage
acceptance=api('jobs/'+jid+'/acceptance');assert acceptance.get('assignment',{}).get('current_task_id')
after={r:inspect(r,'{{.Id}}') for r in roles}
assert all(before[r]==after[r] for r in ('runner','provider-gateway','storage','backup','egress'))
report={'paid_usage_unchanged':True,'job_status':by_id[jid]['status'],'round':by_id[jid]['round'],
    'budget_unchanged':True,'source_folder_mounted':False,'runner_pin_refreshed':True,
    'contexts':contexts,'before':before,'after':after,'runtime_gameplay_verified':False}
(out/'deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('paid_usage_unchanged','job_status','round','budget_unchanged','contexts')},ensure_ascii=False))
