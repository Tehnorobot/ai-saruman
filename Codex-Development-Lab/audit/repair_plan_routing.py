"""Install tested routing fix; recheck paused saved code without a paid model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
jid='5babdfba708646'
audit=root/'audit'/'plan-routing'
audit.mkdir(exist_ok=True)

def run(*args,timeout=180):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError('Scoped recovery failed: '+result.stderr[-2000:]+result.stdout[-2000:])
    return result.stdout.strip()

def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=20) as response:
        return json.load(response)

assert all(j['status'] in ('USER_PAUSED','FAILED','FINISHED','STOPPED') for j in api('jobs'))
assert not list((root/'projects').glob('*/.lab-runner.json'))
assert not list((root/'agent-tasks/requests').glob('*.json'))
assert not list((root/'agent-tasks/running').glob('*.json'))
before=api('jobs/'+jid)
assert before['status']=='USER_PAUSED' and before['round']==5
usage=api('jobs/'+jid+'/usage')['totals']

if '--install' in sys.argv:
    for kind in ('service','worker'):
        old=run(docker,'image','inspect','codex-lab-'+kind+':local','--format','{{.Id}}')
        run(docker,'image','tag',old,'codex-lab-'+kind+':before-plan-routing')
        run(docker,'image','tag','codex-lab-'+kind+':plan-routing','codex-lab-'+kind+':local')
    # Broker pins the verified worker image ID at startup; refresh while idle.
    run(compose,'up','-d','--no-deps','--no-build','--force-recreate','runner')
    run(compose,'up','-d','--no-deps','--no-build','--force-recreate','worker','dashboard')
    for attempt in range(40):
        try:
            assert api('jobs/'+jid)['status']=='USER_PAUSED'
            break
        except OSError:
            time.sleep(.5)
    else:
        raise RuntimeError('Dashboard did not become ready')
    print('Installed plan-routing; job remains paused; model not invoked.',flush=True)
    sys.exit(0)

if '--refresh-context' in sys.argv:
    code=r'''import json
from pathlib import Path
from common import db,acceptance
from common.project_lock import project_lock
from worker.round_context import prepare_context
jid='5babdfba708646';job=db.job_row(jid);assert job['status']=='USER_PAUSED' and job['round']==5
ws=Path('/projects')/job['project_id']/'workspace';out=Path('/artifacts')/job['project_id']/jid
previous=json.loads((out/'round-05/verification_complete.json').read_text())
source=acceptance.source_fingerprint(ws)
with project_lock(ws.parent):context=prepare_context(ws,out,6,previous)
assert acceptance.source_fingerprint(ws)==source
assert db.job_row(jid)['status']=='USER_PAUSED'
print(json.dumps({'round':6,'action':context['round_action'],'source_unchanged':True}))
'''
    result=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',code))
    assert api('jobs/'+jid+'/usage')['totals']==usage
    assert api('jobs/'+jid)['status']=='USER_PAUSED'
    task=api('jobs/'+jid+'/round-task')['task']['data']
    assert task['round_action_round']==6
    assert task['round_action']['target']==result['action']['target']
    (audit/'next-round-task.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    sys.exit(0)

assert '--verify' in sys.argv
code=r'''import json,shutil,time
from pathlib import Path
from unittest.mock import patch
from common import db,acceptance
from common.project_lock import project_lock
from worker import engine
from worker.round_context import prepare_context,save_round_snapshot
jid='5babdfba708646';job=db.job_row(jid)
assert job['status']=='USER_PAUSED' and job['round']==5
ws=Path('/projects')/job['project_id']/'workspace'
out=Path('/artifacts')/job['project_id']/jid
stage=out/'round-05'
archive=stage/'verification-retries'/('plan-routing-'+str(time.time_ns()))
archive.mkdir(parents=True)
for path in list(stage.iterdir()):
 if path.name=='verification-retries':continue
 if path.is_dir():shutil.copytree(path,archive/path.name)
 elif path.is_file():shutil.copyfile(path,archive/path.name)
source=acceptance.source_fingerprint(ws)
catalog=acceptance.build_catalog(acceptance.read_spec(ws.parent))
plan=acceptance.load_plan(ws,catalog)
assert not plan['notes'] and len(plan['scenarios'])==3
def control(_jid):
 current=db.job_row(_jid)
 if current['cancel_requested'] or current['status']=='STOPPED':return 'STOPPED'
 return None if current['status']=='USER_PAUSED' else 'USER_PAUSED'
with patch.object(engine,'poll_job',side_effect=control):
 report=engine.checks(ws,stage,jid)
assert acceptance.source_fingerprint(ws)==source
assert not (ws.parent/'.lab-runner.json').exists()
assert not (ws.parent/'.lab-workspace-swap.json').exists()
assert db.job_row(jid)['status']=='USER_PAUSED'
report['manual_reverification']={'reason':'acceptance_plan_routing_fixed','model_ran':False,
 'previous_checkpoint':str(archive.relative_to(out))}
engine.write_json(stage/'verification_complete.json',report)
engine.write_json(out/'latest_verification.json',report)
with project_lock(ws.parent):
 context=prepare_context(ws,out,6,report)
 save_round_snapshot(out,5,'after_verification',ws)
failure=next((c for c in report.get('checks',[]) if c.get('status') in ('FAIL','ERROR','TIMEOUT')),None)
db.update(jid,status='USER_PAUSED',phase='verification_checked',worker=None,lease_until=0,
 last_error=('Verification: '+failure['label']+' '+failure['status']) if failure else None)
db.log(jid,'Saved round 5 independently reverified after acceptance-plan routing fix. No model invocation; job remains paused.')
print(json.dumps({'status':report['status'],
 'checks':[{k:c.get(k) for k in ('label','status','exit_code')} for c in report['checks']],
 'scenarios':[{k:s.get(k) for k in ('id','status','detail')} for s in report.get('acceptance_results',{}).get('scenarios',[])],
 'acceptance':report.get('acceptance',{}).get('summary'),
 'next_action':context['round_action'],
 'source_unchanged':True,'runner_marker_cleared':True,'job_status':db.job_row(jid)['status']}))
'''
result=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',code,timeout=3600))
assert api('jobs/'+jid+'/usage')['totals']==usage
after=api('jobs/'+jid)
assert all(after[k]==before[k] for k in ('status','round','max_rounds','model','provider','resume_generation','dslab_token_budget'))
result.update(paid_usage_unchanged=True,host_access_expanded=False)
(audit/'reverification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
