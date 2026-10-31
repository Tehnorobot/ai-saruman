"""Replace only the dashboard; prove all execution containers stay untouched."""
import hashlib,json,os,subprocess,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=90)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    return result.stdout.strip()
def read(path):
    with urlopen('http://localhost:8080'+path,timeout=30) as response:return response.read()
def containers():
    rows={}
    for line in run(compose,'ps','--all','--format','json').splitlines():
        value=json.loads(line)
        for row in value if isinstance(value,list) else [value]:rows[row['Service']]=row['ID']
    return rows
before=containers();jobs=json.loads(read('/api/jobs'))
backup='codex-lab-service:before-ui-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
run(docker,'image','tag','codex-lab-service:local',backup)
run(docker,'image','tag','codex-lab-service:ui-redesign','codex-lab-service:local')
# Keep the earlier deferred full-stack deployment from restoring the old UI.
run(docker,'image','tag','codex-lab-service:ui-redesign','codex-lab-service:model-catalogue')
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard')
for attempt in range(40):
    try:
        page=read('/').decode('utf-8')
        if '/dashboard.js' in page:break
    except OSError:pass
    time.sleep(.5)
else:raise RuntimeError('New dashboard did not become ready')
after=containers()
assert before['dashboard']!=after['dashboard']
assert {k:v for k,v in before.items() if k!='dashboard'}=={k:v for k,v in after.items() if k!='dashboard'}
for name in ('dashboard.js','dashboard.css','agent-models.js','provider.js','memory.js'):
    assert hashlib.sha256(read('/'+name)).digest()==hashlib.sha256((root/'app'/name).read_bytes()).digest(),name
current={row['id']:row for row in json.loads(read('/api/jobs'))}
for old in jobs:
    new=current[old['id']]
    for key in ('provider','model','reasoning_effort','role_models','resume_generation'):
        assert old.get(key)==new.get(key),key
    if old['status']=='USER_PAUSED':assert new['status']=='USER_PAUSED'
result={'dashboard_updated':True,'execution_containers_unchanged':True,'model_settings_unchanged':True,
        'backup_image':backup,'jobs':[{k:row[k] for k in ('id','status','round')} for row in current.values()]}
folder=root/'audit/dashboard-ui';folder.mkdir(exist_ok=True)
(folder/'deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
