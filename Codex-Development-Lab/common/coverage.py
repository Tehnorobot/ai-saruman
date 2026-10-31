"""Controller-owned coverage approval; executable success alone cannot close a REQ.

Source spans are a lossless inventory, not a semantic parser. A read-only reviewer
must assess every clause in each span against real test assertions. No model may
delete source spans or self-certify coverage through a workspace JSON file.
"""
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
from common.history import _open,_atomic

FILENAME='coverage-reviews.json'
MAX_BYTES=4*1024*1024

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def obligations(catalog):
    result={}
    for requirement in catalog.get('requirements',[]):
        source=(requirement.get('source') or {}).get('text',requirement.get('title',''))
        # Keep punctuation in its source span, avoid decimal/version separators.
        boundaries=[0]+[m.end() for m in re.finditer(r';\s*|(?<=[.!?])\s+(?=[А-ЯA-Z])|\n+',source)]+[len(source)]
        parts=[]
        for start,end in zip(boundaries,boundaries[1:]):
            if not source[start:end].strip():continue
            identity=digest([requirement['id'],start,end,source[start:end]])[:16]
            parts.append({'id':'condition-'+identity,'text':source[start:end],
                          'start':start,'end':end})
        result[requirement['id']]=parts
    return result

def _mapping(plan,rid):
    row=next((r for r in plan.get('requirements',[]) if r.get('id')==rid),{})
    return row.get('obligation_scenarios',{})

def review_binding(catalog,plan,rid,source_sha):
    return digest({'spec':catalog.get('spec_sha256'),'source':source_sha,'requirement':rid,
        'obligations':obligations(catalog).get(rid,[]),'mapping':_mapping(plan,rid),
        'scenarios':[s for s in plan.get('scenarios',[]) if rid in s.get('requirement_ids',[])]})

def _read(root):
    if root is None:return {}
    try:
        with _open(Path(root),(FILENAME,)) as stream:raw=stream.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:return {}
        value=json.loads(raw)
        return value if isinstance(value,dict) and value.get('schema_version')==1 else {}
    except (OSError,ValueError,TypeError,RecursionError):return {}

def attach(plan,catalog,job_root,source_sha):
    saved=_read(job_root);reviews=saved.get('requirements',{})
    if not isinstance(reviews,dict):reviews={}
    rows={}
    scenarios={s['id']:s for s in plan.get('scenarios',[])}
    for rid,conditions in obligations(catalog).items():
        record=reviews.get(rid,{})
        if not isinstance(record,dict):record={}
        valid=(record.get('binding')==review_binding(catalog,plan,rid,source_sha)
               and saved.get('spec_sha256')==catalog.get('spec_sha256'))
        historical_valid=(saved.get('spec_sha256')==catalog.get('spec_sha256')
            and isinstance(record.get('source_sha256'),str)
            and record.get('binding')==review_binding(catalog,plan,rid,record['source_sha256']))
        approved=record.get('covered_ids',[]) if valid else []
        reviewed={r.get('id'):r for r in record.get('obligations',[]) if isinstance(r,dict)} if valid else {}
        mapping=_mapping(plan,rid);details=[]
        for condition in conditions:
            refs=mapping.get(condition['id'],[]) if isinstance(mapping,dict) else []
            refs=[sid for sid in refs if isinstance(sid,str) and sid in scenarios and rid in scenarios[sid].get('requirement_ids',[])] if isinstance(refs,list) else []
            details.append({**condition,'scenario_ids':refs,
                'reviewed':bool(refs and condition['id'] in approved),
                'review_status':'covered' if refs and condition['id'] in approved else 'missing' if reviewed.get(condition['id'],{}).get('verdict')=='missing' else 'pending',
                'reason':'Покрытие проверено независимым планировщиком; результат теста учитывается отдельно.'
                    if refs and condition['id'] in approved else
                    str(reviewed[condition['id']].get('reason','')) if condition['id'] in reviewed else
                    'Нет сценария для исходного условия.' if not refs else 'Полнота проверок этого условия ещё не подтверждена.'})
        missing=[c['id'] for c in details if not c['reviewed']]
        rows[rid]={'complete':bool(details) and not missing,'obligations':details,'missing_ids':missing,
                   'historically_complete':bool(details) and historical_valid and all(
                       c['id'] in record.get('covered_ids',[]) and c['scenario_ids'] for c in details),
                   'review_round':record.get('round') if valid else None,
                   'revision':review_binding(catalog,plan,rid,source_sha)}
    plan['_coverage']=rows
    return plan

def gate(state,plan):
    if not plan.get('_coverage_required'):return state
    for item in state.get('requirements',[]):
        coverage=plan.get('_coverage',{}).get(item['id'],{})
        item['coverage']=coverage or {'complete':False,'obligations':[],'missing_ids':[]}
        if item.get('status')=='TEST_PASSED' and not coverage.get('complete'):
            item.update(status='IN_PROGRESS',detail='Связанные сценарии прошли. Полное покрытие исходного требования не подтверждено.')
    summary=state.setdefault('summary',{})
    for status,key in [('TEST_PASSED','test_passed'),('FAIL','failed'),('NOT_TESTED','not_tested'),('STALE','stale'),('IN_PROGRESS','in_progress')]:
        summary[key]=sum(r.get('status')==status for r in state.get('requirements',[]))
    if state.get('status')=='TESTS_PASSED_REVIEW_REQUIRED' and summary.get('test_passed')!=len(state.get('requirements',[])):
        state['status']='IN_PROGRESS'
    return state

DIAGNOSTIC_ROOT_FILES = frozenset({
    'round-memory.json', 'agent-handoff.json', 'document-index.json',
    'acceptance-plan.json', 'acceptance-catalog.json', 'acceptance-tasks.json',
    'ТЗ.md', 'build.gradle', 'build.gradle.kts', 'settings.gradle',
    'settings.gradle.kts', 'gradle.properties',
})


def read_evidence(workspace,reference,observed_text,*,diagnostic=False):
    if not isinstance(reference,dict):raise ValueError('Evidence reference must be an object')
    path=reference.get('path');start=reference.get('start_line');end=reference.get('end_line');excerpt=reference.get('excerpt')
    if (not isinstance(path,str) or not path or len(path)>512 or '\\' in path or ':' in path
            or any(p in ('','.','..') for p in path.split('/'))):
        raise ValueError('Invalid evidence path: use a relative workspace path without traversal, backslashes or colon')
    allowed=path.startswith(('src/','.lab-feedback/'))
    if diagnostic:
        allowed=allowed or path in DIAGNOSTIC_ROOT_FILES or (
            path.startswith('tools/') and path.endswith('.py'))
        allowed=allowed or bool(re.fullmatch(
            r'\.lab-memory/(?:index|pages/(?:errors|notes|rounds)-[0-9]{6,16}|errors/[0-9a-f]{64}|notes/[0-9]{1,16}-[0-9a-f]{64}|rounds/[0-9]{2,16}(?:(?:-[0-9a-f]{64})?-verification)?)\.json',path))
    if not allowed:
        raise ValueError('Evidence path is outside '+('diagnostic' if diagnostic else 'coverage')+' sources: '+path)
    if type(start) is not int or type(end) is not int or not 1<=start<=end<=start+120:
        raise ValueError('Invalid evidence start_line/end_line: require integers with 1 <= start <= end and at most 121 lines')
    if not isinstance(excerpt,str) or not 12<=len(excerpt)<=2000 or not excerpt.strip():
        raise ValueError('Invalid evidence excerpt: require 12 to 2000 characters of exact source text')
    with _open(workspace,tuple(path.split('/'))) as stream:raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Evidence source exceeds limit')
    lines=raw.decode('utf-8').splitlines()
    selected='\n'.join(lines[start-1:end])
    if end>len(lines) or excerpt not in selected:raise ValueError('Evidence excerpt does not match source lines')
    # Observations come only from completed tool results, never final model prose.
    quote=' '.join(excerpt.split())
    # The quote is source text, never numbered display text. Keep it intact.
    # Parse nl's TAB delimiter first: a following Java || is code, not a
    # second display delimiter. The old greedy whitespace branch ate its |.
    displayed=[]
    for line in observed_text.splitlines():
        numbered=re.match(r'^ *\d+\t',line)
        if numbered is None:numbered=re.match(r'^ *\d+ *(?:: ?|\|(?= ) ?)',line)
        displayed.append(line[numbered.end():] if numbered else line)
    if (not quote or (quote not in ' '.join(observed_text.split())
                      and quote not in ' '.join('\n'.join(displayed).split()))):
        raise ValueError('Referenced evidence was not observed through tools')
    return {'path':path,'start_line':start,'end_line':end,'excerpt':excerpt,'sha256':hashlib.sha256(raw).hexdigest()}

def observed_tool_text(logfile):
    chunks=[];size=0
    with _open(Path(logfile).parent,(Path(logfile).name,)) as stream:
        for line in stream:
            if len(line)>1024*1024:continue
            try:event=json.loads(line)
            except (ValueError,UnicodeError):continue
            if not isinstance(event,dict):continue
            item=event.get('item',{})
            if event.get('type')!='item.completed' or not isinstance(item,dict):continue
            # A completed shell command can read a file successfully and then
            # exit nonzero (e.g. a following rg has no matches). Its output was
            # still observed. This is citation provenance, never test success.
            text=item.get('text','') if item.get('type')=='tool_result' else item.get('aggregated_output','') if item.get('type')=='command_execution' and type(item.get('exit_code')) is int else ''
            if not isinstance(text,str):continue
            size+=len(text)
            if size>8*1024*1024:break
            chunks.append(text)
    return '\n'.join(chunks)

def apply_reviews(workspace,job_root,catalog,plan,source_sha,reviews,*,observed_text='',round_number=0):
    if not isinstance(reviews,list) or len(reviews)>12:raise ValueError('Too many coverage reviews')
    saved=_read(job_root)
    if saved.get('spec_sha256')!=catalog.get('spec_sha256'):saved={}
    records=saved.get('requirements',{})
    if not isinstance(records,dict):records={}
    inventory=obligations(catalog);scenario_map={s['id']:s for s in plan.get('scenarios',[])}
    accepted=[];rejected=[]
    for review in reviews:
        rid=review.get('requirement_id') if isinstance(review,dict) else None
        try:
            if rid not in inventory:raise ValueError('Unknown reviewed requirement')
            rows=review.get('obligations')
            if (not isinstance(rows,list) or len(rows)!=len(inventory[rid])
                    or len({r.get('id') for r in rows if isinstance(r,dict)})!=len(rows)
                    or {r.get('id') for r in rows if isinstance(r,dict)}!={c['id'] for c in inventory[rid]}):
                raise ValueError('Review must enumerate every immutable source condition exactly once')
            covered=[];evidence=[]
            for row in rows:
                if row.get('verdict') not in ('covered','missing') or not isinstance(row.get('reason'),str) or not 20<=len(row['reason'])<=1600:
                    raise ValueError('Each condition needs a verdict and substantive explanation')
                if row['verdict']=='missing':continue
                refs=row.get('scenario_ids')
                if (not isinstance(refs,list) or not refs or len(set(refs))!=len(refs)
                        or sorted(refs)!=sorted(_mapping(plan,rid).get(row['id'],[]))
                        or any(sid not in scenario_map or rid not in scenario_map[sid]['requirement_ids'] for sid in refs)):
                    raise ValueError('Reviewed scenarios must match the proposed source-condition mapping')
                citations=row.get('evidence_refs')
                if not isinstance(citations,list) or not 1<=len(citations)<=8:raise ValueError('Coverage needs test-source references')
                checked=[read_evidence(workspace,c,observed_text) for c in citations]
                if not any(c['path'].startswith(('src/test/','src/gametest/','src/integrationTest/')) for c in checked):
                    raise ValueError('Coverage needs actual executable test assertions, not only runtime code or logs')
                evidence.extend(checked);covered.append(row['id'])
            records[rid]={'binding':review_binding(catalog,plan,rid,source_sha),'covered_ids':covered,
                          'reviewer_role':'planner','source_sha256':source_sha,
                          'round':round_number,'evidence_refs':evidence,'obligations':rows}
            accepted.append(rid)
        except (ValueError,TypeError,KeyError,OSError,UnicodeError) as error:
            rejected.append({'requirement_id':rid,'reason':str(error)[:300]})
    if accepted:
        root=Path(job_root)
        if root.is_symlink():raise ValueError('Linked controller review root')
        _atomic(root/FILENAME,{'schema_version':1,'spec_sha256':catalog['spec_sha256'],'requirements':records})
    attach(plan,catalog,job_root,source_sha)
    return {'accepted':accepted,'rejected':rejected}
