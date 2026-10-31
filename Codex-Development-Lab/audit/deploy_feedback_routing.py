"""Install tested images while idle and prepare context without invoking a model."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
DOCKER=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
COMPOSE=DOCKER.parents[1]/'cli-plugins/docker-compose.exe'
ENV=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
JID='5babdfba708646'

def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=240)
    if result.returncode:raise RuntimeError(result.stderr[-2000:]+result.stdout[-2000:])
    return result.stdout.strip()

def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=20) as response:return json.load(response)

assert all(j['status'] in ('USER_PAUSED','FAILED','FINISHED','STOPPED') for j in api('jobs'))
assert not list((ROOT/'projects').glob('*/.lab-runner.json'))
for box in ('agent-tasks','planner-tasks'):
    for state in ('requests','running'):assert not list((ROOT/box/state).glob('*.json'))
before=api('jobs/'+JID)
assert before['status']=='USER_PAUSED'
usage=api('jobs/'+JID+'/usage')['totals']
for kind in ('service','worker'):
    run(DOCKER,'image','tag','codex-lab-'+kind+':local','codex-lab-'+kind+':before-feedback-routing')
    run(DOCKER,'image','tag','codex-lab-'+kind+':feedback-routing','codex-lab-'+kind+':local')
run(COMPOSE,'up','-d','--no-deps','--no-build','--force-recreate','runner')
run(COMPOSE,'up','-d','--no-deps','--no-build','--force-recreate','worker','dashboard','agent','agent-dslab','agent-planner')
for attempt in range(40):
    try:
        assert api('jobs/'+JID)['status']=='USER_PAUSED'
        break
    except OSError:time.sleep(.5)
else:raise RuntimeError('Dashboard not ready')

code=r'''import json
from pathlib import Path
from common import acceptance,db
from common.project_lock import project_lock
from worker.round_context import prepare_context
job=db.job_row('5babdfba708646');assert job['status']=='USER_PAUSED'
n=job['round'];ws=Path('/projects')/job['project_id']/'workspace'
root=Path('/artifacts')/job['project_id']/job['id']
report=acceptance.read_json(root/f'round-{n:02d}','verification_complete.json')
source=acceptance.source_fingerprint(ws)
progress=(root/'acceptance-progress.json').read_bytes() if (root/'acceptance-progress.json').exists() else None
with project_lock(ws.parent):context=prepare_context(ws,root,n+1,report)
assert acceptance.source_fingerprint(ws)==source
if progress is not None:assert (root/'acceptance-progress.json').read_bytes()==progress
assert db.job_row(job['id'])['status']=='USER_PAUSED'
feedback=acceptance.read_json(ws/'.lab-feedback','index.json')
texts='\n'.join((ws/item['path']).read_text() for item in feedback['files'])
db.update(job['id'],phase='routing_ready',last_error=None)
db.log(job['id'],'Diagnostic feedback and enforced task rotation installed; context prepared without invoking models. Awaiting explicit resume.')
print(json.dumps({'round':n,'next_round':n+1,'next_action':context['round_action'],
 'directive':context['scheduling_directive'],'feedback_documents':len(feedback['files']),
 'feedback_bytes':feedback['total_bytes'],'feedback_errors':feedback['errors'],
 'movement_trace_available':'Movement.applyInputs' in texts,'source_unchanged':True,
 'progress_unchanged':True,'model_invoked':False,'status':'USER_PAUSED'}))
'''
result=json.loads(run(DOCKER,'exec','codex-development-lab-worker-1','python3','-c',code))
after=api('jobs/'+JID)
assert all(before[k]==after[k] for k in ('status','round','max_rounds','provider','model','resume_generation'))
assert api('jobs/'+JID+'/usage')['totals']==usage
out=ROOT/'audit/feedback-routing';out.mkdir(exist_ok=True)
(out/'deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
