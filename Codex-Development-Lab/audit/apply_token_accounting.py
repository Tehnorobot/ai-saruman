"""Update the read-only dashboard, without restarting an active worker or agent."""
import json,os,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))

def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=120)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    return result.stdout.strip()

def agents():
    result={}
    for name in ('worker','agent','agent-dslab','agent-planner','runner'):
        ids=run(compose,'ps','-q',name).splitlines()
        result[name]=[(cid,run(docker,'inspect','--format','{{.State.StartedAt}}',cid)) for cid in ids]
    return result

before=agents()
run(docker,'image','inspect','codex-lab-service:token-accounting','--format','{{.Id}}')
run(docker,'image','tag','codex-lab-service:local','codex-lab-service:before-token-accounting')
run(docker,'image','tag','codex-lab-service:token-accounting','codex-lab-service:local')
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard')
for attempt in range(40):
    try:
        with urlopen('http://localhost:8080/api/jobs',timeout=10) as response:jobs=json.load(response)
        break
    except OSError:time.sleep(.5)
else:raise RuntimeError('Dashboard did not become ready')
after=agents()
assert before==after,'A development container changed while updating dashboard'
report={'installed':True,'image_tag':'token-accounting','development_containers_unchanged':True,
        'model_invoked':False,'jobs':[{'id':j['id'],'status':j['status'],'round':j['round']} for j in jobs]}
(root/'audit/token-accounting-deployment.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
