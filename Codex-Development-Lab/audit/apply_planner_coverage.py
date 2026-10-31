"""Install the tested coverage update only while all jobs are idle. Never resume jobs."""
import json, os, subprocess, tempfile, time
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
docker = Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose = docker.parents[1] / 'cli-plugins/docker-compose.exe'
env = dict(os.environ, DOCKER_CONFIG=str(Path(tempfile.gettempdir()) / 'codex-lab-history-cli'))

def run(*args):
    result = subprocess.run(list(map(str, args)), cwd=root, env=env, capture_output=True,
                            text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return result.stdout.strip()

def api(path):
    with urlopen('http://localhost:8080/api/' + path, timeout=30) as response:
        return json.load(response)

def idle():
    jobs = api('jobs')
    assert all(j['status'] in ('USER_PAUSED', 'FAILED', 'STOPPED', 'FINISHED') for j in jobs)
    assert not list((root / 'projects').glob('*/.lab-runner.json'))
    for box in ('agent-tasks', 'planner-tasks'):
        for state in ('requests', 'running'):
            assert not list((root / box / state).glob('*.json')), (box, state)
    return jobs

before = idle()
for family in ('service', 'worker'):
    run(docker, 'image', 'inspect', f'codex-lab-{family}:planner-coverage', '--format', '{{.Id}}')
    run(docker, 'image', 'tag', f'codex-lab-{family}:local', f'codex-lab-{family}:before-planner-coverage')
    for tag in ('local', 'model-catalogue'):
        run(docker, 'image', 'tag', f'codex-lab-{family}:planner-coverage', f'codex-lab-{family}:{tag}')
idle()
# The launcher pins its worker image at startup, so refresh it first.
run(compose, 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'runner')
run(compose, 'up', '-d', '--no-deps', '--no-build', '--force-recreate',
    'worker', 'agent', 'agent-dslab', 'agent-planner', 'dashboard')
for attempt in range(40):
    try:
        after = idle()
        break
    except OSError:
        time.sleep(.5)
else:
    raise RuntimeError('Dashboard did not become ready')

# Regenerate only the paused job's controller context, under its normal project lock.
# Source code, the specification, acceptance mappings and model settings stay intact.
code = r'''
import json
from pathlib import Path
from common import db
from common.history import _atomic
from common.project_lock import project_lock
from worker.round_context import prepare_context, _read
j=db.job_row('5babdfba708646')
assert j['status']=='USER_PAUSED'
project=Path('/projects')/j['project_id']
output=Path('/artifacts')/j['project_id']/j['id']
with project_lock(project):
    assert db.job_row(j['id'])['status']=='USER_PAUSED'
    backup=output/'context-before-coverage-upgrade.json'
    if not backup.exists():
        _atomic(backup,{name:_read(project/'workspace',name) for name in
            ('round-memory.json','document-index.json','requirements-catalog.json','acceptance-tasks.json')})
    previous=_read(output/f"round-{j['round']:02d}",'verification_complete.json') or {}
    context=prepare_context(project/'workspace',output,j['round']+1,previous)
print(json.dumps({'round':j['round'],'context_refreshed':True,'model_invoked':False}))
'''
refreshed = json.loads(run(compose, 'exec', '-T', 'worker', 'python3', '-c', code))
current = {j['id']: j for j in idle()}
for old in before:
    new = current[old['id']]
    for key in ('status', 'phase', 'round', 'provider', 'model', 'reasoning_effort',
                'role_models', 'resume_generation'):
        assert old.get(key) == new.get(key), key
result = {'installed': True, 'jobs_unchanged': True, 'model_invoked': False,
          'context': refreshed,
          'images': {family: run(docker, 'image', 'inspect', f'codex-lab-{family}:local',
                                '--format', '{{.Id}}') for family in ('service', 'worker')}}
(root / 'audit/planner-coverage-deployment.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
