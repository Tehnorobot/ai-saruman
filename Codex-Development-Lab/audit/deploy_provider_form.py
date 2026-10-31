"""Recreate only the dashboard, retaining the user's Compose .env settings."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
docker = Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose = docker.parents[1]/'cli-plugins/docker-compose.exe'
env = dict(os.environ, DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))

def run(*args):
    result = subprocess.run(list(map(str,args)), cwd=root, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Dashboard update failed: ' + result.stderr[-1000:])
    return result.stdout.strip()

def inspect(role, expression):
    return run(docker, 'inspect', 'codex-development-lab-'+role+'-1', '--format', expression)

roles = ('dashboard','worker','agent','agent-dslab','runner','provider-gateway','storage','backup','egress')
before = {role:inspect(role,'{{.Id}}') for role in roles}
bindings = json.loads(inspect('dashboard','{{json .HostConfig.PortBindings}}'))['8080/tcp']
assert len(bindings)==1 and bindings[0]['HostIp']=='127.0.0.1'
env['DASHBOARD_PORT'] = bindings[0]['HostPort']
run(docker,'image','tag',inspect('dashboard','{{.Image}}'),'codex-lab-service:before-provider-form')
run(docker,'image','tag','codex-lab-service:local','codex-lab-service:provider-form')
# Compose consumes its normal .env internally. Never print its effective config
# or retrieve credentials; the gateway, agents and worker remain untouched.
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard')
expected = hashlib.sha256((root/'app/provider.js').read_bytes()).digest()
for _ in range(30):
    try:
        with urlopen('http://localhost:'+env['DASHBOARD_PORT']+'/provider.js', timeout=3) as response:
            if hashlib.sha256(response.read()).digest()==expected:
                break
    except OSError:
        pass
    time.sleep(1)
else:
    raise RuntimeError('Updated dashboard asset not ready')
after = {role:inspect(role,'{{.Id}}') for role in roles}
assert all(before[role]==after[role] for role in roles if role!='dashboard')
report = {'before':before,'after':after,'only_dashboard_recreated':True,'job_actions_sent':False}
(root/'audit/provider-visibility/deployment.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('Provider form fixed; only dashboard recreated; agents, gateway, worker and jobs preserved.')
