"""Install the diagnostic evidence fix while all jobs are idle; never resume jobs."""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
docker = Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose = docker.parents[1] / 'cli-plugins/docker-compose.exe'
env = dict(os.environ, DOCKER_CONFIG=str(Path(tempfile.gettempdir()) / 'codex-lab-history-cli'))
tag = sys.argv[1] if len(sys.argv)>1 else 'diagnostic-evidence'
assert re.fullmatch('[a-z0-9][a-z0-9-]{0,60}', tag), 'Invalid tested image tag'
assert all(arg in ('--provider','--runner') for arg in sys.argv[2:]), 'Unknown deployment option'
include_provider='--provider' in sys.argv[2:]
include_runner='--runner' in sys.argv[2:]

def run(*args):
    result = subprocess.run(list(map(str, args)), cwd=root, env=env, capture_output=True,
                            text=True, timeout=180)
    if result.returncode: raise RuntimeError(result.stderr[-2000:])
    return result.stdout.strip()

def idle():
    with urlopen('http://localhost:8080/api/jobs', timeout=30) as response: jobs = json.load(response)
    assert all(j['status'] in ('USER_PAUSED', 'FAILED', 'STOPPED', 'FINISHED') for j in jobs)
    assert not list((root / 'projects').glob('*/.lab-runner.json'))
    for box in ('agent-tasks', 'planner-tasks'):
        for state in ('requests', 'running'):
            assert not list((root / box / state).glob('*.json'))
    return jobs

before = idle()
families=('service','worker')+(('provider',) if include_provider else ())+(('runner',) if include_runner else ())
for family in families:
    run(docker, 'image', 'inspect', f'codex-lab-{family}:{tag}', '--format', '{{.Id}}')
    run(docker, 'image', 'tag', f'codex-lab-{family}:local', f'codex-lab-{family}:before-{tag}')
    for target in ('local', 'model-catalogue'):
        run(docker, 'image', 'tag', f'codex-lab-{family}:{tag}', f'codex-lab-{family}:{target}')
idle()
if include_provider:
    run(compose, 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'provider-gateway')
run(compose, 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'runner')
run(compose, 'up', '-d', '--no-deps', '--no-build', '--force-recreate',
    'worker', 'agent', 'agent-dslab', 'agent-planner', 'dashboard')
for attempt in range(40):
    try:
        current = {j['id']: j for j in idle()}
        break
    except OSError: time.sleep(.5)
else: raise RuntimeError('Dashboard not ready')
for old in before:
    for key in ('status', 'phase', 'round', 'model', 'provider', 'reasoning_effort',
                'role_models', 'resume_generation'):
        assert old.get(key) == current[old['id']].get(key), key
result = {'installed': True, 'image_tag':tag, 'jobs_unchanged': True, 'model_invoked': False,
          'jobs': [{k: j[k] for k in ('id', 'status', 'round')} for j in current.values()]}
(root / f'audit/{tag}-deployment.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
