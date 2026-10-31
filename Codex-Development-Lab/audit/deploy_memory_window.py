"""Install memory viewer at an idle boundary and reload the broker image pin."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1];out=root/'audit/memory-window'
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=120)
    if result.returncode:raise RuntimeError('Update/check failed: '+result.stderr[-1000:])
    return result.stdout.strip()
def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=15) as response:return json.load(response)
def idle():
    rows=api('jobs')
    assert all(r['status'] in ('USER_PAUSED','FINISHED','FAILED','STOPPED') for r in rows)
    assert not list((root/'agent-tasks/requests').glob('*.json'))
    assert not list((root/'agent-tasks/running').glob('*.json'))
    assert not list((root/'projects').glob('*/.lab-runner.json'))
    return rows
def inspect(role,fmt):return run(docker,'inspect','codex-development-lab-'+role+'-1','--format',fmt)
roles=('dashboard','worker','agent','agent-dslab','runner','provider-gateway','storage','backup','egress')
before_jobs=idle();usage=api('jobs/89cd3a16ad2c4e/usage')['totals'];before={r:inspect(r,'{{.Id}}') for r in roles}
for image,role in (('service','dashboard'),('worker','worker')):
    run(docker,'image','tag',inspect(role,'{{.Image}}'),'codex-lab-'+image+':before-memory-window')
    run(docker,'image','tag','codex-lab-'+image+':memory-window','codex-lab-'+image+':local')
idle()
# Required even when its container ID is unchanged: broker pins image at startup.
run(compose,'restart','runner')
# Compose reads normal .env internally; no credentials are read or printed here.
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard','worker','agent','agent-dslab')
for attempt in range(30):
    try:
        overview=api('jobs/89cd3a16ad2c4e/memory')
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('Memory API did not become ready')
seed='''import json
from pathlib import Path
from common import db,acceptance
from common.project_lock import project_lock
from worker.round_context import prepare_context,save_round_snapshot
job=db.job_row('89cd3a16ad2c4e');assert job['status']=='USER_PAUSED'
ws=Path('/projects')/job['project_id']/'workspace';out=Path('/artifacts')/job['project_id']/job['id']
with project_lock(ws.parent):
 before=acceptance.source_fingerprint(ws)
 report=json.loads((out/'latest_verification.json').read_text())
 assert report['status']=='BUILD_VERIFIED_GAMEPLAY_UNVERIFIED'
 context=prepare_context(ws,out,job['round']+1,report)
 save_round_snapshot(out,job['round'],'after_verification',ws)
 assert acceptance.source_fingerprint(ws)==before
 print(json.dumps({'source_sha256':before,'open_error_count':context['open_error_count']}))
'''
context=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',seed))
for path in ('app/memory.js','app/memory.css','common/memory_view.py','worker/round_context.py','worker/engine.py'):
    role='dashboard' if path.startswith(('app/','common/')) else 'worker'
    code='import hashlib;print(hashlib.sha256(open('+repr('/opt/lab/'+path)+',"rb").read()).hexdigest())'
    assert run(docker,'exec','codex-development-lab-'+role+'-1','python3','-c',code)==hashlib.sha256((root/path).read_bytes()).hexdigest(),path
for name in ('memory.js','memory.css'):
    with urlopen('http://localhost:8080/'+name,timeout=10) as response:
        assert response.read()==(root/'app'/name).read_bytes()
detail=api('jobs/89cd3a16ad2c4e/memory/rounds/27')
assert detail['memory']['state']=='available'
assert detail['memory']['data']['snapshot_phase']=='after_verification'
assert detail['acceptance']['data']['summary']['test_passed']==4
assert api('jobs/89cd3a16ad2c4e/usage')['totals']==usage
after_jobs=idle();by_id={r['id']:r for r in after_jobs}
for row in before_jobs:
    assert all(by_id[row['id']].get(k)==row.get(k) for k in ('status','round','model','resume_generation','provider'))
job=by_id['89cd3a16ad2c4e'];assert job['phase']=='verification_checked' and job['last_error'] is None
after={r:inspect(r,'{{.Id}}') for r in roles}
assert all(before[r]==after[r] for r in ('runner','provider-gateway','storage','backup','egress'))
report={'before':before,'after':after,'job_status':job['status'],'job_phase':job['phase'],
        'last_error':job['last_error'],'paid_usage_unchanged':True,'runner_restarted_to_refresh_pinned_image':True,
        'round27_acceptance':detail['acceptance']['data']['summary'],'context':context}
(out/'deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('job_status','job_phase','last_error','round27_acceptance','context')},ensure_ascii=False))
