"""Refresh paused controller context only; never run a planner, coder or game."""
import json,os,subprocess,tempfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
compose=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/cli-plugins/docker-compose.exe')
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
code=r'''
import json
from pathlib import Path
from common import db
from common.project_lock import project_lock
from worker.round_context import prepare_context,_read
j=db.job_row('f86241b7d5b24e')
assert j['status']=='USER_PAUSED'
project=Path('/projects')/j['project_id']
output=Path('/artifacts')/j['project_id']/j['id']
with project_lock(project):
    assert db.job_row(j['id'])['status']=='USER_PAUSED'
    previous=_read(output/f"round-{j['round']:02d}",'verification_complete.json') or {}
    context=prepare_context(project/'workspace',output,j['round']+1,previous)
    directive=context['scheduling_directive']
    assert directive['kind']=='implement_functional_goal',directive
    policy=context.get('round_progress_policy',{})
print(json.dumps({'job_id':j['id'],'status':j['status'],'next_round':j['round']+1,
    'directive':directive,'round_progress_policy':policy,'model_invoked':False},ensure_ascii=False))
'''
result=subprocess.run([str(compose),'exec','-T','worker','python3','-c',code],cwd=root,
                      env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
if result.returncode:raise RuntimeError(result.stderr[-3000:])
value=json.loads(result.stdout)
(root/'audit/functional-goal-context.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(value,ensure_ascii=False))
