"""Bounded detours from repeated verified failures; never changes proof or progress."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile

from common import acceptance, acceptance_progress, manual_review
from common.history import _open
from common.verification import infrastructure_failure

NAME = 'work-routing.json'
LIMIT = 128 * 1024
WINDOW = 128
REPAIRS = frozenset(('repair_scenario', 'diagnose_failure', 'repair_verification_failure'))
POLICY_REVISION = 'reflection-routing-v2-early-failure'
REFLECTION_KINDS = frozenset(('reflect_failure','repair_after_reflection'))


def _read(root, name, limit=1024*1024):
    try:
        with _open(root, tuple(name.split('/'))) as handle:
            if os.fstat(handle.fileno()).st_size > limit:return {}
            value = json.loads(handle.read(limit+1))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,TypeError,UnicodeError,RecursionError):return {}


def _save(root, state):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('Routing root must be a real directory')
    raw=json.dumps(state,ensure_ascii=False).encode()
    if len(raw)>LIMIT:raise ValueError('Routing state exceeds size limit')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=root,prefix='.work-routing-',delete=False) as handle:
            temporary=handle.name;handle.write(raw);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,root/NAME)
    finally:
        if temporary and os.path.exists(temporary):os.unlink(temporary)


def _action(root, number):
    prefix=f'round-{number:02d}/'
    planned=_read(root,prefix+'planner/plan.json').get('action')
    if isinstance(planned,dict):return planned
    value=_read(root,prefix+'round-memory-start.json')
    if value.get('round_action_round',number)!=number:return {}
    action=value.get('round_action')
    return action if isinstance(action,dict) else {}


def _paid(root, number):
    prefix=f'round-{number:02d}/'
    complete=_read(root,prefix+'verification_complete.json')
    development=_read(root,prefix+'development_complete.json')
    if not complete or development.get('kind')=='verification_only' or complete.get('development_kind')=='verification_only':
        return None
    # Historical model checkpoints use {round: N}, newer ones have kind=model.
    if not development:return None
    return complete


def _blocked(plan, report):
    if plan.get('notes') or not isinstance(report,dict) or infrastructure_failure(report):return True
    return any(c.get('label') in ('acceptance_plan','gradle_build','production_jar')
               and c.get('status') in ('FAIL','ERROR','TIMEOUT') for c in report.get('checks',[]))


def _failed(result, scenario):
    if not isinstance(result,dict) or result.get('status')!='FAIL' or result.get('test_status')!='FAIL':return False
    evidence=result.get('assertion_evidence')
    if not isinstance(evidence,dict):return False
    if not evidence.get('reports') or evidence.get('expected_tests')!=scenario.get('expected_tests'):return False
    failures=evidence.get('failures')
    if isinstance(failures,list) and failures:
        expected={(t['classname'],t['name']) for t in scenario['expected_tests']}
        return any(isinstance(f,dict) and (f.get('classname'),f.get('name')) in expected for f in failures)
    # An early named assertion failure can prevent later checks from running.
    # Missing siblings cannot erase that observed failure. Conversely missing
    # tests alone do not prove that any expected assertion failed. Legacy
    # complete reports without per-case failures retain their old routing.
    return not evidence.get('missing_tests')


def _current_failure(ledger, scenario):
    record=ledger.get('scenarios',{}).get(scenario['id'],{}).get('latest',{})
    return (record.get('definition_sha256')==acceptance_progress.definition_digest(scenario)
            and _failed(record.get('result'),scenario))


def _round_failure(root, number, report, ledger, scenario):
    entry=ledger.get('scenarios',{}).get(scenario['id'],{})
    digest=acceptance_progress.definition_digest(scenario)
    records=list(entry.get('history',[]))+[entry.get('latest',{})]
    if not any(r.get('round')==number and r.get('definition_sha256')==digest
               and (r.get('status')=='FAIL' or (r.get('result') or {}).get('status')=='FAIL') for r in records):return False
    raw=_read(root,f'round-{number:02d}/acceptance_results.json') or report.get('acceptance_results',{})
    return isinstance(raw,dict) and any(r.get('id')==scenario['id'] and _failed(r,scenario)
        for r in raw.get('scenarios',[]) if isinstance(r,dict))


def _normal(reason):
    return {'kind':'normal','required_target':None,'allowed_requirement_ids':[],
            'deferred_scenario':None,'return_due_round':None,'reason':reason}


def _historical_failure(root, number, report, scenario):
    """A real past assertion failure can inform routing after a test gains assertions.

    Current eligibility still requires _current_failure against the current exact
    definition. This helper changes scheduling only, never acceptance proof.
    """
    raw=_read(root,f'round-{number:02d}/acceptance_results.json') or report.get('acceptance_results',{})
    if not isinstance(raw,dict):return False
    for result in raw.get('scenarios',[]):
        if not isinstance(result,dict) or result.get('id')!=scenario['id']:continue
        evidence=result.get('assertion_evidence')
        expected=evidence.get('expected_tests') if isinstance(evidence,dict) else None
        if (not isinstance(expected,list) or not expected or any(not isinstance(t,dict)
                or not isinstance(t.get('classname'),str) or not isinstance(t.get('name'),str) for t in expected)):
            continue
        if _failed(result,{'expected_tests':expected}):return True
    return False


def _candidate(catalog,plan,focus,failed,used):
    mapped={rid for s in plan.get('scenarios',[]) for rid in s['requirement_ids']}
    mapped.update(r['id'] for r in plan.get('requirements',[]) if r.get('scenario_ids'))
    manual=set(manual_review.pending_ids(plan))
    uncovered={rid for rid,value in plan.get('_coverage',{}).items()
        if any(not condition.get('scenario_ids') or condition.get('review_status')=='missing'
               for condition in value.get('obligations',[]))}
    return next((r['id'] for r in catalog['requirements'] if r['id'] in focus
        and r['id'] not in manual|failed|used and (r['id'] not in mapped or r['id'] in uncovered)),None)


def _reflection(kind,scenario,round_number):
    return {'kind':kind,'required_target':scenario['id'],
        'allowed_requirement_ids':scenario['requirement_ids'][:64],
        'deferred_scenario':scenario['id'],'return_due_round':round_number,
        'reason':('Use the completed diagnosis in a concrete repair before requesting more tracing.'
                  if kind=='repair_after_reflection' else
                  'Repeated work has not produced a new independent success: inspect existing evidence, '
                  'reject failed hypotheses and run one bounded diagnostic experiment; keep the failure open.')}


def read_directive(job_root,catalog,round_number):
    """Read the frozen decision without creating state or consuming obligations."""
    if type(round_number) is not int or not 1<=round_number<=2**53-1:raise ValueError('Invalid routing round')
    value=_read(job_root,NAME,LIMIT)
    if (value.get('schema_version')!=1 or value.get('spec_sha256')!=catalog.get('spec_sha256')
            or value.get('policy_revision')!=POLICY_REVISION):
        return _normal('No persisted routing directive for this specification and round.')
    decisions=value.get('directives')
    decision=decisions.get(str(round_number)) if isinstance(decisions,dict) else None
    if not isinstance(decision,dict) or decision.get('kind') not in {'normal','rotate_requirement','return_to_failure'}|REFLECTION_KINDS:
        return _normal('No persisted routing directive for this specification and round.')
    result=_normal(str(decision.get('reason',''))[:1000]);result['kind']=decision['kind']
    for field in ('required_target','deferred_scenario'):
        item=decision.get(field)
        result[field]=item[:128] if isinstance(item,str) else None
    ids=decision.get('allowed_requirement_ids')
    result['allowed_requirement_ids']=[rid[:128] for rid in ids[:64] if isinstance(rid,str)] if isinstance(ids,list) else []
    due=decision.get('return_due_round')
    result['return_due_round']=due if type(due) is int and 1<=due<=2**53-1 else None
    return result


def choose_directive(job_root,catalog,plan,assignment,ledger,round_number,previous):
    """Persist one directive per round; only actual paid executions consume a detour."""
    if type(round_number) is not int or not 1<=round_number<=2**53-1:raise ValueError('Invalid routing round')
    root=Path(job_root);digest=catalog['spec_sha256']
    state=_read(root,NAME,LIMIT)
    if state.get('schema_version')!=1 or state.get('spec_sha256')!=digest:
        state={'schema_version':1,'spec_sha256':digest,'directives':{},'used_requirement_ids':[],'obligation':None}
    if state.get('policy_revision')!=POLICY_REVISION:
        # Refresh prepared future decisions after deployment without forgetting
        # existing detours or losing the obligation to return to their failure.
        state.update(policy_revision=POLICY_REVISION,directives={})
    directives=state.get('directives')
    if not isinstance(directives,dict):directives={}
    key=str(round_number)
    if isinstance(directives.get(key),dict):return copy.deepcopy(directives[key])
    known={r['id'] for r in catalog['requirements']}
    used=state.get('used_requirement_ids',[])
    used={rid for rid in used if isinstance(rid,str) and rid in known} if isinstance(used,list) else set()
    scenarios={s['id']:s for s in plan.get('scenarios',[])}
    paid=[]
    for number in range(round_number-1,max(0,round_number-WINDOW-1),-1):
        report=_paid(root,number)
        if report is not None:paid.append((number,report,_action(root,number)))
    directive=_normal('No repeated independently confirmed failure requires a detour.')
    obligation=state.get('obligation')
    if not isinstance(obligation,dict):obligation=None
    if obligation:
        scenario=scenarios.get(obligation.get('scenario'))
        if (not scenario or obligation.get('definition_sha256')!=acceptance_progress.definition_digest(scenario)):
            # Changed definitions must be checked afresh; they cannot inherit a detour.
            obligation=None
        else:
            record=ledger.get('scenarios',{}).get(scenario['id'],{}).get('latest',{})
            passed=(record.get('definition_sha256')==obligation['definition_sha256']
                    and (record.get('result') or {}).get('status')=='TEST_PASSED')
            returned=any(number>obligation['rotation_round'] and action.get('target')==scenario['id']
                         and not _blocked(plan,report) and _round_failure(root,number,report,ledger,scenario)
                         for number,report,action in paid)
            if passed or returned:obligation=None
    blocked=_blocked(plan,previous or {})
    guard=(previous or {}).get('progress_guard',{})
    reflecting=isinstance(guard,dict) and type(guard.get('streak')) is int and guard['streak']>=2
    focus=set((assignment.get('current_task') or {}).get('requirement_ids',[]))
    diagnosed=None
    if reflecting and not blocked and paid:
        number,report,action=paid[0];scenario=scenarios.get(action.get('target'))
        if (scenario and action.get('kind')=='diagnose_failure' and not _blocked(plan,report)
                and focus.intersection(scenario['requirement_ids']) and _current_failure(ledger,scenario)
                and _historical_failure(root,number,report,scenario)):
            diagnosed=scenario
    if obligation:
        if blocked:
            directive=_normal('Repair plan/build/infrastructure before returning to the deferred scenario.')
        else:
            directive={'kind':'return_to_failure','required_target':obligation['scenario'],
                'allowed_requirement_ids':list(obligation['requirement_ids']),
                'deferred_scenario':obligation['scenario'],'return_due_round':round_number,
                'reason':'The one-round detour ended; return to the original unresolved failure.'}
    elif not blocked and len(paid)>=2 and diagnosed is None:
        recent=paid[:2]
        sid=recent[0][2].get('target');scenario=scenarios.get(sid)
        focus=set((assignment.get('current_task') or {}).get('requirement_ids',[]))
        if (scenario and focus.intersection(scenario['requirement_ids']) and _current_failure(ledger,scenario)
                and all(action.get('kind') in REPAIRS and action.get('target')==sid
                        and not _blocked(plan,report) and _round_failure(root,number,report,ledger,scenario)
                        for number,report,action in recent)):
            candidate=_candidate(catalog,plan,focus,set(scenario['requirement_ids']),used)
            if candidate and len(used)<WINDOW:
                used.add(candidate)
                obligation={'scenario':sid,'definition_sha256':acceptance_progress.definition_digest(scenario),
                    'requirement_ids':scenario['requirement_ids'][:64],'rotation_round':round_number}
                directive={'kind':'rotate_requirement','required_target':candidate,'allowed_requirement_ids':[candidate],
                    'deferred_scenario':sid,'return_due_round':round_number+1,
                    'reason':'Two paid repairs repeated the same verified failure; implement one untouched requirement, then return.'}
    if directive['kind']=='normal' and reflecting and not blocked:
        if diagnosed is not None:
            directive=_reflection('repair_after_reflection',diagnosed,round_number)
        else:
            # Prefer the latest attempted failure; a partial implementation may
            # target its REQ rather than its scenario. Only current genuine
            # assertion failures are eligible, never an arbitrary agent claim.
            latest=paid[0][2] if paid else {}
            eligible=[s for s in scenarios.values() if focus.intersection(s['requirement_ids'])
                      and _current_failure(ledger,s)]
            scenario=next((s for s in eligible if s['id']==latest.get('target')),eligible[0] if eligible else None)
            if scenario is not None:
                repaired_after_diagnosis=(len(paid)>=2 and latest.get('kind')=='repair_scenario'
                    and latest.get('target')==scenario['id'] and paid[1][2].get('kind')=='diagnose_failure'
                    and paid[1][2].get('target')==scenario['id']
                    and all(not _blocked(plan,report) and _historical_failure(root,number,report,scenario)
                            for number,report,action in paid[:2]))
                candidate=(_candidate(catalog,plan,focus,set(scenario['requirement_ids']),used)
                           if repaired_after_diagnosis and len(used)<WINDOW else None)
                if candidate:
                    used.add(candidate)
                    obligation={'scenario':scenario['id'],'definition_sha256':acceptance_progress.definition_digest(scenario),
                        'requirement_ids':scenario['requirement_ids'][:64],'rotation_round':round_number}
                    directive={'kind':'rotate_requirement','required_target':candidate,'allowed_requirement_ids':[candidate],
                        'deferred_scenario':scenario['id'],'return_due_round':round_number+1,
                        'reason':'The diagnosed failure survived a concrete repair; implement one independent requirement, then return.'}
                else:
                    directive=_reflection('reflect_failure',scenario,round_number)
    directives[key]=directive
    numeric=sorted((int(k),v) for k,v in directives.items() if isinstance(k,str) and k.isdecimal() and len(k)<=16)
    state.update(directives={str(k):v for k,v in numeric[-WINDOW:]},
        used_requirement_ids=sorted(used),obligation=obligation)
    # Long requirement IDs make a fixed record count insufficient. Evict only
    # old replay entries, never used-target memory or the outstanding obligation.
    while len(json.dumps(state,ensure_ascii=False).encode())>LIMIT and len(state['directives'])>1:
        del state['directives'][next(iter(state['directives']))]
    _save(root,state)
    return copy.deepcopy(directive)


def reserve_regression(request,plan,directive):
    """Reserve the deferred scenario without dropping a newly mapped target check."""
    result=copy.deepcopy(request)
    if directive.get('kind') not in {'rotate_requirement','return_to_failure'}|REFLECTION_KINDS:return result
    scenarios={s['id']:s for s in plan.get('scenarios',[])}
    deferred=scenarios.get(directive.get('deferred_scenario'))
    if deferred is None:return result
    selected=[];seen=set()
    def add(scenario):
        if scenario['id'] not in seen and len(selected)<acceptance.MAX_SCENARIOS_PER_ROUND:
            selected.append(copy.deepcopy(scenario));seen.add(scenario['id'])
    target=directive.get('required_target')
    alternate=next((s for s in scenarios.values() if target in s['requirement_ids'] and s['id']!=deferred['id']),None)
    if directive['kind']=='return_to_failure' or directive['kind'] in REFLECTION_KINDS:add(deferred)
    if alternate:add(alternate)
    add(deferred)
    for scenario in request.get('scenarios',[]):
        if isinstance(scenario,dict) and scenario.get('id') in scenarios:add(scenarios[scenario['id']])
    result['scenarios']=selected
    return result
