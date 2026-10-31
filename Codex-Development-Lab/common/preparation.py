"""Structural handoff checks; semantics stay with the planner and reviewer."""
from common.pipeline_contracts import validate_submission,ContractError
import json
from collections import Counter
from copy import deepcopy


def task_index(graph,catalog):
    return [{'id':task['id'],'title':task['title'],'depends_on':task['depends_on'],
             'condition_ids':task['condition_ids'],
             'open_questions':[{'question':q['question'],'kind':q['kind']} for q in catalog.get('question_resolutions',[])
                 if q['status']=='open' and set(q['condition_ids'])&set(task['condition_ids'])]}
            for task in graph['tasks']]


def route_recorded_questions(scope,graph,catalog,catalog_ref,graph_ref,review_refs,previous=None):
    """Route existing question classifications, without another LLM inventory.

    These are scheduling states, never implemented/verified feature verdicts.
    """
    rows=[]
    for task in graph['tasks']:
        questions=[q for q in catalog.get('question_resolutions',[]) if q['status']=='open'
                   and set(q['condition_ids'])&set(task['condition_ids'])]
        user=[q['question'] for q in questions if q['kind']=='user_clarification']
        technical=[q['question'] for q in questions if q['kind']=='experiment']
        status='needs_user' if user else 'experiment' if technical else 'ready'
        rows.append({'task_id':task['id'],'status':status,'reason':'\n'.join(user or technical) or 'No recorded unresolved dependency.',
            'experiment':'\n'.join(technical) if status=='experiment' else None,
            'return_when':'Obtain the required user clarification.' if user else
                'Research and implement using observed facts in the same package; verify independently.' if technical else
                'Select when useful; verification still required.'})
    old={task['task_id']:task for task in (previous or {}).get('tasks',[]) if task['status']=='external'}
    for row in rows:
        if row['task_id'] in old and row['status']!='needs_user':row.update(deepcopy(old[row['task_id']]))
    value={'contract':'PreparationHandoff','version':'v1','scope':dict(scope),'catalog_ref':catalog_ref,
        'graph_ref':graph_ref,'rationale':'Controller routing from recorded question states; no semantic approval or test PASS.',
        'working_decisions':[],'tasks':rows,'unresolved_review_refs':review_refs}
    blocked=blocked_tasks(value,graph,catalog)
    external=set(old)
    while True:
        expanded=external|{t['id'] for t in graph['tasks'] if any(dep in external for dep in t['depends_on'])}
        if expanded==external:break
        external=expanded
    for row in rows:
        if row['task_id'] in blocked and row['status'] in ('ready','experiment'):
            row.update(status='external' if row['task_id'] in external else 'needs_user',experiment=None,reason=blocked[row['task_id']])
    return validate_handoff(value,graph,catalog,catalog_ref,graph_ref,review_refs)


def blocked_tasks(handoff,graph,catalog):
    blocked={t['task_id']:t['reason'] for t in handoff['tasks'] if t['status'] in ('needs_user','external')}
    # An unanswered normative ambiguity cannot be answered by a handoff claim.
    pending={q['question']:q for q in catalog.get('question_resolutions',[])
             if q['status']=='open' and q['kind']=='user_clarification'}
    for task in graph['tasks']:
        for question in pending.values():
            if set(task['condition_ids'])&set(question['condition_ids']):
                blocked[task['id']]='User clarification pending: '+question['question']
    while True:
        added={t['id']:'Blocked prerequisite: '+', '.join(d for d in t['depends_on'] if d in blocked)
               for t in graph['tasks'] if t['id'] not in blocked and any(d in blocked for d in t['depends_on'])}
        if not added:return blocked
        blocked.update(added)


def validate_handoff(value,graph,catalog,catalog_ref,graph_ref,review_refs):
    validate_submission('PreparationHandoff',value)
    issues=[]
    def issue(path,code,expected,example,received='object'):
        issues.append({'path':path,'code':code,'expected':expected,'received_type':received,
            'example_json':json.dumps(example),'instruction':'Correct only this field or task; preserve all unrelated decisions.'})
    for key,reference in (('catalog_ref',catalog_ref),('graph_ref',graph_ref)):
        if value[key]!=reference:issue('$.'+key,'current_reference','Use the exact current '+key,reference)
    ids=[t['task_id'] for t in value['tasks']]
    expected={t['id'] for t in graph['tasks']}
    unknown=sorted(set(ids)-expected);missing=sorted(expected-set(ids));duplicates=sorted(k for k,n in Counter(ids).items() if n>1)
    if unknown or missing or duplicates:
        details={'unknown_ids':unknown,'missing_ids':missing,'duplicate_ids':duplicates,'allowed_ids':sorted(expected)}
        paths=[f'$.tasks[{i}].task_id' for i,key in enumerate(ids) if key in unknown or key in duplicates]
        if len(ids)!=len(expected) or not paths:paths=['$.tasks']
        issues.extend([{'path':p,'code':'task_membership',
            'expected':'Classify every graph task exactly once: '+json.dumps(details),
            'received_type':'array' if p=='$.tasks' else 'string','example_json':'[]' if p=='$.tasks' else '"existing-task-id"',
            'instruction':'Correct only the listed task IDs; use actual graph IDs. Never invent a mapping.'} for p in paths])
    if value['unresolved_review_refs']!=review_refs:
        issue('$.unresolved_review_refs','current_reference','Retain every supplied unresolved review reference',review_refs,'array')
    for index,task in enumerate(value['tasks']):
        if task['status']=='experiment' and not task['experiment']:
            issues.extend([{'path':f'$.tasks[{index}].experiment','code':'experiment_required',
                'expected':'An experiment task needs a concrete observation to perform','received_type':'null','example_json':'"Observe the actual behavior"','instruction':'Describe the observation for this task only.'}])
        if task['status']!='experiment' and task['experiment'] is not None:
            issues.extend([{'path':f'$.tasks[{index}].experiment','code':'experiment_forbidden',
                'expected':'Only experiment tasks may carry an experiment; otherwise use null','received_type':'string','example_json':'null','instruction':'Set this field to JSON null; preserve other fields.'}])
    blocked=blocked_tasks(value,graph,catalog)
    for index,task in enumerate(value['tasks']):
        if task['task_id'] in blocked and task['status'] in ('ready','experiment'):
            issue(f'$.tasks[{index}]','blocked_dependency','Task depends on an unresolved user/external decision: '+task['task_id']+
                '; choose needs_user or external with a reason and return condition',{'status':'needs_user','reason':blocked[task['task_id']]})
    if issues:raise ContractError('PreparationHandoff',issues)
    return value


def update_external(handoff,updates,graph,catalog):
    if not updates:return handoff
    previous=deepcopy(handoff)
    known={t['id'] for t in graph['tasks']};seen=set()
    for update in updates:
        key=update['task_id']
        if key not in known or key in seen:raise ValueError('External update needs one existing task ID: '+key)
        seen.add(key)
        row=next((t for t in previous['tasks'] if t['task_id']==key),None)
        if row is None:raise ValueError('External update task missing from handoff: '+key)
        if update['state']=='resolved':
            if row['status']!='external':continue  # Replay-safe; never resolves a normative user question.
            row.update(status='ready',experiment=None,reason=update['reason'],return_when=update['return_when'])
        else:row.update(status='external',experiment=None,reason=update['reason'],return_when=update['return_when'])
    return route_recorded_questions(previous['scope'],graph,catalog,previous['catalog_ref'],previous['graph_ref'],
        previous['unresolved_review_refs'],previous)


def validate_package(decision,handoff,graph,catalog):
    """Block genuine unavailable dependencies, not ordinary technical research.

    experiment is retained as diagnostic context. It is neither an execution
    permission gate nor evidence that a task/condition has passed verification.
    """
    blocked=blocked_tasks(handoff,graph,catalog)
    if len(blocked)==len(graph['tasks']) and decision['strategy']!='wait':
        raise ValueError('No independent task is available; wait and explain the actual user/external dependencies')
    for action in decision['actions']:
        if set(action['task_ids'])&set(blocked):
            raise ValueError('Do not schedule blocked tasks; select independent work or wait with the actual dependency')


def retired_technical_gate(issue):
    return (issue.get('code')=='open_experiment' or issue.get('code')=='semantic_links' and
        issue.get('expected','').startswith(('Unresolved technical experiment requires diagnose before implementation',
                                           'Open technical questions require an experiment for task ')))
