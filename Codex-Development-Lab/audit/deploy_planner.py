"""Install tested planner while idle; verify saved code without invoking a model."""
import json,os,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import urlopen
root=Path(__file__).resolve().parents[1]
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
out=root/'audit/planner-rollout';out.mkdir(exist_ok=True)
jid='5babdfba708646'
def run(*args,timeout=180):
 result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=timeout)
 if result.returncode:raise RuntimeError('Planner rollout failed: '+result.stderr[-1800:]+result.stdout[-1800:])
 return result.stdout.strip()
def api(path):
 with urlopen('http://localhost:8080/api/'+path,timeout=20) as r:return json.load(r)
assert all(j['status'] in ('USER_PAUSED','FAILED','FINISHED','STOPPED') for j in api('jobs'))
assert not list((root/'projects').glob('*/.lab-runner.json'))
for box in ('agent-tasks','planner-tasks'):
 for state in ('running','requests'):assert not list((root/box/state).glob('*.json'))
before=api('jobs/'+jid);assert before['status']=='USER_PAUSED'
usage=api('jobs/'+jid+'/usage')['totals']
if '--install' in sys.argv:
 for kind in ('service','worker'):
  old=run(docker,'image','inspect','codex-lab-'+kind+':local','--format','{{.Id}}')
  run(docker,'image','tag',old,'codex-lab-'+kind+':before-planner')
  run(docker,'image','tag','codex-lab-'+kind+':planner','codex-lab-'+kind+':local')
 run(compose,'up','-d','--no-deps','--no-build','--force-recreate','runner')
 run(compose,'up','-d','--no-deps','--no-build','--force-recreate','worker','dashboard','agent','agent-dslab','agent-planner')
 for _ in range(40):
  try:
   assert api('jobs/'+jid)['status']=='USER_PAUSED';break
  except OSError:time.sleep(.5)
 else:raise RuntimeError('Dashboard did not become ready')
 probe=r'''import errno,json,os,uuid
from pathlib import Path
from agent import daemon
assert daemon.EXECUTOR_ROLE=='planner'
probe=Path('/projects/0c95af0c97dd47/workspace')/('.planner-write-probe-'+uuid.uuid4().hex)
try:
 probe.write_text('probe')
except OSError as exc:
 assert exc.errno==errno.EROFS,(exc.errno,str(exc))
else:
 probe.unlink();raise RuntimeError('Planner project mount is writable')
assert not Path('/data/lab.sqlite3').exists()
assert not Path('/var/run/docker.sock').exists()
assert not Path('/coder-tasks').exists()
daemon.bootstrap_planner_auth()
assert os.path.lexists(Path(os.environ['CODEX_HOME'])/'auth.json')
print(json.dumps({'projects_read_only':True,'controller_database_unavailable':True,'docker_socket_unavailable':True,'auth_profile_initialized_without_reading_credentials':True}))
'''
 result=json.loads(run(docker,'exec','codex-development-lab-agent-planner-1','python3','-c',probe))
 mounts=json.loads(run(docker,'inspect','codex-development-lab-agent-planner-1','--format','{{json .Mounts}}'))
 dest={m['Destination']:m for m in mounts}
 assert not dest['/projects']['RW']
 assert Path(dest['/agent-tasks']['Source']).name=='planner-tasks'
 assert not dest['/planner-auth']['RW']
 assert dest['/home/lab/.codex']['Name']!=dest['/planner-auth']['Name']
 result.update(separate_mailbox=True,private_auth_profile=True,paid_model_invoked=False)
 assert api('jobs/'+jid+'/usage')['totals']==usage
 (out/'isolation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 print(json.dumps(result));sys.exit(0)

assert '--verify' in sys.argv
code=r'''import json,shutil,time
from pathlib import Path
from unittest.mock import patch
from common import acceptance,db
from common.project_lock import project_lock
from worker import engine
from worker.round_context import prepare_context,save_round_snapshot
jid='5babdfba708646';job=db.job_row(jid);assert job['status']=='USER_PAUSED'
number=job['round'];ws=Path('/projects')/job['project_id']/'workspace'
out=Path('/artifacts')/job['project_id']/jid;stage=out/f'round-{number:02d}'
old=acceptance.read_json(stage,'verification_complete.json')
archive=stage/'verification-retries'/('planner-'+str(time.time_ns()));archive.mkdir(parents=True)
for path in list(stage.iterdir()):
 if path.name=='verification-retries':continue
 if path.is_dir():shutil.copytree(path,archive/path.name)
 elif path.is_file():shutil.copyfile(path,archive/path.name)
source=acceptance.source_fingerprint(ws)
def control(_jid):
 current=db.job_row(_jid)
 if current['cancel_requested'] or current['status']=='STOPPED':return 'STOPPED'
 return None if current['status']=='USER_PAUSED' else 'USER_PAUSED'
with patch.object(engine,'poll_job',side_effect=control):report=engine.checks(ws,stage,jid)
assert acceptance.source_fingerprint(ws)==source
assert not (ws.parent/'.lab-runner.json').exists()
if 'progress_guard' in old:report['progress_guard']=old['progress_guard']
report['manual_reverification']={'reason':'planner_rollout_diagnostics','model_ran':False,'previous_checkpoint':str(archive.relative_to(out))}
engine.write_json(stage/'verification_complete.json',report);engine.write_json(out/'latest_verification.json',report)
with project_lock(ws.parent):
 context=prepare_context(ws,out,number+1,report);save_round_snapshot(out,number,'after_verification',ws)
assert db.job_row(jid)['status']=='USER_PAUSED'
db.update(jid,phase='planner_ready',last_error=None)
db.log(jid,'Planner and human-only checks installed. Saved code reverified without a model; paused until explicit resume.')
print(json.dumps({'round':number,'next_round':number+1,'next_mode':engine.planner.mode(report),'source_unchanged':True,
 'checks':[{k:c.get(k) for k in ('label','status')} for c in report['checks']],
 'scenarios':[{k:s.get(k) for k in ('id','status','detail')} for s in report.get('acceptance_results',{}).get('scenarios',[])],
 'next_action':context['round_action'],'job_status':'USER_PAUSED','model_invoked':False}))
'''
result=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',code,timeout=3600))
assert api('jobs/'+jid+'/usage')['totals']==usage
after=api('jobs/'+jid)
assert all(before[k]==after[k] for k in ('status','round','max_rounds','provider','model','resume_generation'))
assert api('jobs/'+jid+'/planning')['planner']['state']=='missing'
(out/'reverification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
