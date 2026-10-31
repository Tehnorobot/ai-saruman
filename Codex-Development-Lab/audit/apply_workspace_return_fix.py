"""Apply the tested transport fix only. Do not resume any model task."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

root=Path(__file__).resolve().parents[1]
evidence=root/'artifacts/94a2c07a7a5b44/89cd3a16ad2c4e/recovery-2026-10-21'
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
environment=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def run(args):
    result=subprocess.run(list(map(str,args)),cwd=root,env=environment,capture_output=True,text=True,timeout=120)
    if result.returncode: raise RuntimeError(result.stdout+result.stderr)
    return result.stdout.strip()
def cli(*args): return run([docker,*args])
def inspect(service,expression):return cli('inspect','codex-development-lab-'+service+'-1','--format',expression)
with sqlite3.connect((root/'data/lab.sqlite3').as_uri()+'?mode=ro',uri=True) as con:
    jobs=con.execute('SELECT id,status FROM jobs ORDER BY id').fetchall()
assert all(status in ('FAILED','USER_PAUSED','STOPPED','FINISHED') for _,status in jobs)
assert all(not list((root/'agent-tasks'/part).glob('*.json')) for part in ('requests','running'))
settings={}
for key in ('MINECRAFT_EULA_ACCEPTED','MAX_AGENT_ROUNDS','QUOTA_RECHECK_SECONDS',
            'AGENT_TIMEOUT_SECONDS','TEST_TIMEOUT_SECONDS','MAX_AGENT_TIMEOUT_RETRIES'):
    prefix=key+'='
    fmt=('{{range .Config.Env}}{{if ge (len .) '+str(len(prefix))+'}}{{if eq (slice . 0 '+
         str(len(prefix))+') '+json.dumps(prefix)+'}}{{println .}}{{end}}{{end}}{{end}}')
    entry=inspect('worker',fmt)
    assert entry.startswith(prefix)
    settings[key]=entry[len(prefix):]
assert settings['MINECRAFT_EULA_ACCEPTED'] in ('true','false')
assert all(value.isdigit() for key,value in settings.items() if key!='MINECRAFT_EULA_ACCEPTED')
environment.update(settings)
before={name:inspect(name,'{{.Id}}') for name in ('agent','dashboard','backup','storage','egress','runner','worker')}
cli('image','tag',inspect('worker','{{.Image}}'),'codex-lab-worker:before-workspace-return-20261021')
cli('image','tag','codex-lab-worker:fix-workspace-return','codex-lab-worker:local')
cli('stop','codex-development-lab-worker-1')
cli('restart','codex-development-lab-runner-1')
run([compose,'--env-file','NUL','up','-d','--no-deps','--no-build','--force-recreate','worker'])
for attempt in range(30):
    state=json.loads(inspect('runner','{{json .State}}'))
    if state.get('Health',{}).get('Status')=='healthy':break
    if attempt==29:raise RuntimeError('Runner did not recover after image pin refresh')
    time.sleep(1)
after={name:inspect(name,'{{.Id}}') for name in before}
assert all(after[name]==before[name] for name in before if name!='worker')
assert inspect('worker','{{.Image}}')==cli('image','inspect','codex-lab-worker:local','--format','{{.Id}}')
with sqlite3.connect((root/'data/lab.sqlite3').as_uri()+'?mode=ro',uri=True) as con:
    assert jobs==con.execute('SELECT id,status FROM jobs ORDER BY id').fetchall()
(evidence/'deployment.json').write_text(json.dumps({'before':before,'after':after,
    'settings_preserved':settings,'jobs_preserved':True,'model_tasks_started':False,
    'worker_image':inspect('worker','{{.Image}}')},indent=2),encoding='utf-8')
print(json.dumps({'worker_updated':True,'runner_image_pin_refreshed':True,'jobs_preserved':True}))
