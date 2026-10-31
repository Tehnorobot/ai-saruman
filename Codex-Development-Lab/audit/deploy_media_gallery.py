"""Refresh only the dashboard; active agents and verification are untouched."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import sys
from urllib.request import Request,urlopen

root=Path(__file__).resolve().parents[1];out=root/'audit/media-gallery';out.mkdir(exist_ok=True)
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=120)
    if result.returncode:raise RuntimeError('Dashboard update/check failed: '+result.stderr[-1000:])
    return result.stdout.strip()
def inspect(role,fmt):return run(docker,'inspect','codex-development-lab-'+role+'-1','--format',fmt)
roles=('dashboard','worker','agent','agent-dslab','runner','provider-gateway','storage','backup','egress')
verify_only='--verify-only' in sys.argv
before=(json.loads((root/'audit/memory-window/deployment.json').read_text(encoding='utf-8'))['after']
        if verify_only else {role:inspect(role,'{{.Id}}') for role in roles})
bindings=json.loads(inspect('dashboard','{{json .HostConfig.PortBindings}}'))['8080/tcp']
assert len(bindings)==1 and bindings[0]['HostIp']=='127.0.0.1'
env['DASHBOARD_PORT']=bindings[0]['HostPort'];base='http://localhost:'+env['DASHBOARD_PORT']
if not verify_only:
    run(docker,'image','tag',inspect('dashboard','{{.Image}}'),'codex-lab-service:before-media-gallery')
    run(docker,'image','tag','codex-lab-service:media-gallery','codex-lab-service:local')
    # Normal Compose consumes .env internally. Never read/print its credentials.
    run(compose,'up','-d','--no-deps','--no-build','--force-recreate','dashboard')
for _ in range(30):
    try:
        with urlopen(base+'/api/jobs/89cd3a16ad2c4e/media',timeout=10) as response:catalog=json.load(response)
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('Gallery API not ready')
with urlopen(base+'/',timeout=10) as response:
    # HTMLResponse uses read_text(), which normalizes Windows CRLF to LF.
    assert response.read()==(root/'app/index.html').read_text(encoding='utf-8').encode('utf-8')
details={}
for number in range(28,32):
    with urlopen(base+f'/api/jobs/89cd3a16ad2c4e/media?round_number={number}',timeout=10) as response:page=json.load(response)
    assert page['total']>=3,(number,page)
    assert all(item['round']==number for item in page['files'])
    details[str(number)]={'total':page['total'],'files':[item['path'] for item in page['files']]}
video=next(item for item in catalog['files'] if item['type']=='.mp4')
with urlopen(Request(base+video['url'],headers={'Range':'bytes=0-63'}),timeout=10) as response:
    assert response.status==206 and len(response.read())==64
    assert "media-src 'self'" in response.headers.get('Content-Security-Policy','')
code='import hashlib;print(hashlib.sha256(open("/opt/lab/common/media_catalog.py","rb").read()).hexdigest())'
assert run(docker,'exec','codex-development-lab-dashboard-1','python','-c',code)==hashlib.sha256((root/'common/media_catalog.py').read_bytes()).hexdigest()
after={role:inspect(role,'{{.Id}}') for role in roles}
assert all(before[role]==after[role] for role in roles if role!='dashboard')
report={'only_dashboard_recreated':True,'job_actions_sent':False,'before':before,'after':after,
        'baseline_source':'memory-window deployment' if verify_only else 'immediately before gallery deployment',
        'rounds_28_to_31':details,'first_page_round':catalog['files'][0]['round'],
        'total_media':catalog['total'],'native_mp4_range_check':'PASS'}
(out/'deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('only_dashboard_recreated','job_actions_sent','total_media','first_page_round','native_mp4_range_check')},ensure_ascii=False))
