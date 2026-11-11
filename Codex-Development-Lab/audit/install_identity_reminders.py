"""Install tested runtime images while jobs are paused and retain pending work."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DOCKER = Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
ENV = dict(os.environ, DOCKER_CONFIG=str(Path(tempfile.gettempdir()) / 'codex-lab-history-cli'))
VERSION = 'identity-reminders-v1'
FILES = ['worker/pipeline.py', 'fixtures/minecraft-client/quickplay.init.gradle']


def docker(*args, input=None, timeout=180):
    command = ([str(DOCKER.parents[1] / 'cli-plugins/docker-compose.exe'), *args[1:]]
               if args and args[0] == 'compose' else [str(DOCKER), *args])
    result = subprocess.run(command, cwd=ROOT, env=ENV, input=input,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Docker command failed: ' + result.stderr[-2500:])
    return result.stdout.strip()


def idle():
    with urlopen('http://127.0.0.1:8080/api/jobs', timeout=20) as response:
        jobs = json.load(response)
    assert all(j['status'] in ('USER_PAUSED', 'FAILED', 'STOPPED', 'FINISHED') for j in jobs), 'An active job exists'
    assert not list((ROOT / 'projects').glob('*/.lab-runner.json')), 'Runner still owns a project'
    for box in ('agent-tasks', 'planner-tasks'):
        for state in ('requests', 'running'):
            assert not list((ROOT / box / state).glob('*.json')), 'An agent request is active'
    return jobs


def run():
    raw_tests = (ROOT / 'audit/identity-reminders-full-tests.txt').read_bytes()
    tests = raw_tests.decode('utf-16' if raw_tests.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8')
    assert re.search(r'Ran 1568 tests', tests) and re.search(r'^OK \(skipped=76\)', tests, re.M)
    gameplay = json.loads((ROOT / 'audit/persistence-identity-v2/persistence-identity.json').read_text())
    assert gameplay['status'] == 'PASS' and all(gameplay['checks'].values())
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}
    assert hashes[FILES[1]] == gameplay['adapter_sha256'], 'Adapter changed after the real game test'
    probe = ('import hashlib,json,pathlib\n'
             'names=' + repr(FILES) + '\n'
             'print(json.dumps({n:hashlib.sha256((pathlib.Path("/opt/lab")/n).read_bytes()).hexdigest() for n in names}))\n')
    images = {}
    for family in ('worker', 'service'):
        image = 'codex-lab-' + family + ':' + VERSION
        installed = json.loads(docker('run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--entrypoint', 'python3', image, '-', input=probe))
        assert installed == hashes, (family, 'Built source mismatch')
        images[family] = docker('image', 'inspect', image, '--format', '{{.Id}}')
    before = idle()
    folder = ROOT / 'audit/identity-reminders-installation'
    folder.mkdir(exist_ok=True)
    (folder / 'jobs.json').write_text(json.dumps(before), encoding='utf-8')
    for family in ('worker', 'service'):
        docker('image', 'tag', 'codex-lab-' + family + ':' + VERSION, 'codex-lab-' + family + ':local')
    # The runner pins the worker image at startup. All job/agent queues are idle.
    log = docker('compose', 'up', '-d', '--no-deps', '--force-recreate',
                 'runner', 'worker', 'agent', 'agent-dslab', 'agent-planner', 'dashboard')
    (folder / 'compose.txt').write_text(log, encoding='utf-8')
    deadline = time.monotonic() + 60
    while True:
        try:
            after = idle()
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    assert before == after, 'Job data changed during installation'
    command = ['run', '--rm', '--network', 'none', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges',
        '--mount', 'type=bind,source=' + str(ROOT / 'projects') + ',target=/projects',
        '--mount', 'type=bind,source=' + str(ROOT / 'artifacts') + ',target=/artifacts',
        '--mount', 'type=bind,source=' + str(folder) + ',target=/input,readonly',
        '--mount', 'type=bind,source=' + str(ROOT / 'audit/backfill_unresolved_work.py') + ',target=/maintenance.py,readonly',
        '--entrypoint', 'python3', 'codex-lab-service:' + VERSION,
        '/maintenance.py', '--job-id', '69496be60a264a']
    backfill = json.loads(docker(*command, timeout=300))
    (folder / 'reminders.json').write_text(json.dumps(backfill, indent=2, ensure_ascii=False), encoding='utf-8')
    assert any(r['scenario_id'] == 'persistence-after-exit' and r['unscheduled_condition_ids']
               for r in backfill['reminders']), 'Persistence follow-up is not visible'
    assert before == idle(), 'Maintenance changed job state'
    for name, family in [('worker', 'worker'), ('agent', 'worker'), ('agent-dslab', 'worker'),
                         ('agent-planner', 'worker'), ('dashboard', 'service')]:
        assert docker('inspect', 'codex-development-lab-' + name + '-1', '--format', '{{.Image}}') == images[family]
    result = {'installed': True, 'version': VERSION, 'images': images, 'source_sha256': hashes,
              'suite': {'tests': 1568, 'skipped': 76, 'status': 'PASS'},
              'real_two_jvm_test': 'audit/persistence-identity-v2/persistence-identity.json',
              'backfill': backfill, 'jobs_unchanged': True, 'jobs_resumed': False, 'model_invoked': False}
    (ROOT / 'audit/identity-reminders-installation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'installed': True, 'jobs_unchanged': True,
                      'open_checkpoint_reminders': len(backfill['reminders']),
                      'persistence_followup_visible': True, 'jobs_resumed': False}))


if __name__ == '__main__':
    run()
