"""Apply the prepared update only after the user-selected pause/completion boundary."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
out=root/'audit/acceptance'
out.mkdir(exist_ok=True)
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
environment=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def run(*args):
    p=subprocess.run(list(map(str,args)),env=environment,cwd=root,capture_output=True,text=True,timeout=120)
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    return p.stdout.strip()
def cli(*args):return run(docker,*args)
def inspect(name,fmt):return cli('inspect','codex-development-lab-'+name+'-1','--format',fmt)
def jobs():
    with sqlite3.connect((root/'data/lab.sqlite3').as_uri()+'?mode=ro',uri=True) as con:
        return con.execute('SELECT id,project_id,status,phase,round,max_rounds FROM jobs ORDER BY id').fetchall()
def no_verification():
    assert not list((root/'projects').glob('*/.lab-runner.json')),'An isolated verification is active'
    assert not cli('ps','--filter','label=codex-lab.run','--format','{{.ID}}'),'A verification child is active'
before_jobs=jobs()
assert all(j[2] in ('USER_PAUSED','FINISHED','FAILED','STOPPED') for j in before_jobs), 'Wait for user pause or completion'
assert all(not list((root/'agent-tasks'/part).glob('*.json')) for part in ('requests','running')), 'An agent attempt is still active'
no_verification()
settings={}
for key in ('MINECRAFT_EULA_ACCEPTED','MAX_AGENT_ROUNDS','QUOTA_RECHECK_SECONDS',
            'AGENT_TIMEOUT_SECONDS','TEST_TIMEOUT_SECONDS','MAX_AGENT_TIMEOUT_RETRIES'):
    prefix=key+'='
    fmt=('{{range .Config.Env}}{{if ge (len .) '+str(len(prefix))+'}}{{if eq (slice . 0 '+
         str(len(prefix))+') '+json.dumps(prefix)+'}}{{println .}}{{end}}{{end}}{{end}}')
    entry=inspect('worker',fmt);assert entry.startswith(prefix);settings[key]=entry[len(prefix):]
assert settings['MINECRAFT_EULA_ACCEPTED'] in ('true','false')
assert all(v.isdigit() for k,v in settings.items() if k!='MINECRAFT_EULA_ACCEPTED')
bindings=json.loads(inspect('dashboard','{{json .HostConfig.PortBindings}}'))
assert len(bindings['8080/tcp'])==1 and bindings['8080/tcp'][0]['HostIp']=='127.0.0.1'
settings['DASHBOARD_PORT']=bindings['8080/tcp'][0]['HostPort'];environment.update(settings)
before={n:inspect(n,'{{.Id}}') for n in ('dashboard','worker','agent','runner','backup','storage','egress')}
attempts=[p.name for p in (root/'agent-tasks/running').glob('*.json')]
cli('stop','codex-development-lab-worker-1')
assert inspect('worker','{{.State.Running}}')=='false'
try:
    no_verification()  # Mandatory race gate AFTER controller stop.
    assert all(j[2] in ('USER_PAUSED','FINISHED','FAILED','STOPPED') for j in jobs()), 'A job resumed during deployment'
    assert all(not list((root/'agent-tasks'/part).glob('*.json')) for part in ('requests','running')), 'An agent attempt is still active'
except Exception:
    cli('start','codex-development-lab-worker-1')
    raise
for component in ('worker','service'):
    cli('image','tag','codex-lab-'+component+':local','codex-lab-'+component+':before-acceptance-20261022')
    cli('image','tag','codex-lab-'+component+':acceptance','codex-lab-'+component+':local')
cli('restart','codex-development-lab-runner-1')
run(compose,'--env-file','NUL','up','-d','--no-deps','--no-build','--force-recreate','dashboard','worker')
after={n:inspect(n,'{{.Id}}') for n in before}
assert all(before[n]==after[n] for n in before if n not in ('dashboard','worker'))
assert {(j[0],j[1],j[5]) for j in jobs()}=={(j[0],j[1],j[5]) for j in before_jobs}
for name,image in (('dashboard','service'),('worker','worker')):
    assert inspect(name,'{{.Image}}')==cli('image','inspect','codex-lab-'+image+':acceptance','--format','{{.Id}}')
(out/'deployment.json').write_text(json.dumps({'before':before,'after':after,'jobs_before':before_jobs,
    'jobs_after':jobs(),'active_attempts_preserved':attempts,'agent_container_preserved':True,
    'job_actions_sent':False,'manual_db_changes':False},indent=2),encoding='utf-8')
print('Dashboard and controller upgraded at an idle boundary; agent container and saved jobs preserved')
