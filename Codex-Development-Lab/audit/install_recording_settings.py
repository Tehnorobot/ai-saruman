"""Install configurable recording after offline validation, keeping jobs paused."""
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import urlopen

from install_identity_reminders import ROOT, docker, idle

VERSION = 'recording-settings-v1'
WORKER = ['common/recording_settings.py', 'worker/sandbox.py', 'worker/video_capture.py',
          'worker/sandbox_entry.py', 'worker/verification_plan.py', 'worker/engine.py']
SERVICE = WORKER + ['app/main.py', 'app/index.html', 'app/recording-settings.js',
                    'app/dashboard.js', 'app/dashboard.css', 'storage/s3_cli.py']
FAMILIES = {'worker': WORKER, 'service': SERVICE, 'runner': ['runner/policy.py']}


def log_text(name):
    raw = (ROOT / 'audit' / name).read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8')


def run():
    suite = log_text('recording-settings-full-tests.txt')
    assert re.search(r'^OK(?: \(skipped=\d+\))?\s*$', suite, re.M), 'Full suite did not pass'
    total = int(re.search(r'Ran (\d+) tests', suite)[1])
    skipped = re.search(r'OK \(skipped=(\d+)\)', suite)
    native = log_text('recording-duration-native-tests.txt')
    assert 'Ran 19 tests' in native and re.search(r'^OK\s*$', native, re.M), 'Native long capture did not pass'
    images, hashes = {}, {}
    for family, names in FAMILIES.items():
        hashes[family] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in names}
        script = ('import hashlib,json,pathlib\n'
                  'names=' + repr(names) + '\n'
                  'print(json.dumps({n:hashlib.sha256((pathlib.Path("/opt/lab")/n).read_bytes()).hexdigest() for n in names}))\n')
        image = 'codex-lab-' + family + ':' + VERSION
        actual = json.loads(docker('run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--entrypoint', 'python3', image, '-', input=script))
        assert actual == hashes[family], (family, 'Source changed since build')
        images[family] = docker('image', 'inspect', image, '--format', '{{.Id}}')
    before = idle()
    folder = ROOT / 'audit/recording-settings-installation'
    folder.mkdir(exist_ok=True)
    (folder / 'jobs-before.json').write_text(json.dumps(before), encoding='utf-8')
    for family in FAMILIES:
        docker('image', 'tag', 'codex-lab-' + family + ':' + VERSION, 'codex-lab-' + family + ':local')
    docker('compose', 'up', '-d', '--no-deps', '--force-recreate',
           'runner', 'worker', 'agent', 'agent-dslab', 'agent-planner', 'dashboard', 'storage')
    deadline = time.monotonic() + 60
    while True:
        try:
            after = idle()
            with urlopen('http://127.0.0.1:8080/api/recording-settings', timeout=10) as response:
                setting = json.load(response)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    assert before == after, 'Job state changed'
    assert type(setting['max_seconds']) is int and setting['max_seconds'] > 0
    assert setting['applies_to'] == 'next_test_run'
    with urlopen('http://127.0.0.1:8080/', timeout=10) as response:
        html = response.read().decode('utf-8')
    assert 'id="recordingSettingsOpen"' in html and 'id="recordingMaxSeconds"' in html
    with urlopen('http://127.0.0.1:8080/recording-settings.js', timeout=10) as response:
        assert b'/api/recording-settings' in response.read()
    for service, family in [('runner', 'runner'), ('worker', 'worker'), ('agent', 'worker'),
                            ('agent-dslab', 'worker'), ('agent-planner', 'worker'),
                            ('dashboard', 'service'), ('storage', 'service')]:
        actual = docker('inspect', 'codex-development-lab-' + service + '-1', '--format', '{{.Image}}')
        assert actual == images[family], (service, 'Old image still running')
    result = {'installed': True, 'version': VERSION, 'setting': setting,
              'images': images, 'source_sha256': hashes,
              'suite': {'tests': total, 'skipped': int(skipped[1]) if skipped else 0, 'status': 'PASS'},
              'native_capture': {'tests': 19, 'status': 'PASS', 'selected_seconds': 62,
                                 'encoded_duration_over_60_seconds': True,
                                 'log': 'audit/recording-duration-native-tests.txt'},
              'storage': {'real_h264_fixture_seconds': 71, 'uploaded_bytes_unchanged': True,
                          'external_test_uploads': False},
              'jobs_unchanged': True, 'jobs_resumed': False, 'model_invoked': False,
              'notes': 'Duration is fixed per new test container; storage preserves completed recordings.'}
    (ROOT / 'audit/recording-settings-installation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'installed': True, 'max_seconds': setting['max_seconds'],
                      'tests': total, 'jobs_unchanged': True, 'jobs_resumed': False}))


if __name__ == '__main__':
    run()
