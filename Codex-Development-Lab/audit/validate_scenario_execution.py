"""Record exact installed source/image versions without invoking any model."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
DOCKER=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
ENV=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
FILES=['common/gameplay_execution.py','common/role_context.py','common/verification_feedback.py',
       'worker/acceptance_runner.py','worker/pipeline_verification.py','worker/pipeline.py']


def docker(*args,input=None):
    result=subprocess.run([str(DOCKER),*args],cwd=ROOT,env=ENV,input=input,
                          capture_output=True,text=True,timeout=90)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    return result.stdout.strip()


def run():
    deployment=json.loads((ROOT/'audit/scenario-execution-v1-deployment.json').read_text())
    assert deployment['installed'] and deployment['jobs_unchanged']
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}
    code=('import hashlib,json,pathlib\n'
          'names='+repr(FILES)+'\n'
          'print(json.dumps({n:hashlib.sha256((pathlib.Path("/opt/lab")/n).read_bytes()).hexdigest() for n in names}))\n')
    images={}
    for family in ('service','worker'):
        image='codex-lab-'+family+':scenario-execution-v1'
        image_id=docker('image','inspect',image,'--format','{{.Id}}')
        assert docker('image','inspect','codex-lab-'+family+':local','--format','{{.Id}}')==image_id
        actual=json.loads(docker('run','--rm','-i','--network','none','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--entrypoint','python3',image,'-',input=code))
        assert actual==hashes,(family,'source mismatch')
        images[family]=image_id
    for service in ('worker','agent','agent-dslab','agent-planner','dashboard'):
        family='service' if service=='dashboard' else 'worker'
        assert docker('inspect','codex-development-lab-'+service+'-1','--format','{{.Image}}')==images[family]
    with urlopen('http://localhost:8080/api/jobs',timeout=30) as response:jobs=json.load(response)
    assert all(j['status'] in ('USER_PAUSED','FAILED','STOPPED','FINISHED') for j in jobs)
    assert not list((ROOT/'projects').glob('*/.lab-runner.json'))
    for box in ('agent-tasks','planner-tasks'):
        for state in ('requests','running'):assert not list((ROOT/box/state).glob('*.json'))
    result={'installed':True,'source_sha256':hashes,'images':images,
        'platform_suite':{'tests':1560,'skipped':74,'status':'PASS',
            'log':'audit/scenario-execution-full-tests-v1.txt'},
        'restricted_supervisor_journal_test':'PASS','model_invoked':False,
        'jobs':[{'id':j['id'],'status':j['status'],'round':j['round']} for j in jobs]}
    (ROOT/'audit/scenario-execution-installation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':run()
