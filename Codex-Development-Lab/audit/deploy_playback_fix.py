"""Update only the dashboard; leave agents, workers, runner and jobs untouched."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
image_tag=sys.argv[1] if len(sys.argv)>1 else 'fix-playback'
assert image_tag in ('fix-playback','acceptance','activity')
out=root/'audit'/('playback-inspection' if image_tag=='fix-playback' else image_tag)
out.mkdir(exist_ok=True)
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def run(*args):
    p=subprocess.run(list(map(str,args)),env=env,cwd=root,capture_output=True,text=True,timeout=120)
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    return p.stdout.strip()
def inspect(name,fmt):return run(docker,'inspect','codex-development-lab-'+name+'-1','--format',fmt)
before={name:inspect(name,'{{.Id}}') for name in ('dashboard','worker','agent','runner','storage','backup','egress')}
bindings=json.loads(inspect('dashboard','{{json .HostConfig.PortBindings}}'))
assert len(bindings['8080/tcp'])==1 and bindings['8080/tcp'][0]['HostIp']=='127.0.0.1'
env['DASHBOARD_PORT']=bindings['8080/tcp'][0]['HostPort']
run(docker,'image','tag',inspect('dashboard','{{.Image}}'),'codex-lab-service:before-'+image_tag+'-dashboard')
run(docker,'image','tag','codex-lab-service:'+image_tag,'codex-lab-service:local')
run(compose,'--env-file','NUL','up','-d','--no-deps','--no-build','--force-recreate','dashboard')
after={name:inspect(name,'{{.Id}}') for name in before}
assert all(after[name]==before[name] for name in before if name!='dashboard')
assert inspect('dashboard','{{.Image}}')==run(docker,'image','inspect','codex-lab-service:'+image_tag,'--format','{{.Id}}')
(out/('deployment.json' if image_tag=='fix-playback' else 'dashboard-deployment.json')).write_text(json.dumps({'before':before,'after':after,
    'only_dashboard_recreated':True,'models_started':False},indent=2),encoding='utf-8')
print('Dashboard updated; all other containers preserved; no job actions sent')
