"""Apply already-tested local images without reading .env or resuming a job."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
evidence = root/'audit/runtime-fix-2026-10-21'
evidence.mkdir(exist_ok=True)
docker = Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose = docker.parents[1]/'cli-plugins/docker-compose.exe'
environment = dict(os.environ)
config = Path(tempfile.gettempdir())/'codex-lab-history-cli'
config.mkdir(exist_ok=True)
environment['DOCKER_CONFIG'] = str(config)

def run(args):
    result = subprocess.run(list(map(str,args)), cwd=root, env=environment,
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    with (evidence/'deploy.log').open('a', encoding='utf-8') as output:
        output.write(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError(result.stdout+result.stderr)
    return result.stdout.strip()

def cli(*args):
    return run([docker, '--config', config, *args])

def inspect(service, expression):
    return cli('inspect', 'codex-development-lab-'+service+'-1', '--format', expression)

with sqlite3.connect((root/'data/lab.sqlite3').as_uri()+'?mode=ro', uri=True) as database:
    jobs = database.execute('SELECT id,status FROM jobs').fetchall()
    if any(status not in ('FAILED','USER_PAUSED','STOPPED','FINISHED') for _,status in jobs):
        raise RuntimeError('An active/queued job exists; do not interrupt it')
for part in ('requests','running'):
    if list((root/'agent-tasks'/part).glob('*.json')):
        raise RuntimeError('An active agent task exists')

values = cli('exec','codex-development-lab-worker-1','printenv',
             'MINECRAFT_EULA_ACCEPTED','MAX_AGENT_ROUNDS','QUOTA_RECHECK_SECONDS').splitlines()
if len(values)!=3 or values[0] not in ('true','false') or not all(v.isdigit() for v in values[1:]):
    raise RuntimeError('Cannot preserve worker settings')
environment.update(zip(('MINECRAFT_EULA_ACCEPTED','MAX_AGENT_ROUNDS','QUOTA_RECHECK_SECONDS'),values))
environment.update(AGENT_TIMEOUT_SECONDS='3600', TEST_TIMEOUT_SECONDS='900', MAX_AGENT_TIMEOUT_RETRIES='1')
services = ('dashboard','backup','worker','agent','runner','storage')
before = {name: inspect(name,'{{.Id}}') for name in services}
auth_before = json.loads(inspect('agent','{{json .Mounts}}'))
auth_before = [mount.get('Name') for mount in auth_before if mount['Destination']=='/home/lab/.codex']
for image,service in (('codex-lab-service','dashboard'),('codex-lab-worker','worker'),('codex-lab-runner','runner')):
    cli('image','tag',inspect(service,'{{.Image}}'),image+':before-runtime-fix-20261021')
    cli('image','tag',image+':fix-runtime',image+':local')

run([compose,'--env-file','NUL','up','-d','--no-deps','--no-build','--force-recreate',
     'runner','dashboard','backup','worker','agent'])
after = {name: inspect(name,'{{.Id}}') for name in services}
assert before['storage']==after['storage'], 'Storage was unexpectedly recreated'
auth_after = json.loads(inspect('agent','{{json .Mounts}}'))
auth_after = [mount.get('Name') for mount in auth_after if mount['Destination']=='/home/lab/.codex']
assert auth_before==auth_after and len(auth_after)==1, 'Codex auth volume changed'
result = {'before':before,'after':after,'storage_unchanged':True,'codex_auth_volume_preserved':True,
          'worker_settings':dict(zip(('MINECRAFT_EULA_ACCEPTED','MAX_AGENT_ROUNDS','QUOTA_RECHECK_SECONDS'),values)),
          'agent_timeout_seconds':3600,'timeout_retries':1,'jobs_before':jobs}
(evidence/'deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'services_updated':[name for name in services if before[name]!=after[name]],
                  'codex_auth_volume_preserved':True,'storage_unchanged':True}))
