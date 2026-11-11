"""Local only dashboard: one Markdown upload => durable Codex job."""
from __future__ import annotations
import hashlib, json, os, re
from pathlib import Path, PurePosixPath
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body
from fastapi.responses import HTMLResponse, FileResponse
from common import db
from common import acceptance
from common import acceptance_progress
from common import acceptance_tasks
from common import task_assignment
from common import memory_view
from common import planning,manual_review
from common import media_catalog
from common import token_usage as usage_accounting
from common import request_history
from common.activity import snapshot as activity_snapshot
from common.history import _open
from urllib.parse import quote
from common.history import list_attempts, read_attempt
from common.codex_settings import validate_model, validate_reasoning, REASONING_EFFORTS
from common.provider_settings import validate_settings, DSLAB_MODELS, DSLAB_EFFORTS, resolve_role
from common.provider_client import control as provider_control, GatewayError

app=FastAPI(title='Codex Development Lab', docs_url='/docs')
PROJECTS=Path(os.getenv('LAB_PROJECTS','/projects'))
ARTIFACTS=Path(os.getenv('LAB_ARTIFACTS','/artifacts'))
UPLOAD_STATE=Path(os.getenv('STORAGE_STATE','/s3-state/upload_index.json'))
AGENT_TASKS=Path(os.getenv('AGENT_TASKS','/agent-tasks'))

@app.on_event('startup')
def _start():db.init()

@app.get('/',response_class=HTMLResponse)
def index():
    return (Path(__file__).parent/'index.html').read_text(encoding='utf-8')


@app.get('/api/recording-settings')
def get_recording_settings():
    from common.recording_settings import get_settings
    return get_settings()


@app.put('/api/recording-settings')
def save_recording_settings(payload:dict=Body(...)):
    from common.recording_settings import set_max_seconds
    if set(payload) != {'max_seconds'}:
        raise HTTPException(400, 'Передай только max_seconds — максимальную длину записи в секундах')
    try:
        return set_max_seconds(payload['max_seconds'])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/provider.js')
def provider_script():
    return FileResponse(Path(__file__).parent/'provider.js', media_type='application/javascript')


@app.get('/dashboard.js')
def dashboard_script():
    return FileResponse(Path(__file__).parent/'dashboard.js',media_type='application/javascript')


@app.get('/recording-settings.js')
def recording_settings_script():
    return FileResponse(Path(__file__).parent/'recording-settings.js',media_type='application/javascript')


@app.get('/dashboard.css')
def dashboard_style():
    return FileResponse(Path(__file__).parent/'dashboard.css',media_type='text/css')


@app.get('/memory.js')
def memory_script():
    return FileResponse(Path(__file__).parent/'memory.js',media_type='application/javascript')


@app.get('/planner.js')
def planner_script():
    return FileResponse(Path(__file__).parent/'planner.js',media_type='application/javascript')


@app.get('/planner.css')
def planner_style():
    return FileResponse(Path(__file__).parent/'planner.css',media_type='text/css')


@app.get('/failure-details.js')
def failure_details_script():
    return FileResponse(Path(__file__).parent/'failure-details.js',media_type='application/javascript')


@app.get('/api/jobs/{jid}/planning')
def job_planning(jid:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    root=ARTIFACTS/row['project_id']/jid
    catalog=acceptance.build_catalog(acceptance.read_spec(PROJECTS/row['project_id']))
    plan=acceptance.load_plan(PROJECTS/row['project_id']/'workspace',catalog)
    state=manual_review.decorate({'requirements':catalog['requirements']},plan)
    return {'planner':planning.public_state(root),
        'runtime_error':row.get('last_error') if row.get('phase')=='planner_error' else None,
        'manual_reviews':[r for r in state['requirements'] if r.get('review_status')=='USER_TEST_REQUIRED']}


@app.get('/memory.css')
def memory_style():
    return FileResponse(Path(__file__).parent/'memory.css',media_type='text/css')


@app.get('/pipeline.js')
def pipeline_script():
    return FileResponse(Path(__file__).parent/'pipeline.js',media_type='application/javascript')


@app.get('/pipeline.css')
def pipeline_style():
    return FileResponse(Path(__file__).parent/'pipeline.css',media_type='text/css')


def pipeline_root(jid):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    return row,ARTIFACTS/row['project_id']/jid


@app.get('/api/jobs/{jid}/pipeline')
def job_pipeline(jid:str):
    from common.memory_archive import read_json
    from common import pipeline_memory
    row,root=pipeline_root(jid)
    value=read_json(root,'pipeline/public.json')
    if value is None:return {'enabled':False,'version':'v1','job_status':row['status'],
        'notice':'Новый цикл начнётся при продолжении. Существующие исходники и история сохранены.'}
    value['job_status']=row['status']
    # Event writes can occur after the last stage publication, including a
    # failed model call. Always derive counts from the canonical archive.
    value['memory_summary']=pipeline_memory.compact_summary(root)
    state=read_json(root,'pipeline/state.json') or {}
    value['operation']=state.get('operation',value.get('operation'))
    value['service_operation']=state.get('service_operation')
    value['execution_diagnostics']=state.get('execution_diagnostics',[])
    latest_observation=read_json(root,'pipeline/latest-result.json') or {}
    if 'execution_diagnostics' in latest_observation:
        value['execution_diagnostics']=latest_observation['execution_diagnostics']
    value['agent_reports']=state.get('agent_reports',value.get('agent_reports',{}))
    value['supplemental_evidence']=state.get('supplemental_evidence',value.get('supplemental_evidence',[]))
    value['preparation']=state.get('preparation',value.get('preparation'))
    value['handoff']=state.get('preparation_handoff',value.get('handoff'))
    draft=read_json(root,'pipeline/draft.json')
    if not draft and not value.get('catalog'):
        # Read-only compatibility for jobs paused under the first v1 release:
        # its controller accepted complete.json but did not publish a draft.
        number=value.get('scope',{}).get('round')
        if isinstance(number,int) and number>=1:
            complete=read_json(root,f'round-{number:02d}/pipeline/requirements/complete.json')
            if complete and complete.get('value',{}).get('scope')==value.get('scope'):
                from common.task_graph import validate_catalog
                try:validate_catalog(complete['value'])
                except (ValueError,KeyError,TypeError):pass
                else:draft={'scope':value['scope'],'catalog':complete['value'],'progress':
                    {'phase':'catalog','catalog_structure_valid':True,'graph_structure_valid':False,'semantic_review':'pending'}}
    if draft and draft.get('scope',{}).get('spec_revision')==value.get('scope',{}).get('spec_revision'):
        value['decomposition']=draft['progress']
        if draft['progress']['semantic_review']=='pending':
            value.update(catalog=draft['catalog'],graph=draft.get('graph'),review=draft.get('review'))
            value['status']['coverage']=[{'condition_id':c['id'],'status':'untested','result_refs':[]} for c in draft['catalog']['conditions']]
            value['status']['ready']=False
    value['status']['latest_memory_ref']=value['memory_summary']['latest_record_ref']
    if row['status'] in ('USER_PAUSED','QUOTA_PAUSED','STOPPED','FAILED'):
        value['status']={**value['status'],'stage':'paused','readiness_reasons':[row.get('last_error') or 'Задание приостановлено; состояние сохранено']}
    from common.verification_display import for_pipeline
    value['verification_display']=for_pipeline(root,value)
    return value


@app.get('/api/jobs/{jid}/pipeline/memory')
def pipeline_records(jid:str,task_id:str='',cursor:str|None=None,query:str=''):
    from common import pipeline_memory
    _,root=pipeline_root(jid)
    try:return pipeline_memory.query(root,task_ids=[task_id] if task_id else None,cursor=cursor,query=query)
    except (OSError,ValueError) as exc:raise HTTPException(400,'Invalid pipeline memory query') from exc


@app.get('/api/jobs/{jid}/pipeline/memory/{record_id}')
def pipeline_record(jid:str,record_id:str):
    from common import pipeline_memory
    _,root=pipeline_root(jid)
    try:return {'record':pipeline_memory.load_record(root,record_id),'payload':pipeline_memory.read_payload(root,record_id)}
    except (OSError,ValueError) as exc:raise HTTPException(404,'No such pipeline record') from exc


@app.get('/api/jobs/{jid}/memory')
def job_memory(jid:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    try:return memory_view.overview(ARTIFACTS,row['project_id'],jid)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@app.get('/api/jobs/{jid}/round-task')
def job_round_task(jid:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    from common.memory_archive import read_json
    semantic=read_json(ARTIFACTS/row['project_id']/jid,'pipeline/public.json')
    if semantic and semantic.get('decision'):
        decision=semantic['decision'];actions=decision['actions']
        ids=list(dict.fromkeys(t for a in actions for t in a['task_ids']))
        titles={t['id']:t['title'] for t in semantic.get('graph',{}).get('tasks',[])}
        assignment={'current_task':{'id':', '.join(ids),'title':'; '.join(titles.get(t,t) for t in ids),
            'requirement_ids':list(dict.fromkeys(r for t in semantic.get('graph',{}).get('tasks',[]) if t['id'] in ids for r in t['requirement_ids']))}}
        return {'job_id':jid,'status':row['status'],'round':row['round'],'phase':row['phase'],'task':{'state':'available',
            'data':{'pipeline_version':'v1','round_action_round':decision['scope']['round'],'round_actions':actions,
                'assignment':assignment,'round_action':{'kind':decision['strategy'],'source':decision['rationale'],
                    'diagnostics':[a['goal']+' → '+a['expected_observation'] for a in actions]}}}}
    try:
        return {'job_id':jid,'status':row['status'],'round':row['round'],'phase':row['phase'],
            'task':memory_view.current_task(ARTIFACTS,row['project_id'],jid)}
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@app.get('/api/jobs/{jid}/memory/rounds/{number}')
def job_round_memory(jid:str,number:int):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    try:return memory_view.round_detail(ARTIFACTS,row['project_id'],jid,number)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except FileNotFoundError as exc:raise HTTPException(404,'Unknown round') from exc


@app.get('/api/jobs/{jid}/memory/archive')
def job_memory_archive(jid:str,kind:str='errors',page:int=1):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    try:return memory_view.archive_page(ARTIFACTS,row['project_id'],jid,kind,page)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@app.get('/api/jobs/{jid}/memory/archive/record')
def job_memory_archive_record(jid:str,path:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    try:return memory_view.archive_record(ARTIFACTS,row['project_id'],jid,path)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc

@app.get('/api/jobs')
def jobs():return db.jobs_list()

@app.post('/api/projects')
async def new_project(spec: UploadFile=File(...), max_rounds: int=Form(5),
                      model: str=Form(''), reasoning_effort: str=Form('default'),
                      provider: str=Form('codex'), dslab_token_budget: int=Form(1000000),
                      dslab_max_output_tokens: int=Form(8192), dslab_max_calls: int=Form(100)):
    if not spec.filename or not spec.filename.lower().endswith('.md'):
        raise HTTPException(400,'Upload a Markdown .md file')
    data=await spec.read(1024*1024+1)
    if not 20 <= len(data) <= 1024*1024:raise HTTPException(400,'Specification must be 20 bytes to 1 MiB')
    try:txt=data.decode('utf-8-sig')
    except UnicodeDecodeError:raise HTTPException(400,'Save Markdown as UTF-8')
    if not txt.strip():raise HTTPException(400,'Empty specification')
    try:
        max_rounds = db.validate_round_limit(max_rounds)
        provider, model, reasoning_effort, dslab_token_budget, dslab_max_output_tokens, dslab_max_calls = validate_settings(
            provider, model, reasoning_effort, dslab_token_budget, dslab_max_output_tokens, dslab_max_calls)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # No user-controlled shell commands from form input.
    title=next((s.strip('# ').strip() for s in txt.splitlines() if s.lstrip().startswith('# ')),None) or spec.filename
    pid,jid=db.create_project(title,spec.filename,data,max_rounds=max_rounds,
                              model=model,reasoning_effort=reasoning_effort, provider=provider,
                              dslab_token_budget=dslab_token_budget, dslab_max_output_tokens=dslab_max_output_tokens,
                              dslab_max_calls=dslab_max_calls)
    return {'project_id':pid,'job_id':jid,'status':'QUEUED',
            'provider':provider,'model':model or 'codex_default','reasoning_effort':reasoning_effort}

@app.get('/api/jobs/{jid}')
def job(jid:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    return row

@app.post('/api/jobs/{jid}/codex-settings')
def change_codex_settings(jid: str, model: str=Form(''), reasoning_effort: str=Form('default'),
                          provider: str | None=Form(None), dslab_token_budget: int | None=Form(None),
                          dslab_max_output_tokens: int | None=Form(None), dslab_max_calls: int | None=Form(None)):
    if not db.job_row(jid):
        raise HTTPException(404, 'Unknown job')
    try:
        changed = db.set_job_codex_settings(jid, model, reasoning_effort, provider,
                                           dslab_token_budget, dslab_max_output_tokens, dslab_max_calls)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not changed:
        raise HTTPException(409, 'Pause the job first. Settings cannot change during a running task.')
    return db.job_row(jid)


@app.get('/api/providers')
def providers():
    from common.provider_settings import model_catalogue
    return {'providers': ['codex', 'dslab','litellm','minimax'], 'dslab_models': DSLAB_MODELS, 'dslab_efforts': DSLAB_EFFORTS,
            'catalogue':model_catalogue()}

@app.get('/agent-models.js')
def agent_models_script():
    return FileResponse(Path(__file__).parent/'agent-models.js',media_type='application/javascript')

@app.get('/api/jobs/{jid}/agent-models')
def agent_models(jid: str):
    from common.provider_settings import validate_role_models, resolve_role_details, model_catalogue, AGENT_ROLES
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    models=validate_role_models(json.loads(row.get('role_models') or '{}'),legacy=True)
    catalogue=model_catalogue()
    return {'status':row['status'],'editable':row['status'] in ('QUEUED','USER_PAUSED','QUOTA_PAUSED') and not row['cancel_requested'],
            'models':models,'resolved':{r:resolve_role_details(row,r) for r in AGENT_ROLES},
            'roles':list(AGENT_ROLES),'catalogue':catalogue}

@app.put('/api/jobs/{jid}/agent-models')
def change_agent_models(jid: str, value: dict=Body(...)):
    if not db.job_row(jid):raise HTTPException(404,'Unknown job')
    if set(value)!={'models'}:raise HTTPException(400,'Expected models object')
    try:changed=db.set_job_role_models(jid,value['models'])
    except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc)) from exc
    if not changed:raise HTTPException(409,'Pause the job before changing models')
    return agent_models(jid)


@app.get('/api/jobs/{jid}/usage')
def token_usage(jid: str):
    row = db.job_row(jid)
    if not row:
        raise HTTPException(404, 'Unknown job')
    try:
        report = provider_control('GET', '/internal/jobs/' + jid + '/usage')
        report={**report,'available':True}
    except GatewayError:
        report={'available':False,'detail':'API gateway unavailable. Existing totals have not been reset.'}
    codex,index_state=request_history.snapshot(ARTIFACTS,
        {'coder':AGENT_TASKS,'planner':Path(os.getenv('PLANNER_TASKS','/planner-tasks'))},row['project_id'],jid)
    return {**usage_accounting.merge_usage(report,codex), **index_state, 'active_provider':row['provider'],
            'active_role_providers':{r:resolve_role(row,r)['provider'] for r in ('planner','coder','reviewer')},
            'cost_is_estimate':True}


@app.get('/api/jobs/{jid}/requests')
def job_requests(jid: str, limit: int = 50, cursor: str | None = None):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    if not 1 <= limit <= 100:raise HTTPException(400,'limit must be between 1 and 100')
    codex,index_state=request_history.snapshot(ARTIFACTS,
        {'coder':AGENT_TASKS,'planner':Path(os.getenv('PLANNER_TASKS','/planner-tasks'))},row['project_id'],jid)
    def native_page(native_cursor,size):
        path='/internal/jobs/'+jid+'/requests?limit='+str(size)
        if native_cursor is not None:path+='&cursor='+quote(str(native_cursor),safe='')
        try:return provider_control('GET',path)
        except GatewayError:return {'available':False,'requests':[],'next_cursor':None}
    try:return {**request_history.page(jid,limit,cursor,codex,native_page),**index_state}
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@app.get('/api/jobs/{jid}/activity')
def job_activity(jid: str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    with db.connect() as con:
        recent=[dict(event) for event in con.execute(
            'SELECT id,created_at,text AS message FROM events WHERE job_id=? ORDER BY id DESC LIMIT 500',(jid,))]
    from common.memory_archive import read_json
    pipeline_state=read_json(ARTIFACTS/row['project_id']/jid,'pipeline/state.json') or {}
    return activity_snapshot(row,recent,AGENT_TASKS,pipeline_state=pipeline_state)

@app.get('/api/jobs/{jid}/events')
def events(jid:str, after:int=0):
    if not db.job_row(jid):raise HTTPException(404,'Unknown job')
    return db.events_list(jid,max(0,after))

@app.get('/api/jobs/{jid}/agent-history')
def agent_history(jid: str):
    row = db.job_row(jid)
    if not row:
        raise HTTPException(404, 'Unknown job')
    try:
        writable=list_attempts(ARTIFACTS, AGENT_TASKS, row['project_id'], jid)
        readonly=list_attempts(ARTIFACTS, Path(os.getenv('PLANNER_TASKS','/planner-tasks')),row['project_id'],jid)
        combined={item['id']:item for item in writable['attempts']}
        for item in readonly['attempts']:
            if item.get('role') in ('planner','reviewer') or item['id'] not in combined:combined[item['id']]=item
        return {**writable,'attempts':sorted(combined.values(),key=lambda x:(x.get('round',0),x.get('id',''))),
            'truncated':writable.get('truncated',False) or readonly.get('truncated',False)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.get('/api/jobs/{jid}/agent-history/{tid}')
def agent_history_detail(jid: str, tid: str, after: int=0, limit: int=100, stream: str=''):
    row = db.job_row(jid)
    if not row:
        raise HTTPException(404, 'Unknown job')
    try:
        box=AGENT_TASKS
        readonly=Path(os.getenv('PLANNER_TASKS','/planner-tasks'))
        # Prefer a read-only role's active mailbox log over its last archive
        # snapshot. read_attempt still validates project/job/attempt identity.
        if re.fullmatch(re.escape(jid)+r'-round[0-9]{2,16}(?:-[0-9a-f]{32})?',tid):
            if (readonly/'running'/(tid+'.json')).is_file() or (readonly/'requests'/(tid+'.json')).is_file():box=readonly
        return read_attempt(ARTIFACTS, box, row['project_id'], jid,
                            tid, after=after, limit=limit, stream=stream)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, 'Unknown agent attempt') from exc

@app.post('/api/jobs/{jid}/round-limit')
def change_round_limit(jid: str, max_rounds: int=Form(...)):
    try:
        db.validate_round_limit(max_rounds)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        row = db.set_job_round_limit(jid, max_rounds)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, 'Unknown job')
    return row


@app.post('/api/jobs/{jid}/extend-rounds')
def extend_rounds(jid: str, additional_rounds: int=Form(...)):
    if not 1 <= additional_rounds <= db.MAX_ROUND_LIMIT:
        raise HTTPException(400, 'additional_rounds must be a positive safe integer')
    try:
        row = db.extend_job_rounds(jid, additional_rounds)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, 'Unknown job')
    return row


@app.post('/api/jobs/{jid}/{action}')
def action(jid:str, action:str):
    try:
        row=db.control_job(jid,action)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    if not row:raise HTTPException(404,'Unknown job')
    return row

@app.get('/api/jobs/{jid}/files')
def files(jid:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    root=ARTIFACTS/row['project_id']/jid
    if not root.exists():return []
    items=[]
    for p in root.rglob('*'):
        if p.is_file() and not p.is_symlink():
            item={'path':p.relative_to(root).as_posix(),'size':p.stat().st_size,'type':p.suffix.lower(),
                  'url':f'/api/jobs/{jid}/files/{p.relative_to(root).as_posix()}'}
            if p.suffix.lower()=='.mp4':
                quality_file=p.parent/'video_capture.json'
                try:
                    if quality_file.is_file() and not quality_file.is_symlink() and quality_file.stat().st_size<=65536:
                        quality=json.loads(quality_file.read_text(encoding='utf-8'))
                        if quality.get('status') in ('PASS','FAIL','ERROR'):
                            item.update(media_status=quality['status'],media_reason=str(quality.get('reason',''))[:200])
                except (OSError,ValueError,AttributeError):pass
            items.append(item)
    return sorted(items,key=lambda a:a['path'])[:1000]


@app.get('/api/jobs/{jid}/media')
def media_files(jid:str,round_number:int|None=None,offset:int=0,limit:int=100):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    try:
        return media_catalog.page(ARTIFACTS/row['project_id']/jid,jid,
                                  round_number=round_number,offset=offset,limit=limit)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc

@app.get('/api/jobs/{jid}/files/{relpath:path}')
def file_download(jid:str,relpath:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    root=(ARTIFACTS/row['project_id']/jid).resolve()
    dest=(root/relpath).resolve()
    if not dest.is_relative_to(root) or not dest.is_file():raise HTTPException(404,'No such file')
    if dest.suffix.lower() not in {'.mp4','.png','.jpg','.jpeg','.json','.md','.log','.txt','.jar','.zip','.xml','.html','.jsonl'}:
        raise HTTPException(403,'Unsupported file type')
    # Build output is untrusted. Active reports must never execute in the dashboard origin.
    attachment = dest.suffix.lower() in {'.jar','.zip','.html','.xml'}
    headers={'X-Content-Type-Options':'nosniff',
             'Content-Security-Policy':"default-src 'none'; sandbox; frame-ancestors 'none'"}
    if dest.suffix.lower()=='.mp4':
        # Chromium's direct-media document loads this URL as its own video source.
        # default-src 'none' blocks that native player without an explicit media-src.
        # Keep scripts/frames prohibited; active HTML/XML retain the strict sandbox.
        headers['Content-Security-Policy']="default-src 'none'; media-src 'self'; frame-ancestors 'none'"
        headers['Cache-Control']='no-cache'
    return FileResponse(dest, filename=dest.name if attachment else None,
                        headers=headers)

@app.get('/api/jobs/{jid}/verification')
def verification(jid:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    p=ARTIFACTS/row['project_id']/jid/'latest_verification.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'status':'NOT_TESTED'}


@app.get('/api/jobs/{jid}/acceptance-summary')
def job_acceptance_summary(jid: str):
    state=job_acceptance(jid)
    return {key:state.get(key) for key in ('status','summary','task_summary',
        'report_round','verification_in_progress','history_summary')}


@app.get('/api/jobs/{jid}/acceptance')
def job_acceptance(jid: str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    project=PROJECTS/row['project_id']; workspace=project/'workspace'
    job_root=ARTIFACTS/row['project_id']/jid
    from common.memory_archive import read_json
    semantic=read_json(job_root,'latest_acceptance.json')
    if semantic and semantic.get('pipeline_version')=='v1':
        if row['status']=='RUNNING':semantic['verification_in_progress']=row['phase']=='verifying'
        semantic['report_round']=semantic.get('round')
        try:current=acceptance.source_fingerprint(workspace)
        except (OSError,ValueError):current=None
        semantic['source_current']=current==semantic.get('source_sha256')
        if not semantic['source_current']:
            acceptance.mark_stale(semantic,'Код изменён после проверки; предыдущие результаты сохранены в истории.')
        for requirement in semantic['requirements']:
            for scenario in requirement.get('scenarios',[]):
                relative=scenario.get('video')
                if relative:
                    try:
                        with _open(job_root,tuple(PurePosixPath(relative).parts)):pass
                        scenario['video_url']='/api/jobs/'+jid+'/files/'+quote(relative,safe='/')
                    except (OSError,ValueError):scenario['video_url']=None
        return semantic
    try:catalog=acceptance.build_catalog(acceptance.read_spec(project))
    except (OSError,ValueError,UnicodeError) as exc:
        raise HTTPException(409,'Cannot safely read original specification') from exc
    plan=acceptance.load_plan(workspace,catalog)
    state=acceptance.build_state(catalog,plan,None)
    current_hash=None
    try:
        saved=acceptance.read_json(job_root,'latest_acceptance.json',8*1024*1024)
        if (isinstance(saved,dict) and saved.get('spec_sha256')==catalog['spec_sha256']
                and {r['id'] for r in saved['requirements']}=={r['id'] for r in catalog['requirements']}):
            state=saved
            try:
                current_hash=acceptance.source_fingerprint(workspace)
            except (OSError,ValueError):
                current_hash=None
            if row['status']=='RUNNING' and row.get('phase')=='codex_development':
                acceptance.mark_stale(state,'Идёт новый раунд: результаты относятся к предыдущему состоянию кода.')
            elif current_hash!=state.get('source_sha256'):
                acceptance.mark_stale(state,'Код или план изменились после проверки. Нужен повторный запуск сценариев.')
    except FileNotFoundError:
        pass
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        state=acceptance.build_state(catalog,plan,None)
        state['plan_notes'].append('Сохранённые доказательства не прочитаны или не соответствуют исходникам.')
    progress={}
    if plan.get('scenarios') and not plan.get('notes'):
        from common.coverage import attach
        if current_hash is None:
            try:current_hash=acceptance.source_fingerprint(workspace)
            except (OSError,ValueError):current_hash=''
        attach(plan,catalog,job_root,current_hash or '')
        progress=acceptance_progress.load_progress(job_root,catalog,plan)
        acceptance_progress.decorate_state(state,catalog,plan,progress,current_hash or '')
    state['report_round']=state.get('round')
    state['verification_in_progress']=row['status']=='RUNNING'
    state['source_current']=bool(current_hash and current_hash==state.get('source_sha256')
        and not (row['status']=='RUNNING' and row.get('phase')=='codex_development'))
    hashes={}
    def evidence_file(round_folder, relative, *, digest=None):
        if (not isinstance(round_folder,str) or not re.fullmatch(r'round-[0-9]{2,16}',round_folder)
                or not isinstance(relative,str) or relative.startswith('/') or '\\' in relative):return None
        parts=relative.split('/')
        if any(not p or p in ('.','..') for p in parts):return None
        key=(round_folder,*parts)
        try:
            if key not in hashes:
                with _open(job_root,key) as handle:
                    if digest:
                        if os.fstat(handle.fileno()).st_size>4*1024*1024:return None
                        hashes[key]=hashlib.sha256(handle.read(4*1024*1024+1)).hexdigest()
                    else:hashes[key]=True
            if digest and hashes[key]!=digest:return None
        except (OSError,ValueError):return None
        return '/api/jobs/'+quote(jid,safe='')+'/files/'+quote('/'.join(key),safe='/')
    unresolved={f['id']:f for f in task_assignment.unresolved_failures(progress,plan)} if progress else {}
    historical_results={}
    for item in state['requirements']:
        for scenario in item.get('scenarios',[]):
            sid=scenario.get('id',''); folder=scenario.get('artifact_round')
            if not isinstance(sid,str) or not acceptance.ID.fullmatch(sid):continue
            if scenario.get('status')=='FAIL':
                from common.failure_details import hydrate
                definition=next((s for s in plan.get('scenarios',[]) if s['id']==sid),{})
                if definition:
                    scenario.update(hydrate(ARTIFACTS,row['project_id'],jid,definition,scenario))
                    if not state['source_current']:
                        scenario['failure_details'].update(historical=True,
                            source_round=scenario.get('attempt_round',state.get('report_round')))
            elif sid in unresolved:
                # Source changes can replace the visible scenario by STALE or
                # NOT_TESTED. Keep the last unresolved FAIL visible as history,
                # without changing the current verdict or claiming current proof.
                number=unresolved[sid].get('round')
                if type(number) is int and 0<number<=2**53-1:
                    if number not in historical_results:
                        try:historical_results[number]=acceptance.read_json(job_root,
                            f'round-{number:02d}/acceptance_results.json',8*1024*1024)
                        except (OSError,ValueError,TypeError):historical_results[number]={}
                    raw=next((s for s in historical_results[number].get('scenarios',[])
                        if isinstance(s,dict) and s.get('id')==sid and s.get('status')=='FAIL'),None)
                    if raw:
                        from common.failure_details import hydrate
                        proof=raw.get('assertion_evidence',{})
                        definition={'id':sid,'requirement_ids':raw.get('requirement_ids',[]),
                            'expected_tests':proof.get('expected_tests',[]) if isinstance(proof,dict) else []}
                        details=hydrate(ARTIFACTS,row['project_id'],jid,definition,raw,round_number=number)['failure_details']
                        scenario['failure_details']={**details,'historical':True,'source_round':number}
            base='acceptance/'+sid+'/'
            log=scenario.get('log'); video=scenario.get('video')
            scenario['log_url']=evidence_file(folder,log) if log==base+'test.log' else None
            scenario['video_url']=evidence_file(folder,video) if video==base+'minecraft_client_capture.mp4' else None
            if scenario.get('status')=='TEST_PASSED':
                proof=scenario.get('assertion_evidence',{})
                reports=proof.get('reports',[]) if isinstance(proof,dict) else []
                available=bool(reports)
                for report in reports:
                    path=report.get('file','') if isinstance(report,dict) else ''
                    digest=report.get('sha256') if isinstance(report,dict) else None
                    if (not path.startswith(base+'assertions/') or not isinstance(digest,str)
                            or not re.fullmatch('[0-9a-f]{64}',digest)
                            or not evidence_file(folder,path,digest=digest)):
                        available=False;break
                if not available or (item.get('video_required') and not scenario['video_url']):
                    scenario.update(status='NOT_TESTED',detail='Файлы доказательств отсутствуют или изменены.')
        if item.get('status')=='TEST_PASSED' and any(s.get('status')!='TEST_PASSED' for s in item.get('scenarios',[])):
            item.update(status='NOT_TESTED',detail='Нет полного набора доступных доказательств для пункта.')
    from common.coverage import attach,gate
    attach(plan,catalog,job_root,current_hash or '')
    gate(state,plan)
    requirements=state['requirements']
    state['summary']={'total':len(requirements),
        'test_passed':sum(r['status']=='TEST_PASSED' for r in requirements),
        'failed':sum(r['status']=='FAIL' for r in requirements),
        'not_tested':sum(r['status']=='NOT_TESTED' for r in requirements),
        'stale':sum(r['status']=='STALE' for r in requirements),
        'in_progress':sum(r['status']=='IN_PROGRESS' for r in requirements),
        'without_video':sum(r.get('video_required',False) and not any(s.get('video_url') for s in r.get('scenarios',[])) for r in requirements)}
    if state['summary']['test_passed']<len(requirements) and state['status']=='TESTS_PASSED_REVIEW_REQUIRED':
        state['status']='IN_PROGRESS'
    manual_review.decorate(state,plan)
    acceptance_tasks.annotate(state,catalog)
    assignment=task_assignment.read(job_root,catalog)
    if assignment:
        state['assignment']={k:v for k,v in assignment.items() if k not in ('completed_bindings','failure_memory')}
    return state

@app.get('/api/jobs/{jid}/backups')
def backups(jid:str):
    if not db.job_row(jid):raise HTTPException(404,'Unknown job')
    p=Path(os.getenv('LAB_BACKUPS','/backups'))/jid
    return [{'filename':x.name,'size':x.stat().st_size} for x in sorted(p.glob('*.tar.gz'))] if p.exists() else []

@app.get('/api/jobs/{jid}/s3')
def s3_items(jid:str):
    row=db.job_row(jid)
    if not row:raise HTTPException(404,'Unknown job')
    if not UPLOAD_STATE.exists():return []
    try:state=json.loads(UPLOAD_STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):return []
    return [x for x in state.get('objects',{}).values() if str(x.get('original','')).startswith(f"{row['project_id']}/{jid}/")]

@app.get('/api/jobs/{jid}/s3-link')
async def s3_link(jid:str,key:str):
    permitted={v.get('key') for v in s3_items(jid)}
    if key not in permitted:raise HTTPException(403,'Object not associated with job')
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r=await c.get('http://storage:8090/presign',params={'key':key})
            r.raise_for_status()
        return r.json()
    except Exception:raise HTTPException(503,'S3 signer not configured or unavailable')

@app.get('/healthz')
def health():return {'status':'ok'}

@app.get('/api/jobs/{jid}/backups/{filename}')
def backup_file(jid:str,filename:str):
    if not db.job_row(jid):raise HTTPException(404,'Unknown job')
    if '/' in filename or '\\' in filename or not filename.endswith('.tar.gz'):
        raise HTTPException(400,'Invalid backup name')
    root=(Path(os.getenv('LAB_BACKUPS','/backups'))/jid).resolve()
    dest=(root/filename).resolve()
    if not dest.is_relative_to(root) or not dest.is_file():raise HTTPException(404,'No such backup')
    return FileResponse(dest,filename=filename)
