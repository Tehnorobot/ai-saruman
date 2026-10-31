"""Independent scratch integration checks; run inside the Linux test client."""
import hashlib
import io
import json
import os
from pathlib import Path
import time

from common.safe_archive import pack_tree
from worker.sandbox import DockerSandbox


ROOT = Path('/projects/1234567890abcd')
WORKSPACE = ROOT / 'workspace'
EVIDENCE = Path('/evidence')
SANDBOX = DockerSandbox()


def evidence(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, indent=2), encoding='utf-8')


def run():
    ROOT.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(exist_ok=True)
    EVIDENCE.mkdir(exist_ok=True)
    SANDBOX.recover(ROOT)
    (WORKSPACE / 'delete-me.txt').write_text('old')
    (WORKSPACE / 'keep.txt').write_text('preserved')
    (WORKSPACE / 'settings.gradle').write_text("rootProject.name = 'restricted-runner-verification'\n")
    (WORKSPACE / 'build.gradle').write_text("plugins { id 'java' }\n")
    source = WORKSPACE / 'src/main/java/Smoke.java'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('public class Smoke { public static void main(String[] args) { System.out.println("verified"); } }')
    program = '''import json, os, pathlib, subprocess
p=pathlib.Path
assert os.getuid()==1000
assert not p('/var/run/docker.sock').exists()
assert not p('/run/lab-runner/runner.sock').exists()
assert not p('/data/lab.sqlite3').exists()
assert not p('/projects/1234567890abcd').exists()
assert not p('/state').exists()
assert not p('/agent-tasks/requests').exists()
assert not p('/home/lab/.codex/auth.json').exists()
assert not os.getenv('AWS_SECRET_ACCESS_KEY')
try:
 p('/opt/lab/forbidden.txt').write_text('bad')
 raise AssertionError('Root filesystem was writable')
except OSError:
 pass
p('delete-me.txt').unlink()
p('changed.txt').write_text('returned from isolated runner')
p('isolation.json').write_text(json.dumps({'uid':os.getuid(),'env':dict(os.environ),'mounts':p('/proc/self/mountinfo').read_text()}))
p('/output/result.txt').write_text('output volume writable')
subprocess.run(['gradle','--offline','--no-daemon','jar'],check=True)
'''
    result = SANDBOX.execute(['python3', '-c', program], WORKSPACE,
                             EVIDENCE / 'build.log', 120, '00000000000001', lambda: None)
    assert result['status'] == 'PASS', result
    assert (WORKSPACE / 'changed.txt').read_text() == 'returned from isolated runner'
    assert not (WORKSPACE / 'delete-me.txt').exists()
    assert (WORKSPACE / 'keep.txt').read_text() == 'preserved'
    assert not (ROOT / '.lab-runner.json').exists()
    jars = list((WORKSPACE / 'build/libs').glob('*.jar'))
    assert len(jars) == 1
    evidence('build-result.json', {**result, 'jar': jars[0].name,
                                  'sha256': hashlib.sha256(jars[0].read_bytes()).hexdigest()})

    result = SANDBOX.execute(['python3', '-c',
                             "from pathlib import Path; import time; Path('timeout.txt').write_text('saved'); time.sleep(90)"],
                            WORKSPACE, EVIDENCE / 'timeout.log', 1, '00000000000002', lambda: None)
    assert result['status'] == 'TIMEOUT', result
    assert (WORKSPACE / 'timeout.txt').read_text() == 'saved'
    assert not (ROOT / '.lab-runner.json').exists()
    evidence('timeout-result.json', result)

    count = 0
    def pause():
        nonlocal count
        count += 1
        return 'PAUSED' if count >= 4 else None
    result = SANDBOX.execute(['python3', '-c',
                             "from pathlib import Path; import time; Path('pause.txt').write_text('saved'); time.sleep(90)"],
                            WORKSPACE, EVIDENCE / 'pause.log', 120, '00000000000003', pause)
    assert result['status'] == 'PAUSED', result
    assert (WORKSPACE / 'pause.txt').read_text() == 'saved'
    assert not (ROOT / '.lab-runner.json').exists()
    evidence('pause-result.json', result)

    # Deliberately leave a durable running job, then let the host restart broker.
    request = SANDBOX.config(['python3', '-c',
                             "from pathlib import Path; import time; Path('recovered.txt').write_text('saved'); time.sleep(300)"],
                            WORKSPACE, '00000000000004')
    token = SANDBOX.api('POST', '/runs', request)['Id']
    (ROOT / '.lab-runner.json').write_text(json.dumps({'container': token}))
    with io.BytesIO() as snapshot:
        pack_tree(WORKSPACE, snapshot)
        SANDBOX.api('PUT', '/runs/' + token + '/workspace', upload=snapshot)
    SANDBOX.api('POST', '/runs/' + token + '/start')
    time.sleep(2)
    assert SANDBOX.api('GET', '/runs/' + token)['State']['Running']
    evidence('restart-ready.json', {'token': token})
    deadline = time.monotonic() + 180
    while not (EVIDENCE / 'broker-restarted.json').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('Host did not restart scratch broker')
        time.sleep(1)
    info = SANDBOX.api('GET', '/runs/' + token)
    assert not info['State']['Running'], info
    SANDBOX.recover(ROOT)
    assert (WORKSPACE / 'recovered.txt').read_text() == 'saved'
    assert not (ROOT / '.lab-runner.json').exists()
    try:
        SANDBOX.api('GET', '/runs/' + token)
    except RuntimeError as exc:
        assert ': 404:' in str(exc)
    else:
        raise AssertionError('Recovered run was not removed')
    evidence('restart-result.json', {'state_after_restart': info, 'workspace_restored': True})
    evidence('result.json', {'status': 'PASS', 'checks': ['Windows bind source/output journaled replacement',
             'nonroot readonly isolated build', 'actual offline Gradle jar', 'timeout snapshot return',
             'pause snapshot return', 'broker restart stops orphan and recovers snapshot']})


if __name__ == '__main__':
    if os.getenv('VERIFY_VIDEO_ONLY'):
        result = SANDBOX.execute(['python3', '-c', 'import time; time.sleep(3)'],
                                 WORKSPACE, EVIDENCE / 'video.log', 60,
                                 '00000000000005', lambda: None, record=True)
        video = EVIDENCE / 'minecraft_client_capture.mp4'
        assert result['status'] == 'PASS', result
        assert video.is_file() and video.stat().st_size > 100
        import subprocess
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                                'format=duration', '-of', 'json', str(video)],
                               check=True, capture_output=True, text=True)
        duration = float(json.loads(probe.stdout)['format']['duration'])
        assert 0 < duration <= 60, duration
        evidence('video-result.json', {'status': 'PASS', 'duration': duration,
                 'bytes': video.stat().st_size,
                 'sha256': hashlib.sha256(video.read_bytes()).hexdigest(),
                 'content': 'real Xvfb desktop capture; no Minecraft launched'})
    elif os.getenv('VERIFY_POLICY_ONLY'):
        allowed = {'project': '1234567890abcd', 'job': '000000000000ff',
                   'argv': ['true'], 'record': False}
        rejected = []
        for key, value in [('image', 'alpine'), ('mounts', ['/']), ('env', {'HOME': '/state'}),
                           ('privileged', True), ('HostConfig', {'Binds': ['/']}),
                           ('project', '../1234567890')]:
            try:
                SANDBOX.api('POST', '/runs', {**allowed, key: value})
            except RuntimeError as exc:
                assert ': 400:' in str(exc), exc
                rejected.append(key)
            else:
                raise AssertionError('Unsafe request accepted: ' + key)
        try:
            SANDBOX.api('POST', '/containers/create', {'Image': 'alpine'})
        except RuntimeError as exc:
            assert ': 404:' in str(exc), exc
        else:
            raise AssertionError('Raw Engine API exposed')
        evidence('policy-result.json', {'status': 'PASS', 'rejected': rejected,
                                       'raw_engine_api': '404'})
    else:
        run()
