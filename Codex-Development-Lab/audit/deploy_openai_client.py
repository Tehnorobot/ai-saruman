"""Install tested budget/accounting changes only while all work is idle."""
import json,os,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen
root=Path(__file__).resolve().parents[1]
docker=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
compose=docker.parents[1]/'cli-plugins/docker-compose.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
jid='5babdfba708646'
def run(*args):
    r=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=240)
    if r.returncode:raise RuntimeError(r.stderr[-2000:]+r.stdout[-2000:])
    return r.stdout.strip()
def api(path):
    with urlopen('http://localhost:8080/api/'+path,timeout=20) as response:return json.load(response)
assert all(j['status'] in ('USER_PAUSED','FAILED','FINISHED','STOPPED') for j in api('jobs'))
assert not list((root/'projects').glob('*/.lab-runner.json'))
for box in ('agent-tasks','planner-tasks'):
    for state in ('requests','running'):assert not list((root/box/state).glob('*.json'))
before=api('jobs/'+jid);assert before['status']=='USER_PAUSED'
usage=api('jobs/'+jid+'/usage')['totals']
for kind in ('provider',):
    run(docker,'image','tag','codex-lab-'+kind+':local','codex-lab-'+kind+':before-openai-client')
    run(docker,'image','tag','codex-lab-'+kind+':openai-client','codex-lab-'+kind+':local')
run(compose,'up','-d','--no-deps','--no-build','--force-recreate','provider-gateway')

for _ in range(40):
    try:
        after=api('jobs/'+jid)
        current=api('jobs/'+jid+'/usage')
        if current['available']:break
    except OSError:pass
    time.sleep(.5)
else:raise RuntimeError('Dashboard/gateway not ready')
assert all(before[k]==after[k] for k in ('status','phase','round','model','provider','resume_generation','last_error'))
assert all(current['totals'][key]==value for key,value in usage.items())
assert sum(r['requests'] for r in current['roles'])==usage['requests']
result={'status':'USER_PAUSED','round':after['round'],'planner_budget':max(1000,after['dslab_token_budget']//5),
 'coder_budget':after['dslab_token_budget']-max(1000,after['dslab_token_budget']//5),
 'usage_unchanged':True,'context_unchanged':True,'model_invoked':False,'role_accounting_available':True,'role_settings_available':api('jobs/'+jid+'/agent-models')['roles']==['planner','coder']}
folder=root/'audit/openai-client';folder.mkdir(exist_ok=True)
(folder/'deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
