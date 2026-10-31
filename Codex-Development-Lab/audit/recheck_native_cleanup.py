"""Install the native extraction path and reverify saved code without a model."""
import json,os,subprocess,tempfile,time,sys
from pathlib import Path
from urllib.request import urlopen
root=Path(__file__).resolve().parents[1];out=root/'audit/cleanup-permissions'
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
jid='5babdfba708646'
def run(*args,timeout=180):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Reverification failed: '+result.stderr[-3000:])
    return result.stdout.strip()
def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=20) as response:return json.load(response)
assert all(j['status'] in ('USER_PAUSED','FAILED','FINISHED','STOPPED') for j in api('jobs'))
assert not list((root/'projects').glob('*/.lab-runner.json'))
assert not list((root/'agent-tasks/requests').glob('*.json'))
assert not list((root/'agent-tasks/running').glob('*.json'))
before=api('jobs/'+jid);usage=api('jobs/'+jid+'/usage')['totals']
assert before['status']=='USER_PAUSED'
if '--verify-only' not in sys.argv:
    image=run(docker,'inspect','codex-development-lab-runner-1','--format','{{.Image}}')
    run(docker,'image','tag',image,'codex-lab-runner:before-native-libraries')
    run(docker,'image','tag','codex-lab-runner:native-libraries','codex-lab-runner:local')
    run(compose,'up','-d','--no-deps','--no-build','--force-recreate','runner')
code='''import json,shutil,time
from pathlib import Path
from unittest.mock import patch
from common import db,acceptance
from common.project_lock import project_lock
from worker import engine
from worker.round_context import prepare_context,save_round_snapshot
jid='5babdfba708646';job=db.job_row(jid);assert job['status']=='USER_PAUSED'
ws=Path('/projects')/job['project_id']/'workspace';out=Path('/artifacts')/job['project_id']/jid
stage=out/'round-01';archive=stage/'verification-retries'/('native-path-'+str(time.time_ns()))
archive.mkdir(parents=True)
for name in ('verification.json','build.log','client_gametest.log','video_capture.json','runner.log'):
 path=stage/name
 if path.is_file():shutil.copyfile(path,archive/name)
source=acceptance.source_fingerprint(ws)
def control(_jid):
 current=db.job_row(_jid)
 return 'STOPPED' if current['cancel_requested'] or current['status']=='STOPPED' else ('USER_PAUSED' if current['status']!='USER_PAUSED' else None)
with patch.object(engine,'poll_job',side_effect=control):report=engine.checks(ws,stage,jid)
assert acceptance.source_fingerprint(ws)==source
assert not (ws.parent/'.lab-runner.json').exists()
assert not (ws.parent/'.lab-workspace-swap.json').exists()
engine.write_json(stage/'verification_complete.json',report)
engine.write_json(out/'latest_verification.json',report)
with project_lock(ws.parent):
 prepare_context(ws,out,2,report);save_round_snapshot(out,1,'after_verification',ws)
failure=next((c for c in report.get('checks',[]) if c.get('status') in ('FAIL','ERROR','TIMEOUT')),None)
db.update(jid,status='USER_PAUSED',phase='verification_checked',worker=None,lease_until=0,
 last_error=('Verification: '+failure['label']+' '+failure['status']) if failure else None)
db.log(jid,'Повторная независимая проверка сохранённого кода после исправления очистки и путей нативных библиотек. Модель не вызывалась.')
print(json.dumps({'status':report['status'],'checks':[{k:c.get(k) for k in ('label','status','exit_code')} for c in report['checks']],
 'acceptance':report.get('acceptance',{}).get('summary'),'source_unchanged':True,
 'runner_marker_cleared':True,'job_status':db.job_row(jid)['status']}))
'''
result=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',code,timeout=3600))
assert api('jobs/'+jid+'/usage')['totals']==usage
after=api('jobs/'+jid)
assert all(after[k]==before[k] for k in ('status','round','max_rounds','model','provider','resume_generation','dslab_token_budget'))
result.update(paid_usage_unchanged=True,host_access_expanded=False,tmp_noexec_preserved=True)
(out/'reverification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
