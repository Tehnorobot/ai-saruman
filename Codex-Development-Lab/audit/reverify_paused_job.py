"""Verify saved sources once; never resume the model loop or spend tokens."""
import os
from pathlib import Path
import subprocess
import tempfile

docker='C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
env=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))
script=r'''
import json,shutil,uuid
from pathlib import Path
from unittest.mock import patch
from common import db
from common.project_lock import project_lock
from common.verification import infrastructure_failure
from worker import engine
from worker.round_context import prepare_context
jid='89cd3a16ad2c4e'
job=db.job_row(jid);assert job['status']=='USER_PAUSED' and not job['worker']
generation=job['resume_generation']
root=engine.WORKSPACE_ROOT/job['project_id'];ws=root/'workspace'
out=engine.ARTIFACTS/job['project_id']/jid;stage=out/f"round-{job['round']:02d}"
def guard(_jid):
 row=db.job_row(jid)
 if row['status']!='USER_PAUSED' or row['resume_generation']!=generation:
  raise engine.LeaseLost('Job changed state during manual verification')
 return None
guard(jid)
backup=stage/'verification-retries'/('manual-'+uuid.uuid4().hex);backup.mkdir(parents=True)
for name in ('verification_complete.json','verification.json','runner.log','acceptance.json'):
 p=stage/name
 if p.is_file() and not p.is_symlink():shutil.copyfile(p,backup/name)
print('Independent verification started; job remains paused, no model request.',flush=True)
with patch.object(engine,'poll_job',side_effect=guard):
 report=engine.checks(ws,stage,jid)
guard(jid)
engine.write_json(stage/'verification_complete.json',report)
engine.write_json(out/'latest_verification.json',report)
with project_lock(root):
 prepare_context(ws,out,job['round']+1,report)
 try:
  from worker.round_context import save_round_snapshot
 except ImportError:pass
 else:save_round_snapshot(out,job['round'],'after_verification',ws)
blocker=infrastructure_failure(report)
detail=(blocker.get('detail') or blocker.get('tail') or 'Verification failed') if blocker else None
with db.connect() as con:
 con.execute('BEGIN IMMEDIATE')
 current=con.execute('SELECT status,resume_generation FROM jobs WHERE id=?',(jid,)).fetchone()
 assert current['status']=='USER_PAUSED' and current['resume_generation']==generation
 con.execute('UPDATE jobs SET phase=?,last_error=?,updated_at=? WHERE id=?',
  ('verification_error' if blocker else 'verification_checked',str(detail)[-900:] if detail else None,db.utc(),jid))
 db.event(con,jid,'Повторная независимая проверка сохранённого кода: '+report['status']+'. Задание оставлено на паузе; модель не запускалась.')
print(json.dumps({'status':report['status'],'checks':[{'label':c.get('label'),'status':c.get('status'),'detail':c.get('detail','')} for c in report['checks']],
 'acceptance':report.get('acceptance',{}).get('summary'),'jars':report.get('jars'),
 'job_status':db.job_row(jid)['status'],'infrastructure_error':bool(blocker)},ensure_ascii=False),flush=True)
'''
raise SystemExit(subprocess.run([docker,'exec','-i','codex-development-lab-worker-1','python3','-'],input=script,env=env,text=True,encoding='utf-8').returncode)
