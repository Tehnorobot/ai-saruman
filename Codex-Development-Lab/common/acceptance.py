"""Source-backed requirements and evidence, separate from model implementation claims."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from common.history import _open

MAX_JSON = 1024 * 1024
MAX_ITEMS = 2000
MAX_SCENARIOS_PER_ROUND = 4
KINDS = {'gameplay', 'unit', 'integration', 'manual'}
ID = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')
TASK = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,95}')
COVERAGE_NOTE = ('Пункты извлечены из исходного ТЗ. Названия, критерии и тесты уточняет агент. '
    'Успех запуска и видео не доказывают полноту и правильность самих проверок; '
    '«Тесты пройдены» требует проверки соответствия критерия исходному требованию.')


def read_json(root, relative, max_bytes=MAX_JSON):
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(p in ('.', '..') for p in parts):
        raise ValueError('Invalid acceptance path')
    with _open(root, parts) as handle:
        if os.fstat(handle.fileno()).st_size > max_bytes:
            raise ValueError('Acceptance document exceeds size limit')
        raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError('Acceptance document exceeds size limit')
    return json.loads(raw.decode('utf-8-sig'))


def read_spec(project_root):
    with _open(project_root,('ТЗ.md',)) as handle:
        raw=handle.read(MAX_JSON+1)
        if len(raw)>MAX_JSON:raise ValueError('Specification exceeds size limit')
    return raw.decode('utf-8-sig')


def write_workspace_json(workspace, filename, document):
    if filename not in ('acceptance-catalog.json','acceptance-request.json',
                        'round-memory.json','document-index.json','acceptance-tasks.json','acceptance-plan.json'):
        raise ValueError('Invalid acceptance workspace document')
    workspace=Path(workspace)
    if workspace.is_symlink():raise ValueError('Workspace must be a real directory')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=workspace,
                prefix='.lab-acceptance-',suffix='.tmp',delete=False) as handle:
            temporary=handle.name; json.dump(document,handle,ensure_ascii=False,indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary,workspace/filename)
    finally:
        if temporary and os.path.exists(temporary):os.unlink(temporary)


def build_catalog(spec_text):
    if not isinstance(spec_text, str) or len(spec_text.encode('utf-8')) > MAX_JSON:
        raise ValueError('Specification exceeds size limit')
    lines = spec_text.splitlines()
    sections, units, start, pending, fence = [], [], None, [], None
    def flush():
        nonlocal start, pending
        if not pending:
            return
        text = '\n'.join(pending).rstrip()
        if text:
            section = ' / '.join(sections)
            digest = hashlib.sha256((section+'\n'+text).encode()).hexdigest()[:10]
            units.append({'id': f'REQ-{start:05d}-{digest}',
                'title': re.sub(r'^\s*(?:[-*+] |\d+[.)]\s+)', '', text).splitlines()[0][:200],
                'section': section, 'source': {'start_line': start,
                    'end_line': start+len(pending)-1, 'text': text},
                'kind': 'manual', 'expected': '', 'status': 'NOT_TESTED', 'scenario_ids': []})
        pending, start = [], None
    for number, line in enumerate(lines, 1):
        heading = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', line)
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                flush(); fence = marker[1][0]
            elif marker[1][0] == fence:
                pending.append(line); flush(); fence = None; continue
        if fence is None and heading:
            flush(); depth=len(heading[1]); sections=sections[:depth-1]
            while len(sections)<depth-1: sections.append('')
            sections.append(heading[2]); continue
        if fence is None and not line.strip():
            flush(); continue
        if fence is None and re.match(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)',line):
            flush()
        if start is None: start=number
        pending.append(line)
    flush()
    truncated = len(units)>MAX_ITEMS
    catalog={'schema_version': 1, 'spec_sha256': hashlib.sha256(spec_text.encode()).hexdigest(),
            'requirements': units[:MAX_ITEMS], 'truncated': truncated, 'coverage_note': COVERAGE_NOTE}
    from common.coverage import obligations
    catalog['source_obligations']=obligations(catalog)
    return catalog


def _text(value, maximum, *, required=False):
    if (not isinstance(value,str) or len(value)>maximum or (required and not value.strip())
            or any(ord(c)<32 and c not in '\n\t' for c in value)):
        raise ValueError('Invalid acceptance text')
    return value


def load_plan(workspace, catalog):
    empty={'requirements': [], 'scenarios': [], 'notes': [], '_coverage_required':True}
    try:
        raw=read_json(workspace,'acceptance-plan.json')
    except FileNotFoundError:
        empty['notes'].append('Агент ещё не подготовил acceptance-plan.json. Пункты ТЗ не проверены.')
        return empty
    except (OSError,ValueError,UnicodeError,RecursionError) as exc:
        empty['notes'].append('План приёмки не прочитан: '+str(exc)[:200]); return empty
    if (not isinstance(raw,dict) or type(raw.get('schema_version')) is not int or raw.get('schema_version')!=1
            or raw.get('spec_sha256')!=catalog['spec_sha256']):
        empty['notes'].append('План относится к другому ТЗ или имеет неизвестный формат.'); return empty
    known={item['id'] for item in catalog['requirements']}
    from common.coverage import obligations
    source_conditions=catalog.get('source_obligations') or obligations(catalog)
    try:
        requirements=raw.get('requirements',[]); scenarios=raw.get('scenarios',[])
        if not isinstance(requirements,list) or len(requirements)>MAX_ITEMS:
            raise ValueError('Too many requirements')
        if not isinstance(scenarios,list) or len(scenarios)>MAX_ITEMS:
            raise ValueError('Too many scenarios')
        normalized=[]; seen=set()
        for item in requirements:
            if not isinstance(item,dict) or item.get('id') not in known or item['id'] in seen:
                raise ValueError('Unknown or duplicate requirement ID')
            seen.add(item['id'])
            kind=item.get('kind','manual')
            ids=item.get('scenario_ids',[])
            if kind not in KINDS or not isinstance(ids,list) or len(ids)>64:
                raise ValueError('Invalid requirement kind or scenarios')
            if any(not isinstance(s,str) or len(s)>64 or not ID.fullmatch(s) for s in ids) or len(set(ids))!=len(ids):
                raise ValueError('Invalid scenario identifier')
            normalized.append({'id':item['id'],'title':_text(item.get('title',''),300,required=True),
                'expected':_text(item.get('expected',''),4000,required=True),'kind':kind,'scenario_ids':ids})
            mapping=item.get('obligation_scenarios',{})
            known_conditions={c['id'] for c in source_conditions.get(item['id'],[])}
            if (not isinstance(mapping,dict) or any(oid not in known_conditions or not isinstance(refs,list)
                    or len(refs)>64 or any(sid not in ids for sid in refs) or len(set(refs))!=len(refs)
                    for oid,refs in mapping.items())):
                raise ValueError('Invalid source-condition to scenario mapping')
            normalized[-1]['obligation_scenarios']=mapping
            if 'manual_review' in item:
                from common.manual_review import validate
                if kind!='manual' or ids:raise ValueError('Executable requirement cannot be deferred to a user')
                normalized[-1]['manual_review']=validate(item['manual_review'])
        by_id={item['id']:item for item in normalized}; checked=[]; seen=set()
        for scenario in scenarios:
            if not isinstance(scenario,dict): raise ValueError('Invalid scenario')
            sid=scenario.get('id'); task=scenario.get('task',scenario.get('gradle_task')); refs=scenario.get('requirement_ids')
            if 'task' in scenario and 'gradle_task' in scenario and scenario['task']!=scenario['gradle_task']:
                raise ValueError('Conflicting task and gradle_task; use task for the Gradle task name')
            if not isinstance(sid,str) or len(sid)>64 or not ID.fullmatch(sid) or sid in seen:
                raise ValueError('Invalid or duplicate scenario identifier')
            seen.add(sid)
            if not isinstance(task,str) or not TASK.fullmatch(task):
                raise ValueError('Invalid Gradle task: scenario.task must name a dedicated Gradle task, e.g. runClientGameTest')
            if scenario.get('kind') not in KINDS-{'manual'}:
                raise ValueError('Invalid executable scenario kind')
            if (not isinstance(refs,list) or not refs or len(refs)>64
                    or any(not isinstance(r,str) or r not in by_id or sid not in by_id[r]['scenario_ids'] for r in refs)
                    or len(set(refs))!=len(refs)):
                raise ValueError('Scenario must map back to its source requirements')
            tests=scenario.get('expected_tests')
            if not isinstance(tests,list) or not 1<=len(tests)<=64:
                raise ValueError('Executable assertions must name expected test cases')
            tests=[{'classname':_text(t.get('classname'),256,required=True),
                    'name':_text(t.get('name'),256,required=True)} for t in tests if isinstance(t,dict)]
            if len(tests)!=len(scenario['expected_tests']): raise ValueError('Invalid expected test')
            if (len({(t['classname'],t['name']) for t in tests})!=len(tests)
                    or any(any(ord(c)<32 for c in t['classname']+t['name']) for t in tests)):
                raise ValueError('Invalid or duplicate expected tests')
            if any(by_id[r]['kind']!=scenario['kind'] for r in refs):
                raise ValueError('Scenario kind does not match its requirements')
            checked.append({'id':sid,'kind':scenario['kind'],'task':task,'requirement_ids':refs,
                'description':_text(scenario.get('description',''),2000),'expected_tests':tests})
        scenario_map={s['id']:s for s in checked}
        for item in normalized:
            if any(s not in scenario_map or item['id'] not in scenario_map[s]['requirement_ids'] for s in item['scenario_ids']):
                raise ValueError('Missing scenario mapping')
        return {'requirements':normalized,'scenarios':checked,'notes':[], '_coverage_required':True}
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        empty['notes'].append('План отклонён: '+str(exc)[:200]); return empty


def prepare_request(catalog, plan, previous=None, source_sha256=''):
    passed=set(); attempts={}
    if previous and previous.get('spec_sha256')==catalog['spec_sha256']:
        for requirement in previous.get('requirements',[]):
            for scenario in requirement.get('scenarios',[]):
                if 'test_status' in scenario:
                    attempts[scenario['id']]=scenario.get('attempt_round',previous.get('round',0))
    if previous and previous.get('source_sha256')==source_sha256 and previous.get('spec_sha256')==catalog['spec_sha256']:
        passed={s['id'] for r in previous.get('requirements',[]) for s in r.get('scenarios',[])
                if s.get('status')=='TEST_PASSED'}
    ordered=sorted(plan['scenarios'],key=lambda s:(attempts.get(s['id'],-1),s['id'] in passed))
    return {'schema_version':1,'spec_sha256':catalog['spec_sha256'],
            'catalog_ids':[r['id'] for r in catalog['requirements']],
            'scenarios':ordered[:MAX_SCENARIOS_PER_ROUND]}


def source_fingerprint(workspace, inventory=None):
    root=Path(workspace)
    ignored={'.git','.gradle','.lab-cache','.agents','.codex','build','node_modules','__pycache__',
             'run','run-clientGameTest','run-serverGameTest','.pytest_cache','.venv','venv','.lab-feedback','.lab-memory','.lab-pipeline','.lab-evidence'}
    # Working notes and controller context are not executable source or evidence.
    generated={'acceptance-request.json','acceptance-catalog.json',
               'round-memory.json','document-index.json','agent-handoff.json','acceptance-tasks.json'}
    digest=hashlib.sha256(); count=total=0
    for directory, dirs, files in os.walk(root,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d not in ignored)
        if any((Path(directory)/d).is_symlink() for d in dirs):
            raise ValueError('Source tree contains a directory link')
        for name in sorted(files):
            if name in generated: continue
            path=Path(directory)/name; relative=path.relative_to(root).as_posix()
            with _open(root,PurePosixPath(relative).parts) as handle:
                info=os.fstat(handle.fileno()); total+=info.st_size; count+=1
                if total>200*1024*1024 or count>10000: raise ValueError('Source fingerprint exceeds budget')
                digest.update(relative.encode()); digest.update(b'\0')
                file_digest=hashlib.sha256() if inventory is not None else None
                for chunk in iter(lambda:handle.read(65536),b''):
                    digest.update(chunk)
                    if file_digest is not None:file_digest.update(chunk)
                digest.update(b'\0')
                if inventory is not None:inventory[relative]=file_digest.hexdigest()
    return digest.hexdigest()


def build_state(catalog, plan, results, source_sha256='', round_number=0):
    annotations={r['id']:r for r in plan['requirements']}
    valid={s['id']:s for s in plan['scenarios']}
    evidence={}
    notes=list(plan.get('notes',[]))
    if isinstance(results,dict) and results.get('spec_sha256')==catalog['spec_sha256']:
        for result in results.get('scenarios',[])[:MAX_ITEMS]:
            if not isinstance(result,dict) or result.get('id') not in valid:continue
            scenario=valid[result['id']]
            if result.get('requirement_ids')!=scenario['requirement_ids']:continue
            item=dict(result)
            item['kind']=scenario['kind']
            item.setdefault('attempt_round',round_number)
            if item.get('status') not in ('TEST_PASSED','FAIL','NOT_TESTED'):item['status']='NOT_TESTED'
            proof=item.get('assertion_evidence')
            if item['status']=='TEST_PASSED' and not (item.get('test_status')=='PASS'
                    and isinstance(proof,dict) and proof.get('reports')
                    and proof.get('expected_tests')==scenario['expected_tests']
                    and not proof.get('missing_tests')):
                item.update(status='NOT_TESTED',detail='Нет свидетельств выполнения именованных проверок.')
            from common.failure_details import enrich_result
            evidence[item['id']]=enrich_result(scenario,item)
    requirements=[]
    for original in catalog['requirements']:
        item={**original,**annotations.get(original['id'],{})}
        scenarios=[evidence.get(sid,{'id':sid,'status':'NOT_TESTED','detail':'Сценарий ещё не выполнялся.'})
                   for sid in item.get('scenario_ids',[])]
        status='NOT_TESTED'
        kinds_match=all(s.get('kind',valid.get(s['id'],{}).get('kind'))==item['kind'] for s in scenarios)
        if scenarios and any(s['status']=='FAIL' for s in scenarios):status='FAIL'
        elif scenarios and kinds_match and all(s['status']=='TEST_PASSED' for s in scenarios):status='TEST_PASSED'
        item.update(status=status,scenarios=scenarios,
            video_required=False,
            detail=('Тесты пройдены; соответствие критерия исходному ТЗ требует проверки.' if status=='TEST_PASSED'
                    else 'Есть ошибки проверки.' if status=='FAIL'
                    else 'Нет полного набора выполненных проверок для этого пункта.'))
        requirements.append(item)
    summary={'total':len(requirements),'test_passed':sum(r['status']=='TEST_PASSED' for r in requirements),
             'failed':sum(r['status']=='FAIL' for r in requirements),
             'not_tested':sum(r['status']=='NOT_TESTED' for r in requirements),
             'without_video':sum(r['video_required'] and not any(s.get('video') for s in r['scenarios']) for r in requirements)}
    if catalog.get('truncated'):notes.append('Каталог превышает 2000 пунктов; полный охват не подтверждён.')
    all_passed=bool(requirements) and summary['test_passed']==len(requirements) and not catalog.get('truncated')
    result={'schema_version':1,'spec_sha256':catalog['spec_sha256'],'source_sha256':source_sha256,
            'round':round_number,'status':'TESTS_PASSED_REVIEW_REQUIRED' if all_passed else
            'IN_PROGRESS' if summary['test_passed'] or summary['failed'] else 'NOT_TESTED',
            'summary':summary,'requirements':requirements,'plan_notes':notes,'coverage_note':COVERAGE_NOTE}
    from common.manual_review import decorate
    from common.coverage import gate
    return gate(decorate(result,plan),plan)


def mark_stale(state, detail):
    if 'history_summary' not in state:
        state['history_summary']={'test_passed':sum(r['status'] in ('TEST_PASSED','STALE') for r in state['requirements']),
                                  'latest_round':state.get('round',0)}
    for item in state['requirements']:
        if item['status']=='TEST_PASSED':
            item.update(status='STALE',detail=detail)
        for scenario in item.get('scenarios',[]):
            if scenario.get('status')=='TEST_PASSED':scenario['status']='STALE'
    state['status']='IN_PROGRESS' if state['requirements'] else 'NOT_TESTED'
    state['summary']['test_passed']=0
    state['summary']['stale']=sum(r['status']=='STALE' for r in state['requirements'])
    state['summary']['not_tested']=sum(r['status']=='NOT_TESTED' for r in state['requirements'])
    if detail not in state['plan_notes']:state['plan_notes'].append(detail)
    return state
