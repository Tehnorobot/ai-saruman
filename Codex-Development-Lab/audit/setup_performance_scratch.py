"""Disposable runner/proxy infrastructure; no model, auth, .env, or game."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
docker = 'C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
environment = dict(os.environ, DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
prefix = 'codex-lab-perf-audit-20261021'

def cli(*args, optional=False):
    process = subprocess.run([docker,*args],env=environment,capture_output=True,text=True,timeout=60)
    if process.returncode and not optional: raise RuntimeError(process.stderr)
    if process.returncode: return ''
    return process.stdout.strip()

for name in ('test_internal','agent_internal'):
    actual = 'codex-development-lab_'+name
    existing = cli('network','inspect',actual,optional=True)
    if existing:
        info=json.loads(existing)[0]
        assert info['Internal'] and info['Options']['com.docker.network.bridge.gateway_mode_ipv4']=='isolated'
    else:
        cli('network','create','--internal','--driver','bridge',
            '--opt','com.docker.network.bridge.gateway_mode_ipv4=isolated',
            '--opt','com.docker.network.bridge.gateway_mode_ipv6=isolated',
            '--label','com.docker.compose.project=codex-development-lab',
            '--label','com.docker.compose.network='+name,actual)
for kind in ('state','control'):
    cli('volume','create','--label','codex-lab.audit=performance-security',prefix+'-'+kind)
proxy=cli('run','-d','--name',prefix+'-proxy','--label','codex-lab.audit=performance-security',
          '--network','codex-development-lab_test_internal','--network-alias','lab-egress',
          '--user','1000:1000','--cap-drop','ALL','--security-opt','no-new-privileges:true','--read-only',
          '--memory','192m','--pids-limit','160','codex-lab-egress:verify-performance')
cli('network','connect','bridge',proxy)
broker=cli('run','-d','--name',prefix+'-runner','--label','codex-lab.audit=performance-security',
           '--network','none','--user','0:1000','--cap-drop','ALL','--cap-add','CHOWN',
           '--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--security-opt','no-new-privileges:true',
           '--read-only','--memory','512m','--pids-limit','256','--tmpfs','/tmp:rw,nosuid,nodev,size=64m,mode=1777',
           '--mount','type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock',
           '--mount','type=volume,source='+prefix+'-state,target=/state',
           '--mount','type=volume,source='+prefix+'-control,target=/run/lab-runner',
           '-e','LAB_RUNNER_IMAGE=codex-lab-worker:verify-performance','codex-lab-runner:verify-performance')
output=root/'audit/performance-security'
output.mkdir(exist_ok=True)
(root/'audit/performance-projects').mkdir(exist_ok=True)
result={'proxy':proxy,'runner':broker,'prefix':prefix,'control_volume':prefix+'-control',
        'projects':str(root/'audit/performance-projects'),'output':str(output)}
(output/'scratch.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
