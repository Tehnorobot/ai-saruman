"""Read-only runtime checks, then remove only the labeled disposable test services."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import urllib.request

root=Path(__file__).resolve().parents[1]
evidence=root/'audit/performance-security'
docker='C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
environment=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
def cli(*args):
    result=subprocess.run([docker,*args],env=environment,capture_output=True,text=True,timeout=45)
    if result.returncode: raise RuntimeError(result.stderr)
    return result.stdout.strip() or result.stderr.strip()
def inspect(name,expression): return cli('inspect',name,'--format',expression)

report={}
for service in ('dashboard','backup','worker','agent','runner','storage','egress'):
    name='codex-development-lab-'+service+'-1'
    # Read only explicit structural metadata, never Config.Env or volume contents.
    mounts=json.loads(inspect(name,'{{json .Mounts}}'))
    state=json.loads(inspect(name,'{{json .State}}'))
    host=json.loads(inspect(name,'{{json .HostConfig}}'))
    assert state['Running'],service
    assert not host['Privileged'] and 'ALL' in host['CapDrop']
    assert 'no-new-privileges:true' in host['SecurityOpt']
    for mount in mounts:
        if mount['Type']=='bind':
            if mount['Destination']=='/var/run/docker.sock':
                assert service=='runner' and mount['Source']=='/var/run/docker.sock'
            else:
                source=mount['Source']
                if source.startswith('/run/desktop/mnt/host/'):
                    suffix=source[len('/run/desktop/mnt/host/'):]
                    source=suffix[0].upper()+':/'+suffix[2:]
                assert Path(source).resolve().is_relative_to(root), (service,source)
    if service in ('worker','backup','runner'): assert host['NetworkMode']=='none'
    if service=='agent':
        assert host['NetworkMode']=='codex-development-lab_agent_internal'
        assert all(m['Destination']!='/var/run/docker.sock' for m in mounts)
    if service in ('egress','runner'): assert state['Health']['Status']=='healthy'
    report[service]={'running':True,'network':host['NetworkMode'],'mounts':mounts}
for network in ('agent_internal','test_internal'):
    item=json.loads(cli('network','inspect','codex-development-lab_'+network))[0]
    assert item['Internal'] and item['Driver']=='bridge'
    for family in ('ipv4','ipv6'):
        assert item['Options']['com.docker.network.bridge.gateway_mode_'+family]=='isolated'
with urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=5) as response:
    assert json.load(response)['status']=='ok'
with sqlite3.connect((root/'data/lab.sqlite3').as_uri()+'?mode=ro',uri=True) as database:
    jobs=database.execute('SELECT id,status FROM jobs ORDER BY id').fetchall()
deployment=json.loads((evidence/'deployment.json').read_text())
assert [list(row) for row in jobs]==deployment['jobs']
auth=cli('exec','codex-development-lab-agent-1','codex','login','status')
assert 'Logged in using ChatGPT' in auth

# Dispose of only this task's stopped scratch containers/volumes. Audit files remain.
prefix='codex-lab-perf-audit-20261021'
for suffix in ('proxy','runner'):
    name=prefix+'-'+suffix
    labels=json.loads(inspect(name,'{{json .Config.Labels}}'))
    assert labels.get('codex-lab.audit')=='performance-security'
    assert not json.loads(inspect(name,'{{json .State}}'))['Running']
    cli('rm',name)
for suffix in ('control','state'):
    name=prefix+'-'+suffix
    metadata=json.loads(cli('volume','inspect',name))[0]
    assert metadata['Labels'].get('codex-lab.audit')=='performance-security'
    cli('volume','rm',name)
result={'services':report,'jobs':jobs,'auth_status':'Logged in using ChatGPT',
        'checks_completed':['runtime_mounts','isolated_networks','capabilities','healthy_services',
                            'dashboard_http','auth_preserved','terminal_jobs_preserved','scratch_removed']}
(evidence/'runtime-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'checks_completed':result['checks_completed'],'jobs':jobs}))
