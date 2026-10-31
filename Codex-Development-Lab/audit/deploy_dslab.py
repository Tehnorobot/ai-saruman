"""Apply only at an idle boundary. Never read .env or start a paid model request."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
out = root / 'audit/dslab'
docker = Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose = docker.parents[1] / 'cli-plugins/docker-compose.exe'
environment = dict(os.environ, DOCKER_CONFIG=str(Path(tempfile.gettempdir()) / 'codex-lab-history-cli'))
environment.update(DSLAB_API_KEY='', DSLAB_CONTROL_TOKEN='')


def run(*args, timeout=120):
    result = subprocess.run(list(map(str, args)), cwd=root, env=environment, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout[-2000:] + result.stderr[-2000:])
    return result.stdout.strip()


def api(path, method='GET'):
    with urlopen(Request('http://localhost:8080' + path, method=method), timeout=10) as response:
        return json.load(response)


def assert_idle():
    jobs = api('/api/jobs')
    assert all(job['status'] in ('USER_PAUSED', 'QUOTA_PAUSED', 'FINISHED', 'FAILED', 'STOPPED') for job in jobs), 'Active job: update deferred'
    assert not list((root/'agent-tasks/requests').glob('*.json')), 'Pending model request: update deferred'
    assert not list((root/'agent-tasks/running').glob('*.json')), 'Model executor still running: update deferred'
    assert not list((root/'projects').glob('*/.lab-runner.json')), 'Build runner active: update deferred'
    return jobs


before_jobs = assert_idle()
# A quota pause can resume automatically. Convert only these already-idle jobs
# to an explicit pause in a fenced transaction; an automatic claim winning
# this race aborts the update instead of requesting interruption of a live job.
quota_jobs = [job['id'] for job in before_jobs if job['status'] == 'QUOTA_PAUSED']
pause_script = '''from common import db
with db.connect() as connection:
 connection.execute('BEGIN IMMEDIATE')
 rows=connection.execute('SELECT id,status FROM jobs').fetchall()
 assert all(row['status'] in ('QUOTA_PAUSED','USER_PAUSED','FINISHED','FAILED','STOPPED') for row in rows), 'Active job: update deferred'
 for jid in ''' + repr(quota_jobs) + ''':
  cursor=connection.execute("UPDATE jobs SET status='USER_PAUSED',pause_requested=1,updated_at=? WHERE id=? AND status='QUOTA_PAUSED'",(db.utc(),jid))
  assert cursor.rowcount==1, 'Job changed state: update deferred'
  db.event(connection,jid,'На паузе для обновления провайдера; исходники и раунды сохранены.')
'''
run(docker, 'exec', 'codex-development-lab-worker-1', 'python3', '-c', pause_script)
assert all(job['status'] != 'QUOTA_PAUSED' for job in assert_idle())
names = ('dashboard', 'worker', 'agent', 'runner', 'backup', 'storage', 'egress')
def inspect(name, fmt):
    return run(docker, 'inspect', 'codex-development-lab-' + name + '-1', '--format', fmt)
before = {name: inspect(name, '{{.Id}}') for name in names}
bindings = json.loads(inspect('dashboard', '{{json .HostConfig.PortBindings}}'))
assert bindings['8080/tcp'][0]['HostIp'] == '127.0.0.1'
environment['DASHBOARD_PORT'] = bindings['8080/tcp'][0]['HostPort']
# Preserve only public, previously authorized runtime settings. No credential
# storage, whole container environment, or actual .env is read.
public = ('MINECRAFT_EULA_ACCEPTED', 'QUOTA_RECHECK_SECONDS', 'MAX_AGENT_ROUNDS',
          'AGENT_TIMEOUT_SECONDS', 'TEST_TIMEOUT_SECONDS', 'MAX_AGENT_TIMEOUT_RETRIES')
probe = 'import json,os;print(json.dumps({k:os.getenv(k) for k in ' + repr(public) + '}))'
settings = json.loads(run(docker, 'exec', 'codex-development-lab-worker-1', 'python3', '-c', probe))
environment.update({name: value for name, value in settings.items() if value is not None})
for image, service in (('worker', 'worker'), ('service', 'dashboard')):
    rollback = 'codex-lab-' + image + ':before-dslab'
    try:
        run(docker, 'image', 'inspect', rollback, '--format', '{{.Id}}')
    except RuntimeError:
        run(docker, 'image', 'tag', inspect(service, '{{.Image}}'), rollback)
    run(docker, 'image', 'tag', 'codex-lab-' + image + ':local', 'codex-lab-' + image + ':dslab')
run(docker, 'image', 'tag', 'codex-lab-provider:local', 'codex-lab-provider:dslab')
assert_idle()
backup = "import pathlib,sqlite3;p=pathlib.Path('/data/lab.before-dslab.sqlite3');\nif not p.exists():\n s=sqlite3.connect('/data/lab.sqlite3');d=sqlite3.connect(p);s.backup(d);d.close();s.close()"
run(docker, 'exec', 'codex-development-lab-worker-1', 'python3', '-c', backup)
run(compose, '--env-file', 'NUL', 'stop', 'worker', 'agent')
assert_idle()
run(compose, '--env-file', 'NUL', 'up', '-d', '--no-deps', '--no-build', 'init-permissions')
assert run(docker, 'wait', 'codex-development-lab-init-permissions-1') == '0'
run(compose, '--env-file', 'NUL', 'up', '-d', '--no-deps', '--no-build', '--force-recreate',
    'provider-gateway', 'dashboard', 'agent', 'agent-dslab', 'worker')
for _ in range(30):
    try:
        after_jobs = api('/api/jobs')
        if all('provider' in job for job in after_jobs):
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise RuntimeError('Updated dashboard did not become ready')
after = {name: inspect(name, '{{.Id}}') for name in names}
assert all(after[name] == before[name] for name in ('runner', 'backup', 'storage', 'egress'))
by_id = {job['id']: job for job in after_jobs}
assert all(by_id[job['id']]['round'] == job['round'] for job in before_jobs)
assert all(by_id[job['id']]['status'] == ('USER_PAUSED' if job['status'] == 'QUOTA_PAUSED' else job['status']) for job in before_jobs)
result = {'before': before, 'after': after, 'models_started': False,
          'actual_env_read': False, 'credentials_configured': False,
          'jobs': [{'id': job['id'], 'status': by_id[job['id']]['status'], 'round': job['round']} for job in before_jobs]}
(out/'deployment.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print('DSLab integration applied at idle boundary; jobs paused, rounds preserved; no paid requests. Add credentials to .env and recreate recipients as documented.')
