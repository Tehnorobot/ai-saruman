"""Recover one quarantined project after validating the scoped cleanup fix."""
import hashlib,json,os,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
out=root/'audit/cleanup-permissions';out.mkdir(exist_ok=True)
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
jid='5babdfba708646';pid='0c95af0c97dd47'
def run(*args):
    value=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=180)
    if value.returncode:raise RuntimeError('Recovery failed: '+value.stderr[-3000:])
    return value.stdout.strip()
def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=20) as response:return json.load(response)
def idle():
    rows=api('jobs')
    assert all(r['status'] in ('USER_PAUSED','FAILED','FINISHED','STOPPED') for r in rows)
    assert not list((root/'agent-tasks/requests').glob('*.json'))
    assert not list((root/'agent-tasks/running').glob('*.json'))
    assert all(p.parent.name==pid for p in (root/'projects').glob('*/.lab-runner.json'))
    return rows
rows=idle();job=api('jobs/'+jid);assert job['status']=='FAILED' and job['phase']=='worker_exception'
usage=api('jobs/'+jid+'/usage')['totals']
for image,role in (('service','dashboard'),('worker','worker')):
    current=run(docker,'inspect','codex-development-lab-'+role+'-1','--format','{{.Image}}')
    run(docker,'image','tag',current,'codex-lab-'+image+':before-cleanup-permissions')
    run(docker,'image','tag','codex-lab-'+image+':cleanup-permissions','codex-lab-'+image+':local')
idle()
run(compose,'restart','runner')
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard','worker','agent','agent-dslab')
for _ in range(30):
    try:api('jobs/'+jid);break
    except OSError:time.sleep(1)
else:raise RuntimeError('Dashboard not ready')
code='''import hashlib,json,tempfile
from pathlib import Path
from common import acceptance,db,safe_archive
from common.project_lock import project_lock
from worker.sandbox import DockerSandbox
jid='5babdfba708646';pid='0c95af0c97dd47'
root=Path('/projects')/pid;ws=root/'workspace';stage=Path('/artifacts')/pid/jid/'round-01'
job=db.job_row(jid);assert job['status']=='FAILED' and job['phase']=='worker_exception'
with project_lock(root,allow_runner=True):
 marker=json.loads((root/'.lab-runner.json').read_text());token=marker['container']
 before=acceptance.source_fingerprint(ws)
 archive=stage/'pre-cleanup-recovery-workspace.tar'
 if not archive.exists():
  with archive.open('xb') as stream:safe_archive.pack_tree(ws,stream,exclude_top=('.gradle','build','.lab-cache'))
 box=DockerSandbox();info=box.api('GET','/runs/'+token)
 assert info['Config']['Labels']['codex-lab.project']==pid and not info['State']['Running']
 # Preserve actual verifier output before the broker run can be removed.
 with tempfile.TemporaryFile() as snapshot,tempfile.TemporaryDirectory() as directory:
  box.api('GET','/runs/'+token+'/output',download=snapshot)
  safe_archive.unpack_tree(snapshot,Path(directory));box._copy_output(Path(directory),stage)
 logs=box.api('GET','/runs/'+token+'/logs',raw=True)
 (stage/'recovered-container.log').write_bytes(logs)
 # Finishes the recovery journal, then confirms stop/snapshot return/delete.
 box.recover(root)
 assert not (root/'.lab-runner.json').exists() and not (root/'.lab-workspace-swap.json').exists()
 assert not list(root.glob('.lab-previous-*')) and not list(root.glob('.lab-incoming-*'))
 after=acceptance.source_fingerprint(ws);assert before==after
 mod=ws/'libs/unionclef-1.21.11-0.95.61.jar'
 assert hashlib.sha256(mod.read_bytes()).hexdigest()=='76bbb677d2f8294e6d2de98b554f81818ae15605ef8a82ff7e1d3b9b82555d1b'
 report=acceptance.read_json(stage,'verification.json')
 (stage/'recovered_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 failure=next((c for c in report.get('checks',[]) if c.get('status') in ('FAIL','ERROR','TIMEOUT')),None)
 detail=('Recovered verification: '+str(failure.get('label'))+' '+str(failure.get('status'))) if failure else None
 db.update(jid,status='USER_PAUSED',phase='verification_recovered',last_error=detail,
  worker=None,lease_until=0,pause_requested=1,cancel_requested=0)
 db.log(jid,'Восстановлен снимок после ошибки очистки каталогов. Логи сохранены; продолжение сначала повторит проверку кода без нового вызова модели.')
 print(json.dumps({'source_unchanged':before==after,'source_sha256':after,
  'mod_sha256':hashlib.sha256(mod.read_bytes()).hexdigest(),
  'recovered_checks':[{k:c.get(k) for k in ('label','status','exit_code')} for c in report.get('checks',[])],
  'status':db.job_row(jid)['status'],'phase':db.job_row(jid)['phase'],
  'development_checkpoint_exists':(stage/'development_complete.json').exists()}))
'''
result=json.loads(run(docker,'exec','codex-development-lab-worker-1','python3','-c',code))
assert result['development_checkpoint_exists']
for role in ('worker','dashboard','agent-dslab'):
    code="import hashlib;print(hashlib.sha256(open('/opt/lab/common/safe_archive.py','rb').read()).hexdigest())"
    assert run(docker,'exec','codex-development-lab-'+role+'-1','python3','-c',code)==hashlib.sha256((root/'common/safe_archive.py').read_bytes()).hexdigest()
assert api('jobs/'+jid+'/usage')['totals']==usage
assert not list((root/'projects').glob('*/.lab-runner.json'))
after=idle();by_id={r['id']:r for r in after}
for row in rows:
    assert all(by_id[row['id']].get(k)==row.get(k) for k in ('round','max_rounds','model','provider','resume_generation','dslab_token_budget'))
    if row['id']!=jid:assert by_id[row['id']]['status']==row['status']
result.update(paid_usage_unchanged=True,permissions_expanded=False,runner_image_pin_refreshed=True)
(out/'recovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
