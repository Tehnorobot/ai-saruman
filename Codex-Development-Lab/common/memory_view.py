"""Read-only, bounded public view of controller memory and archived rounds."""
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from common.history import _open, PRIVATE_FIELDS
from common.error_history import reconcile_legacy_errors

LIMIT=1024*1024


def _root(base,pid,jid):
    if any(not re.fullmatch('[0-9a-f]{14}',v) for v in (pid,jid)):
        raise ValueError('Invalid project/job identifier')
    root=Path(base)/pid/jid
    if root.is_symlink() or root.parent.is_symlink():
        raise ValueError('Linked artifact roots are not allowed')
    return root


def _text(value,n=1000):
    return value[:n] if isinstance(value,str) else ''


def _read(root,name,transform,limit=LIMIT):
    item={'state':'missing','source':name,'modified_at':None,'data':None}
    try:
        with _open(root,tuple(name.split('/'))) as f:
            stat=os.fstat(f.fileno())
            item['modified_at']=datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat()
            if stat.st_size>limit:
                item['state']='too_large';return item
            value=json.loads(f.read(limit+1))
        if not isinstance(value,dict):raise ValueError('Not an object')
        item.update(state='available',data=transform(value))
    except FileNotFoundError:pass
    except (ValueError,UnicodeError,RecursionError,TypeError):item['state']='invalid'
    except OSError:item['state']='unavailable'
    return item


def _notes(v):
    out={k:_text(v.get(k),1800) for k in ('summary','source','provenance','spec_sha256')}
    for k in ('next_steps','decisions'):
        values=v.get(k,[])
        out[k]=[_text(x,400) for x in values[:8] if isinstance(x,str)] if isinstance(values,list) else []
    values=v.get('errors_and_solutions',[])
    out['errors_and_solutions']=[{k:_text(x.get(k),600) for k in ('error','solution','evidence')}
        for x in values[:8] if isinstance(x,dict)] if isinstance(values,list) else []
    return out


def _ids(value,limit=64):
    return [_text(x,128) for x in value[:limit] if isinstance(x,str)] if isinstance(value,list) else []


def _assignment(value):
    if not isinstance(value,dict):return None
    result={k:_text(value.get(k),160) for k in
        ('current_task_id','status','selection_reason')}
    for key in ('pending_task_count','unresolved_failure_count','completed_task_count',
                'regression_requirement_count'):
        if type(value.get(key)) is int:result[key]=value[key]
    task=value.get('current_task')
    result['current_task']=None
    if isinstance(task,dict):
        result['current_task']={k:_text(task.get(k),300) for k in ('id','title')}
        for key in ('requirement_ids','unresolved_requirement_ids','scenario_ids'):
            result['current_task'][key]=_ids(task.get(key))
        for key in ('requirement_count','unresolved_requirement_count','omitted_unresolved_count'):
            if type(task.get(key)) is int:result['current_task'][key]=task[key]
    return result


def _action(value):
    if not isinstance(value,dict):return None
    result={k:_text(value.get(k),1200 if k in ('diagnostic','source') else 160)
            for k in ('kind','target','diagnostic','source')}
    result['requirement_ids']=_ids(value.get('requirement_ids'))
    notes=value.get('diagnostics')
    result['diagnostics']=[_text(x,600) for x in notes[:4] if isinstance(x,str)] if isinstance(notes,list) else []
    return result


def _task_snapshot(value):
    return {'task_assignment':_assignment(value.get('assignment')),
            'round_action':_action(value.get('round_action')),
            'round_action_round':value.get('round_action_round')
                if type(value.get('round_action_round')) is int else None}


def current_task(base,pid,jid):
    """Cheap polling view; never scan history or expose arbitrary memory fields."""
    return _read(_root(base,pid,jid),'round-memory.json',_task_snapshot)


def _memory(v):
    out={k:v.get(k) for k in ('schema_version','last_processed_round','snapshot_round') if type(v.get(k)) is int}
    out.update({k:_text(v.get(k),100) for k in ('spec_sha256','snapshot_phase','snapshot_at')})
    out.update(_task_snapshot(v))
    if isinstance(v.get('archive'),dict):out['archive']=_archive_public(v['archive'])
    if isinstance(v.get('memory_archive'),dict):out['memory_archive']=_archive_public(v['memory_archive'])
    if not isinstance(v.get('errors'),list):raise ValueError('Invalid memory errors')
    out['errors']=[]
    for e in v['errors'][-30:]:
        if not isinstance(e,dict):continue
        row={k:_text(e.get(k),1000) for k in ('label','detail','status','resolution')}
        row.update({k:e[k] for k in ('first_round','last_round','repeat_count','resolved_round') if type(e.get(k)) is int})
        out['errors'].append(row)
    out['recent_agent_notes']=[]
    for note in v.get('recent_agent_notes',[]) [-8:] if isinstance(v.get('recent_agent_notes'),list) else []:
        if isinstance(note,dict) and isinstance(note.get('claims'),dict):
            out['recent_agent_notes'].append({'source_round':note.get('source_round') if type(note.get('source_round')) is int else None,
                'provenance':'UNVERIFIED agent claims','claims':_notes(note['claims'])})
    return out


def _counts(v):
    return {k:v[k] for k in ('total','test_passed','failed','not_tested','without_video') if type(v.get(k)) is int}


def _verification(v):
    checks=[]
    for c in v.get('checks',[])[:16] if isinstance(v.get('checks'),list) else []:
        if isinstance(c,dict):
            checks.append({'label':_text(c.get('label'),100),'status':_text(c.get('status'),40),
                'detail':_text(c.get('detail'),1200),'tail':_text(c.get('tail'),5000)[-1200:],
                'exit_code':c.get('exit_code') if type(c.get('exit_code')) is int else None})
    return {'status':_text(v.get('status'),80),'checks':checks,
            'task_assignment':_assignment(v.get('task_assignment'))}


def _round_task(root,name,number,start=None,verification=None):
    """Use the immutable start snapshot, never end-of-round/next assignment."""
    if start is None:start=_read(root,name+'/round-memory-start.json',_task_snapshot)
    data=start.get('data') or {}
    if start['state']=='available' and (data.get('task_assignment') or data.get('round_action')):
        action_round=data.get('round_action_round')
        # A mismatched action may describe the next round; do not relabel it.
        action=data.get('round_action') if action_round in (None,number) else None
        return {'state':'available','source':start['source'],
            'data':{'assignment':data.get('task_assignment'),'round_action':action,
                'round_action_round':action_round if action is not None else None},
            'provenance':'Назначение перед работой: исторический снимок начала раунда.'}
    if verification is None:verification=_read(root,name+'/verification_complete.json',_verification)
    assignment=(verification.get('data') or {}).get('task_assignment')
    if verification['state']=='available' and assignment:
        return {'state':'available','source':verification['source'],
            'data':{'assignment':assignment,'round_action':None,'round_action_round':None},
            'provenance':'Назначение из независимого отчёта проверки; снимок начала раунда недоступен.'}
    return {'state':'missing','source':name+'/round-memory-start.json','data':None,
            'provenance':'Историческое назначение неизвестно. Текущая память его не заменяет.'}


def _acceptance(v):
    scenarios={}
    for requirement in v.get('requirements',[])[:2000] if isinstance(v.get('requirements'),list) else []:
        if not isinstance(requirement,dict):continue
        for scenario in requirement.get('scenarios',[])[:64] if isinstance(requirement.get('scenarios'),list) else []:
            if not isinstance(scenario,dict):continue
            sid=_text(scenario.get('id'),64)
            if sid and (sid in scenarios or len(scenarios)<100):
                scenarios[sid]={'id':sid,'status':_text(scenario.get('status'),40),
                    'test_status':_text(scenario.get('test_status'),40),'has_video':bool(scenario.get('video'))}
                if scenario.get('status')=='FAIL':
                    from common.failure_details import summarize
                    proof=scenario.get('assertion_evidence',{})
                    definition={'id':sid,'requirement_ids':scenario.get('requirement_ids',[]),
                        'expected_tests':proof.get('expected_tests',[]) if isinstance(proof,dict) else []}
                    scenarios[sid]['failure_details']=summarize(definition,scenario)
    return {'summary':_counts(v.get('summary',{})) if isinstance(v.get('summary'),dict) else {},
            'source_sha256':_text(v.get('source_sha256'),64),'spec_sha256':_text(v.get('spec_sha256'),64),
            'scenarios':list(scenarios.values())}


def _acceptance_details(base,pid,jid,number=None):
    def transform(value):
        from common.failure_details import hydrate
        requirements=value.get('requirements',[])
        for requirement in requirements[:2000] if isinstance(requirements,list) else []:
            if not isinstance(requirement,dict):continue
            scenarios=requirement.get('scenarios',[])
            for scenario in scenarios[:64] if isinstance(scenarios,list) else []:
                if not isinstance(scenario,dict) or scenario.get('status')!='FAIL':continue
                sid=scenario.get('id')
                if not isinstance(sid,str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',sid):continue
                proof=scenario.get('assertion_evidence',{})
                definition={'id':sid,'requirement_ids':scenario.get('requirement_ids',[]),
                    'expected_tests':proof.get('expected_tests',[]) if isinstance(proof,dict) else []}
                scenario.update(hydrate(base,pid,jid,definition,scenario,round_number=number))
        return _acceptance(value)
    return transform


def _summary(root,name):
    result={'state':'missing','source':name,'text':'','provenance':'UNVERIFIED agent claims'}
    try:
        with _open(root,tuple(name.split('/'))) as f:
            if os.fstat(f.fileno()).st_size>2*LIMIT:
                result['state']='too_large';return result
            while True:
                line=f.readline(LIMIT+1)
                if not line:break
                if len(line)>LIMIT:result['state']='too_large';break
                try:e=json.loads(line)
                except (ValueError,UnicodeError,RecursionError):continue
                if not isinstance(e,dict):continue
                item=e.get('item',{})
                if (isinstance(item,dict) and item.get('type')=='agent_message'
                        and item.get('agent_id',e.get('agent_id','main'))=='main'):
                    result.update(state='available',text=_text(item.get('text'),3000))
    except FileNotFoundError:pass
    except OSError:result['state']='unavailable'
    return result


def overview(base,pid,jid):
    root=_root(base,pid,jid)
    current=_read(root,'round-memory.json',_memory)
    rounds=[];truncated=False
    if root.exists():
        with os.scandir(root) as entries:
            for index,e in enumerate(entries):
                if index>=10000:truncated=True;break
                m=re.fullmatch('round-([0-9]{2,16})',e.name)
                if m and e.is_dir(follow_symlinks=False):rounds.append((int(m[1]),e.name))
    rounds.sort(reverse=True)
    # Project legacy statuses from canonical completed reports without rewriting
    # artifacts while the worker is active. Historical round snapshots stay intact.
    if current.get('data'):
        for number,name in sorted(rounds[:30]):
            report=_read(root,name+'/verification_complete.json',lambda value:value)
            value=report.get('data') or {}
            checks=list(value.get('checks') or [])
            for scenario in (value.get('acceptance_results') or {}).get('scenarios',[]):
                if isinstance(scenario,dict):checks.append({'label':'scenario:'+str(scenario.get('id','')),
                    'status':'PASS' if scenario.get('status')=='TEST_PASSED' else scenario.get('status')})
            reconcile_legacy_errors(current['data'],number,checks)
    truncated=truncated or len(rounds)>2000
    result=[]
    for number,name in rounds[:2000]:
        # Listing uses only tiny completion markers, not all acceptance catalogs.
        v=_read(root,name+'/verification_complete.json',_verification)
        snapshot=_read(root,name+'/round-memory.json',lambda x:{})
        start=_read(root,name+'/round-memory-start.json',_task_snapshot)
        task=_round_task(root,name,number,start,v)['data'] or {}
        title=(((task.get('assignment') or {}).get('current_task') or {}).get('title',''))
        result.append({'round':number,'verification_status':(v['data'] or {}).get('status','NOT_TESTED'),
            'memory_available':snapshot['state']=='available','start_memory_available':start['state']=='available',
            'checkpoint_available':v['state']=='available','modified_at':v['modified_at'],
            'task_title':title,'action_target':(task.get('round_action') or {}).get('target','')})
    return {'schema_version':1,'current':current,'rounds':result,'truncated':truncated,'limit':2000,
            'archive':_read(root,'memory-archive/index.json',_archive_public),
            'latest_acceptance':_read(root,'latest_acceptance.json',_acceptance_details(base,pid,jid)),
            'latest_verification':_read(root,'latest_verification.json',_verification),
            'retention_note':'Здесь показана краткая рабочая сводка. Полный архив ошибок и заметок доступен в разделах «Все ошибки» и «Все заметки». Старые записи не удаляются из-за возраста или количества. Полные отчёты сохранены по раундам; заметки агента не являются доказательством PASS.'}


def _archive_public(value):
    """Archive data is untrusted display text; never expose credential fields."""
    private=PRIVATE_FIELDS|{'password','secret','capability','thinking','signature_delta'}
    if isinstance(value,dict):
        return {k:_archive_public(v) for k,v in value.items() if k.lower() not in private}
    if isinstance(value,list):return [_archive_public(v) for v in value]
    return value


def archive_page(base,pid,jid,kind,page=1):
    if kind not in ('errors','notes','rounds') or type(page) is not int or not 1<=page<=10**9:
        raise ValueError('Invalid archive page')
    root=_root(base,pid,jid)
    return {'kind':kind,'page_number':page,
        'index':_read(root,'memory-archive/index.json',_archive_public),
        'page':_read(root,f'memory-archive/pages/{kind}-{page:06d}.json',_archive_public)}


def archive_record(base,pid,jid,path):
    if not isinstance(path,str):raise ValueError('Invalid archive record')
    if path.startswith('.lab-memory/'):path=path[len('.lab-memory/'):]
    if not re.fullmatch(r'(?:errors/[0-9a-f]{64}|notes/[0-9]{1,16}-[0-9a-f]{64}|rounds/[0-9]{2,16}(?:(?:-[0-9a-f]{64})?-verification)?|pages/(?:errors|notes|rounds)-[0-9]{6,16})\.json',path):
        raise ValueError('Invalid archive record')
    return _read(_root(base,pid,jid),'memory-archive/'+path,_archive_public,limit=8*LIMIT)


def round_detail(base,pid,jid,number):
    root=_root(base,pid,jid)
    if type(number) is not int or not 1<=number<=2**53-1:raise ValueError('Invalid round')
    name=f'round-{number:02d}'
    folder=root/name
    if not folder.is_dir() or folder.is_symlink():raise FileNotFoundError('Unknown round')
    memory=_read(root,name+'/round-memory.json',_memory)
    if memory['state']=='missing':memory=_read(root,name+'/round-memory-start.json',_memory)
    current=_read(root,'round-memory.json',_memory)
    fallback=current
    if current['data'] is not None:
        fallback={**current,'data':{**current['data'],
            'errors':[e for e in current['data']['errors'] if e.get('first_round',number+1)<=number],
            'recent_agent_notes':[e for e in current['data']['recent_agent_notes'] if e.get('source_round')==number]}}
    verification=_read(root,name+'/verification_complete.json',_verification)
    return {'schema_version':1,'round':number,'memory':memory,
        'round_task':_round_task(root,name,number,verification=verification),
        'current_memory_for_round':{**fallback,'provenance':'Текущая память; это не исторический снимок выбранного раунда.'},
        'handoff':_read(root,name+'/agent-handoff.json',_notes),
        'verification':verification,
        'acceptance':_read(root,name+'/acceptance.json',_acceptance_details(base,pid,jid,number)),
        'development':_read(root,name+'/development_complete.json',lambda v:{'kind':
            'verification_only' if v.get('kind')=='verification_only' else 'model'}),
        'agent':_read(root,name+'/codex_result.json',lambda v:{k:_text(v.get(k),100) for k in ('status','selected_model')}),
        'summary':_summary(root,name+'/codex.jsonl'),
        'retention_note':'Отчёты проверок независимы; заметки агента не подтверждают прохождение тестов.'}
