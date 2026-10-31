"""Apply verified local images, preserving login/settings and terminal jobs."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parents[1]
evidence = root/'audit/performance-security'
docker = Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose = docker.parents[1]/'cli-plugins/docker-compose.exe'
config = Path(tempfile.gettempdir())/'codex-lab-history-cli'
environment = dict(os.environ, DOCKER_CONFIG=str(config))

def run(args, *, log=True):
    result = subprocess.run(list(map(str,args)), cwd=root, env=environment, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=180)
    if log:
        with (evidence/'deployment.log').open('a',encoding='utf-8') as handle:
            handle.write(result.stdout+result.stderr)
    if result.returncode: raise RuntimeError(result.stdout+result.stderr)
    return result.stdout.strip()

def cli(*args, log=True): return run([docker,*args],log=log)
def inspect(service, expression):
    return cli('inspect','codex-development-lab-'+service+'-1','--format',expression,log=False)
def jobs():
    with sqlite3.connect((root/'data/lab.sqlite3').as_uri()+'?mode=ro',uri=True) as connection:
        return connection.execute('SELECT id,status FROM jobs ORDER BY id').fetchall()

before_jobs=jobs()
if any(status not in ('FAILED','USER_PAUSED','STOPPED','FINISHED') for _,status in before_jobs):
    raise RuntimeError('Active/queued jobs exist; safe deployment requires them to be paused first')
for part in ('requests','running'):
    if list((root/'agent-tasks'/part).glob('*.json')): raise RuntimeError('An active model task exists')
assert json.loads((evidence/'benchmark-complete/benchmark.json').read_text())['status']=='PASS'
assert json.loads((evidence/'network/network.json').read_text())['status']=='PASS'
test_log=(root/'audit/performance-linux-tests-final.log').read_bytes()
assert 'OK (skipped=5)' in test_log.decode('utf-16' if test_log.startswith(b'\xff\xfe') else 'utf-8-sig')

defaults={'MINECRAFT_EULA_ACCEPTED':'false','MAX_AGENT_ROUNDS':'5','QUOTA_RECHECK_SECONDS':'900',
          'AGENT_TIMEOUT_SECONDS':'3600','TEST_TIMEOUT_SECONDS':'900','MAX_AGENT_TIMEOUT_RETRIES':'1'}
settings={}
for key,default in defaults.items():
    # Select only public configuration fields inside Engine; never fetch all Env.
    prefix=key+'='
    fmt=('{{range .Config.Env}}{{if ge (len .) '+str(len(prefix))+'}}{{if eq (slice . 0 '+
         str(len(prefix))+') '+json.dumps(prefix)+'}}{{println .}}{{end}}{{end}}{{end}}')
    value=inspect('worker',fmt)
    settings[key]=value[len(prefix):] if value else default
assert settings['MINECRAFT_EULA_ACCEPTED'] in ('true','false')
assert all(value.isdigit() for key,value in settings.items() if key!='MINECRAFT_EULA_ACCEPTED')
environment.update(settings)
ports=json.loads(inspect('dashboard','{{json .HostConfig.PortBindings}}'))
assert len(ports.get('8080/tcp',[]))==1 and ports['8080/tcp'][0]['HostIp']=='127.0.0.1'
environment['DASHBOARD_PORT']=ports['8080/tcp'][0]['HostPort']
run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',root/'audit/validate_effective_isolation.ps1'])
services=('dashboard','backup','worker','agent','runner','storage')
before={name:inspect(name,'{{.Id}}') for name in services}
auth_before=[item['Name'] for item in json.loads(inspect('agent','{{json .Mounts}}'))
             if item['Destination']=='/home/lab/.codex']
for kind,service in (('service','dashboard'),('worker','worker'),('runner','runner')):
    cli('image','tag',inspect(service,'{{.Image}}'),'codex-lab-'+kind+':before-performance-20261021')
for kind in ('service','worker','runner','egress'):
    cli('image','tag','codex-lab-'+kind+':verify-performance','codex-lab-'+kind+':local')

# The scratch proxy must not share its DNS alias with the production proxy.
for name in ('codex-lab-perf-audit-20261021-proxy','codex-lab-perf-audit-20261021-runner'):
    labels=json.loads(cli('inspect',name,'--format','{{json .Config.Labels}}',log=False))
    assert labels.get('codex-lab.audit')=='performance-security'
    cli('stop',name)

run([compose,'--env-file','NUL','up','-d','--no-deps','--no-build','--force-recreate',
     'egress','runner','dashboard','backup','worker','agent'])
# Start the existing storage unchanged: never replace its credential environment.
cli('start','codex-development-lab-storage-1')
for attempt in range(30):
    states={name:json.loads(inspect(name,'{{json .State}}')) for name in services+('egress',)}
    if all(item.get('Running') for item in states.values()) and all(
            states[name].get('Health',{}).get('Status')=='healthy' for name in ('runner','egress')): break
    if attempt==29: raise RuntimeError('Updated services did not become ready')
    time.sleep(1)
after={name:inspect(name,'{{.Id}}') for name in services+('egress',)}
auth_after=[item['Name'] for item in json.loads(inspect('agent','{{json .Mounts}}'))
            if item['Destination']=='/home/lab/.codex']
assert before['storage']==after['storage']
assert auth_before==auth_after and len(auth_after)==1
assert before_jobs==jobs(), 'Deployment unexpectedly changed a terminal job'
result={'before':before,'after':after,'settings_preserved':settings,
        'auth_volume_preserved':True,'storage_container_preserved':True,
        'jobs_preserved':True,'jobs':before_jobs,
        'images':{kind:cli('image','inspect','codex-lab-'+kind+':local','--format','{{.Id}}',log=False)
                  for kind in ('service','worker','runner','egress')}}
(evidence/'deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'updated':[name for name in after if before.get(name)!=after[name]],
                  'auth_volume_preserved':True,'jobs_preserved':True}))
