"""Run via stdin in the existing worker only while every job is idle."""
import hashlib
import json
import os
from pathlib import Path
import re

from common import acceptance, db, memory_archive
from common.history import _atomic
from common.project_lock import project_lock
from worker.round_context import refresh_archive

jobs=db.jobs_list()
assert all(j['status'] in ('USER_PAUSED','FAILED','STOPPED','FINISHED') for j in jobs), 'Active job'
projects=Path(os.getenv('LAB_PROJECTS','/projects'))
artifacts=Path(os.getenv('LAB_ARTIFACTS','/artifacts'))
assert not list(projects.glob('*/.lab-runner.json')), 'Active runner'
for name in ('AGENT_TASKS','PLANNER_TASKS'):
    mailbox=Path(os.getenv(name,'/agent-tasks' if name=='AGENT_TASKS' else '/planner-tasks'))
    assert not list((mailbox/'requests').glob('*.json')) and not list((mailbox/'running').glob('*.json')), 'Active agent'

results=[]
for job in jobs:
    project=projects/job['project_id'];ws=project/'workspace'
    output=artifacts/job['project_id']/job['id']
    if not output.is_dir() or not ws.is_dir():
        results.append({'job_id':job['id'],'state':'missing_workspace_or_artifacts'})
        continue
    with project_lock(project):
        assert db.job_row(job['id'])['status'] in ('USER_PAUSED','FAILED','STOPPED','FINISHED'), 'Job resumed before migration'
        before=acceptance.source_fingerprint(ws)
        digest=hashlib.sha256(acceptance.read_spec(project).encode()).hexdigest()
        rounds=[int(m[1]) for folder in output.iterdir()
                if (m:=re.fullmatch(r'round-([0-9]{2,16})',folder.name)) and folder.is_dir() and not folder.is_symlink()]
        number=max(rounds+[job['round']])+1
        legacy=memory_archive.read_json(output,'round-memory.json') or {}
        memory=refresh_archive(ws,output,number,digest,legacy_memory=legacy)
        _atomic(output/'round-memory.json',memory)
        acceptance.write_workspace_json(ws,'round-memory.json',memory)
        assert acceptance.source_fingerprint(ws)==before, 'Production source changed'
        results.append({'job_id':job['id'],'state':'migrated','source_unchanged':True,
                        'archive':memory.get('memory_archive',{})})
current={j['id']:j for j in db.jobs_list()}
for original in jobs:
    for key in ('status','phase','round','resume_generation','role_models','model','provider','reasoning_effort'):
        assert original.get(key)==current[original['id']].get(key), 'Job settings changed'
print(json.dumps({'jobs_unchanged':True,'model_invoked':False,'results':results}))
