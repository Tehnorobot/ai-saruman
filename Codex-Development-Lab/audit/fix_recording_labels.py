"""Install readable recording labels by recreating only the dashboard."""
import hashlib
import json
import re
import time
from urllib.request import urlopen

from install_identity_reminders import ROOT, docker


def run():
    image = 'codex-lab-service:recording-labels-v1'
    expected = hashlib.sha256((ROOT / 'app/index.html').read_bytes()).hexdigest()
    code = 'import hashlib;print(hashlib.sha256(open("/opt/lab/app/index.html","rb").read()).hexdigest())\n'
    actual = docker('run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
                    '--entrypoint', 'python3', image, '-', input=code)
    assert actual == expected
    names = ['worker', 'agent', 'agent-planner', 'agent-dslab', 'runner', 'provider-gateway']
    ids = {n: docker('inspect', 'codex-development-lab-' + n + '-1', '--format', '{{.Id}}') for n in names}
    docker('image', 'tag', image, 'codex-lab-service:local')
    docker('compose', 'up', '-d', '--no-deps', '--force-recreate', 'dashboard')
    deadline = time.monotonic() + 45
    while True:
        try:
            with urlopen('http://127.0.0.1:8080/', timeout=5) as response:
                text = response.read().decode('utf-8')
                content_type = response.headers.get('content-type')
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    section = text[text.index('id="recordingSettings"'):text.index('id="modelsSlot"')]
    assert 'Запись видео' in section and 'Максимальная длина записи, секунд' in section
    assert not re.search(r'\?{3,}|\ufffd', section)
    assert 'charset=utf-8' in content_type.lower()
    assert all(docker('inspect', 'codex-development-lab-' + n + '-1', '--format', '{{.Id}}') == cid for n, cid in ids.items())
    with urlopen('http://127.0.0.1:8080/api/jobs/69496be60a264a', timeout=5) as response:
        job = json.load(response)
    result = {'installed': True, 'html_sha256': expected, 'readable_live_labels': True,
              'worker_and_agents_unchanged': True, 'recreated_services': ['dashboard'],
              'job': {k: job[k] for k in ('id', 'status', 'phase', 'round')}}
    (ROOT / 'audit/recording-labels-installation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    run()
