"""Update idle worker/executors; preserve pause, credentials and job progress."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'audit'/'round-memory'
OUT.mkdir(exist_ok=True)
DOCKER=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
COMPOSE=DOCKER.parents[1]/'cli-plugins/docker-compose.exe'
ENV=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))


def run(*args):
    result=subprocess.run(list(map(str,args)),cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=120)
    if result.returncode:
        raise RuntimeError('Container update/check failed: '+result.stderr[-1200:])
    return result.stdout.strip()


def api(suffix):
    with urlopen('http://localhost:8080/api/'+suffix,timeout=10) as response:
        return json.load(response)


def idle():
    jobs=api('jobs')
    assert all(row['status'] in ('USER_PAUSED','FINISHED','FAILED','STOPPED') for row in jobs),'Update requires idle jobs'
    assert not list((ROOT/'agent-tasks/requests').glob('*.json'))
    assert not list((ROOT/'agent-tasks/running').glob('*.json'))
    assert not list((ROOT/'projects').glob('*/.lab-runner.json'))
    return jobs


roles=('worker','agent','agent-dslab','dashboard','runner','provider-gateway','storage','backup','egress')
def inspect(role,fmt):
    return run(DOCKER,'inspect','codex-development-lab-'+role+'-1','--format',fmt)


jobs_before=idle()
usage_before=api('jobs/89cd3a16ad2c4e/usage')['totals']
before={role:inspect(role,'{{.Id}}') for role in roles}
run(DOCKER,'image','tag',inspect('worker','{{.Image}}'),'codex-lab-worker:before-round-memory')
run(DOCKER,'image','tag','codex-lab-worker:round-memory','codex-lab-worker:local')
idle()
# Broker pins an immutable image ID at startup. Reload it after retagging,
# otherwise verification silently keeps the old supervisor implementation.
run(COMPOSE,'restart','runner')
# Normal Compose consumes .env internally; this script never opens or prints it.
run(COMPOSE,'up','-d','--no-deps','--no-build','--force-recreate','worker','agent','agent-dslab')
after={role:inspect(role,'{{.Id}}') for role in roles}
assert all(before[role]==after[role] for role in roles if role not in ('worker','agent','agent-dslab'))
for role in ('worker','agent','agent-dslab'):
    assert inspect(role,'{{.State.Running}}')=='true'
    for relative in ('worker/engine.py','worker/round_context.py','worker/sandbox_entry.py',
                     'common/verification.py','common/acceptance.py','agent/dslab.py'):
        command='import hashlib;print(hashlib.sha256(open('+repr('/opt/lab/'+relative)+',"rb").read()).hexdigest())'
        actual=run(DOCKER,'exec','codex-development-lab-'+role+'-1','python3','-c',command)
        assert actual==hashlib.sha256((ROOT/relative).read_bytes()).hexdigest(),(role,relative)

seed='''import json
from pathlib import Path
from common import db,acceptance
from common.project_lock import project_lock
from worker.round_context import prepare_context
jid='89cd3a16ad2c4e'
job=db.job_row(jid)
assert job['status']=='USER_PAUSED'
ws=Path('/projects')/job['project_id']/'workspace'
out=Path('/artifacts')/job['project_id']/jid
assert not any(ws.rglob('.env*')), 'Unexpected credential path: context preparation aborted'
with project_lock(ws.parent):
 before=acceptance.source_fingerprint(ws)
 previous=json.loads((out/'latest_verification.json').read_text())
 context=prepare_context(ws,out,job['round']+1,previous)
 assert acceptance.source_fingerprint(ws)==before
 print(json.dumps({'source_sha256':before,'context_characters':len(json.dumps(context,ensure_ascii=False)),
    'open_error_count':context['open_error_count'],'spec_sha256':context['spec_sha256'],
    'document_sections':context['documents']['section_count'],'agent_notes_available':context['agent_handoff']['available']}))
'''
seeded=json.loads(run(DOCKER,'exec','codex-development-lab-worker-1','python3','-c',seed))
jobs_after=idle()
by_id={row['id']:row for row in jobs_after}
for row in jobs_before:
    for key in ('status','round','model','provider','resume_generation','max_rounds'):
        assert by_id[row['id']].get(key)==row.get(key),(row['id'],key)
usage_after=api('jobs/89cd3a16ad2c4e/usage')['totals']
assert usage_after==usage_before,'Unexpected provider activity during idle update'
report={'before':before,'after':after,'job_state_preserved':True,'paid_requests_started':False,
        'usage_unchanged':True,'context':seeded,'usage_totals':usage_after}
(OUT/'deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'updated':['worker','agent','agent-dslab'],'job_status':'USER_PAUSED',
                  'usage_unchanged':True,'context':seeded},ensure_ascii=False))
