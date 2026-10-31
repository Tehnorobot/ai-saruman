"""Bounded planner proposals. Plans schedule work; they never establish PASS."""
from pathlib import Path
import json
from common import acceptance, manual_review, coverage,round_progress
from common.history import _open

KINDS={'repair_scenario','implement_and_map_requirement','implement_or_verify_scenario','review_coverage',
       'repair_verification_failure','repair_acceptance_plan','diagnose_failure','await_user_review'}
IMPLEMENTATION_KINDS={'repair_scenario','implement_and_map_requirement','implement_or_verify_scenario'}
MAX_ROUND_ACTIONS=4

def enforce_directive(action,context):
    directive=context.get('scheduling_directive') or {}
    kind=directive.get('kind');target=directive.get('required_target')
    if kind=='rotate_requirement':
        if action.get('kind')!='implement_and_map_requirement' or action.get('target')!=target:
            raise ValueError('Controller requires one independent requirement before returning to the repeated failure: '+str(target))
    elif kind=='return_to_failure':
        if (action.get('target')!=target or action.get('kind') not in
                ('repair_scenario','diagnose_failure','repair_verification_failure')):
            raise ValueError('Controller requires return to the deferred failure: '+str(target))
    elif kind in ('reflect_failure','repair_after_reflection'):
        required='diagnose_failure' if kind=='reflect_failure' else 'repair_scenario'
        if action.get('target')!=target or action.get('kind')!=required:
            raise ValueError('Controller requires '+required+' for the observed failure: '+str(target))
    elif kind=='implement_functional_goal':
        if (action.get('kind')!='implement_and_map_requirement' or action.get('target')!=target
                or not set(directive.get('required_condition_ids',[])).issubset(action.get('condition_ids',[]))
                or action.get('verification_kind')!='gameplay'
                or len(action.get('verification_objective','').strip())<24):
            raise ValueError('Controller requires a minimal working goal slice before more bootstrap/reviews: implement the exact REQ and condition_ids, with a concrete verification_objective')
    return directive

def text(value,limit=1000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError('Missing or oversized planner text')
    return value.strip()


def target_options(catalog,plan,context):
    allowed=set((context.get('current_task') or {}).get('requirement_ids',[]))
    allowed.update(r for f in context.get('unresolved_scenario_failures',[]) for r in f.get('requirement_ids',[]))
    if not allowed:allowed={r['id'] for r in catalog['requirements']}
    facts={s['id']:s.get('status','NOT_TESTED') for s in context.get('scenario_facts',{}).get('items',[])}
    return {'requirement_targets':sorted(allowed),
            'scenario_targets':{s['id']:facts.get(s['id'],'NOT_TESTED')
                for s in plan.get('scenarios',[]) if allowed.intersection(s['requirement_ids'])},
            'coverage_review_pending':[r['requirement_id'] for r in context.get('coverage_status',[])
                if r.get('requirement_id') in allowed and not r.get('complete')]}

def validate(value,catalog,plan,context,number,mode):
    if not isinstance(value,dict):raise ValueError('Planner must return a JSON object')
    known={r['id'] for r in catalog['requirements']}
    current=context.get('current_task') or {}
    allowed=set(current.get('requirement_ids',[]))
    allowed.update(rid for f in context.get('unresolved_scenario_failures',[]) for rid in f.get('requirement_ids',[]))
    if not allowed:allowed=known
    raw=value.get('action')
    if not isinstance(raw,dict) or raw.get('kind') not in KINDS:raise ValueError('Invalid planner action')
    target=text(raw.get('target'),128)
    refs=raw.get('requirement_ids',[])
    if (not isinstance(refs,list) or len(refs)>12 or any(not isinstance(r,str) or r not in allowed for r in refs)):
        raise ValueError('Planner action must stay inside current task and observed regressions')
    kind=raw['kind']
    hard_failures=[c for c in context.get('previous_independent_verification',{}).get('checks',[])
        if c.get('label') in ('gradle_build','production_jar','verification_report')
        and c.get('status') in ('FAIL','ERROR','TIMEOUT')]
    if hard_failures and kind not in ('repair_verification_failure','diagnose_failure','repair_acceptance_plan'):
        raise ValueError('Build or verification infrastructure failure must be repaired before new features')
    scenarios={s['id']:s for s in plan.get('scenarios',[])}
    if kind in ('repair_scenario','implement_or_verify_scenario'):
        if target not in scenarios or not allowed.intersection(scenarios[target]['requirement_ids']):
            raise ValueError('Planner selected unknown or unrelated scenario')
        refs=scenarios[target]['requirement_ids'][:12]
    if kind=='implement_and_map_requirement' and target not in allowed:
        if target in scenarios:
            raise ValueError('action.target '+target+' is a scenario ID; implement_and_map_requirement requires a REQ ID from '+', '.join(sorted(allowed)[:8]))
        raise ValueError('Planner selected '+('out-of-scope' if target in known else 'unknown')+' requirement: '+target)
    if kind=='implement_and_map_requirement':refs=[target]
    if kind=='review_coverage':
        if target not in allowed or not value.get('coverage_reviews'):raise ValueError('Coverage review needs a known current requirement and independent review proposals')
        refs=[target]
    current_passed={s['id'] for s in context.get('scenario_facts',{}).get('items',[])
                    if s.get('status')=='TEST_PASSED'}
    failed_targets={s.get('id') for s in context.get('unresolved_scenario_failures',[])}
    if (kind in ('implement_or_verify_scenario','repair_scenario') and target in current_passed
            and target not in failed_targets):
        raise ValueError('Uncovered source conditions or pending coverage review must be addressed instead of repeating an already-passed scenario: '+target)
    action={'kind':kind,'target':target,'requirement_ids':refs,
            'reason':text(raw.get('reason'),1200),'diagnostic':str(raw.get('diagnostic',''))[:1600]}
    evidence=raw.get('evidence_refs',[])
    condition_ids=raw.get('condition_ids',[])
    known_conditions={c['id'] for rid in refs for c in coverage.obligations(catalog).get(rid,[])}
    if (not isinstance(condition_ids,list) or len(condition_ids)>24
            or any(not isinstance(c,str) or c not in known_conditions for c in condition_ids)
            or len(set(condition_ids))!=len(condition_ids)):
        raise ValueError('Action condition_ids must refer to exact source conditions of its requirements')
    if condition_ids:action['condition_ids']=condition_ids
    if raw.get('verification_objective') is not None:
        action['verification_objective']=text(raw['verification_objective'],1200)
    if raw.get('verification_kind') is not None:
        if raw['verification_kind'] not in ('gameplay','unit'):raise ValueError('Invalid verification_kind')
        action['verification_kind']=raw['verification_kind']
    if raw.get('expected_progress') is not None:
        expected=raw['expected_progress']
        if not isinstance(expected,dict) or set(expected)!={'kind','observable_result'}:
            raise ValueError('expected_progress needs only kind and observable_result')
        action['expected_progress']={'kind':text(expected.get('kind'),32),
                                     'observable_result':text(expected.get('observable_result'),1200)}
    if not isinstance(evidence,list) or len(evidence)>6 or any(not isinstance(e,dict) for e in evidence):
        raise ValueError('Invalid planner evidence references')
    if evidence:action['evidence_refs']=evidence
    if (kind in ('repair_scenario','diagnose_failure','repair_verification_failure')
            and context.get('verification_feedback',{}).get('file_count',0)>0 and not evidence):
        raise ValueError('Read the available diagnostic evidence and cite its actual lines before choosing a repair')
    if context.get('acceptance_plan_notes') and kind!='repair_acceptance_plan':
        raise ValueError('Invalid acceptance plan must be repaired first')
    directive=enforce_directive(action,context)
    round_progress.validate(action,context)
    proposals=value.get('manual_reviews',[])
    if not isinstance(proposals,list) or len(proposals)>12:raise ValueError('Too many manual proposals')
    mapped={r['id']:r for r in plan.get('requirements',[])}
    manual=[];seen=set()
    for item in proposals:
        if not isinstance(item,dict):raise ValueError('Invalid manual proposal')
        rid=item.get('id');req=mapped.get(rid,{})
        if directive.get('kind')=='rotate_requirement' and rid==directive.get('required_target'):
            raise ValueError('Required independent implementation cannot be replaced by a manual deferral')
        if rid not in allowed or rid in seen or req.get('scenario_ids'):
            raise ValueError('Manual proposal cannot hide an executable requirement')
        if any(rid in s['requirement_ids'] for s in plan.get('scenarios',[])):
            raise ValueError('Manual proposal conflicts with executable scenarios')
        seen.add(rid);manual.append({'id':rid,'manual_review':manual_review.validate(item.get('manual_review'))})
    if (context.get('round_progress_policy',{}).get('enabled') and kind=='await_user_review' and not manual):
        raise ValueError('Manual-only action requires genuine validated user review instructions')
    def strings(key,maximum):
        values=value.get(key,[])
        if not isinstance(values,list) or len(values)>maximum:raise ValueError('Invalid planner '+key)
        return [text(v,600) for v in values]
    hypotheses=strings('hypotheses',6)
    if mode=='deep_diagnosis' and not hypotheses:raise ValueError('Deep diagnosis requires concrete hypotheses')
    if directive.get('kind')=='reflect_failure' and (not hypotheses or len(action['diagnostic'].strip())<48):
        raise ValueError('Reflection needs concrete hypotheses, observed facts and a bounded diagnostic experiment')
    reviews=value.get('coverage_reviews',[])
    if not isinstance(reviews,list) or len(reviews)>12:raise ValueError('Invalid coverage reviews')
    if any(not isinstance(r,dict) or r.get('requirement_id') not in allowed for r in reviews):
        raise ValueError('Coverage review must stay inside the current task and observed regressions')
    verification=raw.get('verification_scenario_ids',[])
    if (not isinstance(verification,list) or len(verification)>acceptance.MAX_SCENARIOS_PER_ROUND
            or any(not isinstance(s,str) or s not in scenarios for s in verification)
            or len(set(verification))!=len(verification)
            or any(not set(refs).intersection(scenarios[s]['requirement_ids']) for s in verification)):
        raise ValueError('Verification scenarios must be known and mapped to this action requirements')
    if verification:action['verification_scenario_ids']=verification
    additions=value.get('additional_actions',[])
    if not isinstance(additions,list) or len(additions)>=MAX_ROUND_ACTIONS:
        raise ValueError('A round may contain one to four related actions')
    limit=value.get('verification_limit',acceptance.MAX_SCENARIOS_PER_ROUND)
    if type(limit) is not int or not 1<=limit<=acceptance.MAX_SCENARIOS_PER_ROUND:
        raise ValueError('verification_limit must be an integer from 1 to 4')
    if limit<1+len(additions):
        raise ValueError('verification_limit must reserve at least one scenario slot per package action')
    bundle_reason=''
    if additions:
        if kind not in IMPLEMENTATION_KINDS or directive.get('kind') in ('rotate_requirement','return_to_failure','implement_functional_goal','reflect_failure','repair_after_reflection'):
            raise ValueError('Only ordinary implementation/repair may bundle actions; mandatory directives stay single')
        if proposals or reviews or mode=='deep_diagnosis':
            raise ValueError('Manual/coverage reviews and deep diagnosis must remain separate from an implementation bundle')
        bundle_reason=text(value.get('bundle_reason'),1200)
    normalized=[];seen={target};all_refs=set(refs)
    for extra in additions:
        if not isinstance(extra,dict) or extra.get('kind') not in IMPLEMENTATION_KINDS:
            raise ValueError('Additional actions must be related implementation or scenario repair')
        if extra.get('kind')=='repair_scenario' and kind!='repair_scenario':
            raise ValueError('A package containing a scenario repair must put that repair first')
        dependency=extra.get('depends_on',[])
        if (not isinstance(dependency,list) or len(dependency)>3
                or any(not isinstance(d,str) or d not in seen for d in dependency)
                or len(set(dependency))!=len(dependency)):
            raise ValueError('Action dependencies must refer only to earlier actions in this package')
        # Apply the same scope, hard-failure, citation and passed-scenario rules
        # to every member; supplementary actions never bypass primary guards.
        checked=validate({'summary':value.get('summary'),'action':extra,
                          'hypotheses':value.get('hypotheses',[])},catalog,plan,context,number,mode)['action']
        if checked['target'] in seen:raise ValueError('Duplicate action target in round package')
        if not set(checked['requirement_ids']).intersection(allowed):
            raise ValueError('Additional action is outside the current task and observed regressions')
        if dependency:checked['depends_on']=dependency
        seen.add(checked['target']);all_refs.update(checked['requirement_ids']);normalized.append(checked)
    if len(all_refs)>12:raise ValueError('Round package exceeds twelve referenced requirements')
    selected_scenarios=set()
    for member in [action]+normalized:
        selected_scenarios.update(member.get('verification_scenario_ids',[]))
        if (member['kind'] in ('repair_scenario','implement_or_verify_scenario')
                or member['kind']=='diagnose_failure' and member['target'] in scenarios):
            selected_scenarios.add(member['target'])
    if len(selected_scenarios)>limit:
        raise ValueError('Explicit package verification scenarios exceed verification_limit; choose a smaller package or increase its limit')
    return {'schema_version':1,'spec_sha256':catalog['spec_sha256'],'round':number,'mode':mode,
        'status':'PLANNED','summary':text(value.get('summary'),1600),'action':action,
        'hypotheses':hypotheses,'next_steps':strings('next_steps',8),'manual_reviews':manual,
        'scheduling_directive':directive,'coverage_reviews':reviews,
        'additional_actions':normalized,'bundle_reason':bundle_reason,
        'verification_limit':limit,'action_count':1+len(normalized)}


def round_actions(decision):
    """Validated execution order; old single-action checkpoints stay compatible."""
    return [decision['action']]+decision.get('additional_actions',[])


def verification_package(job_root,number,catalog):
    """Only the controller's same-round validated checkpoint may prioritize tests."""
    try:
        decision=acceptance.read_json(Path(job_root)/f'round-{number:02d}'/'planner','plan.json',128*1024)
        actions=round_actions(decision)
        limit=decision.get('verification_limit',acceptance.MAX_SCENARIOS_PER_ROUND)
        if (decision.get('round')!=number or decision.get('spec_sha256')!=catalog['spec_sha256']
                or decision.get('status')!='PLANNED' or not 1<=len(actions)<=MAX_ROUND_ACTIONS
                or type(limit) is not int or not 1<=limit<=acceptance.MAX_SCENARIOS_PER_ROUND):return [],acceptance.MAX_SCENARIOS_PER_ROUND
        return actions,limit
    except (OSError,ValueError,KeyError,TypeError):return [],acceptance.MAX_SCENARIOS_PER_ROUND

def final_message(logfile):
    value=None
    with _open(Path(logfile).parent,(Path(logfile).name,)) as source:
        # A long successful tool session is not an oversized final plan. Read
        # its bounded tail rather than rejecting the entire historical log.
        tail_bytes=8*1024*1024
        size=source.seek(0,2)
        source.seek(max(0,size-tail_bytes))
        if size>tail_bytes:source.readline(tail_bytes+1)  # skip partial first event
        while True:
            line=source.readline(tail_bytes+1)
            if not line:break
            if len(line)>tail_bytes:raise ValueError('Planner event exceeds safe size limit')
            try:event=json.loads(line)
            except (ValueError,UnicodeError):continue
            item=event.get('item',{}) if isinstance(event,dict) else {}
            if (isinstance(item,dict) and item.get('type')=='agent_message'
                    and item.get('agent_id',event.get('agent_id','main')) in ('main','planner')):
                value=item.get('text')
    if not isinstance(value,str) or len(value)>1024*1024:raise ValueError('Planner returned no bounded final JSON in log tail')
    value=value.strip()
    if value.startswith('```') and value.endswith('```'):value=value.split('\n',1)[1].rsplit('```',1)[0]
    return json.loads(value)

def public_state(job_root):
    try:
        value=acceptance.read_json(job_root,'planner-state.json',128*1024)
        return {'state':'available','source':'planner-state.json','data':{
            k:value[k] for k in ('round','mode','status','summary','action','additional_actions','bundle_reason','verification_limit','action_count','hypotheses','next_steps','manual_reviews','scheduling_directive','model_selection','requested_model','plan_reused','automatic_switch','failure_detail') if k in value}}
    except FileNotFoundError:return {'state':'missing','source':'planner-state.json','data':None}
    except (OSError,ValueError,TypeError,UnicodeError,RecursionError):
        return {'state':'invalid','source':'planner-state.json','data':None}
