"""Apply the acceptance controller update only at an idle boundary."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
out=root/'audit/acceptance-progress';out.mkdir(exist_ok=True)
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
before={r:inspect(r,'{{.Id}}') for r in roles}
for image,role in (('service','dashboard'),('worker','worker')):
    run(docker,'image','tag',inspect(role,'{{.Image}}'),'codex-lab-'+image+':before-acceptance-progress')
    run(docker,'image','tag','codex-lab-'+image+':acceptance-progress','codex-lab-'+image+':local')
idle()
# The broker pins the worker image ID at startup; refresh this security pin.
run(compose,'restart','runner')
# Consume normal .env internally without reading or displaying credentials.
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard','worker','agent','agent-dslab')
for _ in range(30):
    try:
        state=api('jobs/'+jid+'/acceptance')
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('Updated acceptance API not ready')

seed='''import json
from pathlib import Path
from common import db,acceptance,acceptance_progress
from common.project_lock import project_lock
from worker.engine import refresh_acceptance_queue,verification_only_ready
job=db.job_row("89cd3a16ad2c4e");assert job['status']=='USER_PAUSED'
ws=Path('/projects')/job['project_id']/'workspace'
out=Path('/artifacts')/job['project_id']/job['id']
with project_lock(ws.parent):
 source=acceptance.source_fingerprint(ws)
 catalog=acceptance.build_catalog(acceptance.read_spec(ws.parent));plan=acceptance.load_plan(ws,catalog)
 ledger=acceptance_progress.load_progress(out,catalog,plan)
 acceptance_progress.save_progress(out,ledger)
 request=acceptance_progress.select_request(catalog,plan,ledger,source)
 assert acceptance.source_fingerprint(ws)==source
report=json.loads((out/f"round-{job['round']:02d}"/'verification_complete.json').read_text())
refresh_acceptance_queue(ws,out,report)
assert verification_only_ready(ws,report)
print(json.dumps({'next_scenarios':[s['id'] for s in request['scenarios']],
 'queue':report['acceptance']['queue_summary'],'next_round_verification_only':True,
 'source_sha256':source}))
'''
migration=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',seed))
state=api('jobs/'+jid+'/acceptance')
assert state['history_summary']['test_passed']>=8
assert migration['queue']['unseen']>=9
for path in ('app/index.html','app/main.py','app/memory.js','common/acceptance_progress.py',
             'common/memory_view.py','worker/engine.py','worker/progress_guard.py','worker/acceptance_runner.py'):
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
 'runner_pin_refreshed':True,'summary':state['summary'],'history_summary':state['history_summary'],
 'migration':migration,'before':before,'after':after}
(out/'deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:result[k] for k in ('paid_usage_unchanged','job_status','round','summary','history_summary','migration')},ensure_ascii=False))
