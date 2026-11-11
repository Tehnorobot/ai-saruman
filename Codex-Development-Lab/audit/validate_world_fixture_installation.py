"""Read-only installed-state receipt; no jobs, models or game sessions start."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
tag='world-fixture-v6'

def command(*args):
    return subprocess.check_output([str(docker),*args],cwd=root,env=env,text=True,timeout=30).strip()

def read(path):
    return json.loads((root/path).read_text(encoding='utf-8'))

def tests(path,count):
    raw=(root/path).read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'Ran (\d+) tests in ([0-9.]+)s',text)
    assert match and int(match[1])==count and re.search(r'^OK(?: \(skipped=\d+\))?\s*$',text,re.M),path
    assert not re.search(r'^FAILED ',text,re.M),path
    skipped=re.search(r'OK \(skipped=(\d+)\)',text)
    return {'count':count,'skipped':int(skipped[1]) if skipped else 0,
            'seconds':float(match[2]),'log':path,'status':'OK'}

deployment=read('audit/'+tag+'-deployment.json')
assert deployment['installed'] and deployment['jobs_unchanged'] and not deployment['model_invoked']
images={}
for service,family in [('worker','worker'),('dashboard','service'),('runner','runner')]:
    expected=command('image','inspect','codex-lab-'+family+':'+tag,'--format','{{.Id}}')
    actual=command('inspect','codex-development-lab-'+service+'-1','--format','{{.Image}}')
    assert expected==actual,(service,expected,actual)
    images[service]=actual
source=command('exec','codex-development-lab-worker-1','python3','-c',
    "from common.acceptance import source_fingerprint; from pathlib import Path; print(source_fingerprint(Path('/projects/7aa2e3592f1640/workspace')))")
assert source=='1f32c0ff9e8be9f29395d2f807e2a476b9df16825710459e3c1ea11cda948237','Mod sources changed during installation'
with urlopen('http://localhost:8080/api/jobs',timeout=20) as response:jobs=json.load(response)
assert all(j['status'] in ('USER_PAUSED','FAILED','FINISHED','STOPPED') for j in jobs)
job=next(j for j in jobs if j['id']=='69496be60a264a')
assert job['status']=='USER_PAUSED' and job['round']==3 and job['last_error'] is None
for mailbox in ('agent-tasks','planner-tasks'):
    for state in ('requests','running'):assert not list((root/mailbox/state).glob('*.json'))
assert not list((root/'projects').glob('*/.lab-runner.json'))
clients=read('audit/world-fixture-smoke/client-smoke-v3/client-smoke.json')
assert clients['status']=='CLIENT_WORLD_OBSERVED' and len(clients['runs'])==2
assert clients['protected_template_access_denied'] and clients['network']=='none'
assert all(r['player_joined'] and r['normal_exit_code']==0 for r in clients['runs'])
assert clients['runs'][0]['fixture']['run_id']!=clients['runs'][1]['fixture']['run_id']
assert not clients['host_game_directory_mounted'] and not clients['product_tests_executed']
loom=read('audit/world-fixture-smoke/actual-loom-smoke-v2/actual-loom-smoke.json')
assert loom['status']=='ACTUAL_LOOM_LAUNCH_CONTRACT_OBSERVED' and not loom['live_cache_written']
recovery=read('audit/world-fixture-smoke/cancel-smoke/cancel-smoke.json')
assert recovery['generator_did_not_survive'] and recovery['partial_template_not_published']
assert recovery['fresh_generation_recovered'] and recovery['warm_reused']
warm=read('audit/world-fixture-smoke/installed-prewarm/warm-v6/world-fixture.json')
assert warm['status']=='TEMPLATE_READY' and warm['template']['reused']
result={'installed':True,'image_tag':tag,'images':images,
    'full_suite':tests('audit/world-fixture-image-tests-v6-final.txt',1503),
    'worker_suite':tests('audit/world-fixture-worker-tests-v6-final.txt',79),
    'broker_suite':tests('audit/world-fixture-broker-tests-v4.txt',25),
    'real_client':clients,'real_loom':loom,'real_generator_crash_recovery':recovery,
    'installed_template_ready':True,'installed_template_key':warm['template']['key'],
    'project_sources_unchanged':True,'project_source_sha256':source,
    'current_job':{k:job[k] for k in ('id','project_id','status','round','phase')},
    'all_jobs_idle':True,'jobs_resumed':False,'new_llm_roles':False,'model_invoked':False,
    'env_or_credentials_changed':False,'game_host_folder_mounted':False,
    'scope_note':'World infrastructure validated. Mage Home gameplay acceptance still requires a resumed independent scenario.'}
target=root/'audit/world-fixture-validation.json'
target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:result[k] for k in ('installed','image_tag','full_suite','worker_suite','broker_suite','installed_template_ready','project_sources_unchanged','current_job')},ensure_ascii=False))
