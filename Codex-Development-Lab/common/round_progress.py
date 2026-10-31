"""Controller contract for a useful post-bootstrap round, never an agent verdict."""
import re
from common import coverage,acceptance_progress

KINDS={'implement_and_map_requirement':'new_behavior','repair_scenario':'repair',
       'implement_or_verify_scenario':'verification','review_coverage':'coverage',
       'repair_verification_failure':'infrastructure','repair_acceptance_plan':'infrastructure',
       'diagnose_failure':'diagnosis','await_user_review':'manual'}


def policy(catalog,plan,assignment,ledger,previous,state):
    build=any(c.get('label')=='gradle_build' and c.get('status')=='PASS'
              for c in (previous or {}).get('checks',[]))
    gameplay=False
    for scenario in plan.get('scenarios',[]):
        entry=ledger.get('scenarios',{}).get(scenario['id']) or {}
        record=(entry.get('last_pass') or {}) if isinstance(entry,dict) else {}
        result=(record.get('result') or {}) if isinstance(record,dict) else {}
        if (scenario.get('kind')=='gameplay' and isinstance(record,dict) and isinstance(result,dict)
                and record.get('definition_sha256')==acceptance_progress.definition_digest(scenario)
                and result.get('test_status')=='PASS'):
            gameplay=True;break
    if not build or not gameplay:return {'enabled':False,'version':1}
    members=set((assignment.get('current_task') or {}).get('requirement_ids',[]))
    pending=[];review=[]
    for rid,items in coverage.obligations(catalog).items():
        if rid not in members:continue
        statuses={c['id']:c for c in plan.get('_coverage',{}).get(rid,{}).get('obligations',[])}
        for item in items:
            if re.fullmatch(r'\s*(?:\d+[.)]|[-*])\s*',item['text']):continue
            status=statuses.get(item['id'],{})
            if not status.get('scenario_ids') or status.get('review_status')=='missing':
                pending.append({'requirement_id':rid,'condition_id':item['id']})
            elif not status.get('reviewed'):review.append(rid)
    scenarios={s['id']:s for r in state.get('requirements',[]) for s in r.get('scenarios',[])}
    return {'enabled':True,'version':1,'pending_conditions':pending[:128],
            'pending_conditions_total':len(pending),'pending_conditions_omitted':max(0,len(pending)-128),
            'coverage_review_requirement_ids':sorted(set(review)),
            'failed_scenario_ids':sorted(sid for sid,s in scenarios.items() if s.get('status')=='FAIL'),
            'verification_scenario_ids':sorted(sid for sid,s in scenarios.items() if s.get('status') in ('STALE','NOT_TESTED'))[:128]}


def validate(action,context):
    rule=context.get('round_progress_policy') or {}
    if not rule.get('enabled'):return
    expected=action.get('expected_progress')
    if not isinstance(expected,dict) or expected.get('kind')!=KINDS[action['kind']]:
        raise ValueError('Each post-bootstrap action needs expected_progress.kind matching its actual purpose: '+KINDS[action['kind']])
    result=expected.get('observable_result')
    if not isinstance(result,str) or not 24<=len(result.strip())<=1200:
        raise ValueError('expected_progress.observable_result must describe a concrete checkable result (24 to 1200 characters)')
    if action['kind']=='implement_and_map_requirement':
        candidates={p['condition_id'] for p in rule.get('pending_conditions',[])
                    if p['requirement_id']==action['target']}
        if not candidates.intersection(action.get('condition_ids',[])):
            raise ValueError('Implementation must address an unresolved substantive source condition, not repeat startup or packaging claims')
        if action.get('verification_kind') not in ('gameplay','unit') or len(action.get('verification_objective','').strip())<24:
            raise ValueError('New behavior needs verification_kind and a concrete verification_objective')
    if action['kind']=='repair_scenario':
        failed=set(rule.get('failed_scenario_ids',[]))|{s.get('id') for s in context.get('unresolved_scenario_failures',[])}
        if action['target'] not in failed:raise ValueError('Repair must address this specific independently observed failure')
    if action['kind']=='implement_or_verify_scenario' and action['target'] not in rule.get('verification_scenario_ids',[]):
        raise ValueError('Verification must address an actually unverified or stale scenario')
    if action['kind']=='review_coverage' and action['target'] not in rule.get('coverage_review_requirement_ids',[]):
        raise ValueError('Coverage review must address pending mapped conditions, not repeat an unchanged completed review')
    if action['kind'] in ('diagnose_failure','repair_verification_failure'):
        checks=context.get('previous_independent_verification',{}).get('checks',[])
        if not (rule.get('failed_scenario_ids') or context.get('unresolved_scenario_failures')
                or any(c.get('status') in ('FAIL','ERROR','TIMEOUT') for c in checks)):
            raise ValueError('Diagnosis/infrastructure repair requires an independently observed failure')
    if action['kind']=='repair_acceptance_plan' and not context.get('acceptance_plan_notes'):
        raise ValueError('Acceptance-plan repair requires an actual controller schema error')


def outcome(decision,plan,results):
    """Report actual execution separately from the proposed progress. No PASS for REQs."""
    from common.planning import round_actions
    rows={s['id']:s for s in results.get('scenarios',[]) if isinstance(s,dict)}
    requirements={r['id']:r for r in plan.get('requirements',[])}
    scenario_ids={s['id'] for s in plan.get('scenarios',[])}
    observed=[]
    for action in round_actions(decision):
        expected=action.get('expected_progress')
        if not expected:continue
        ids=set(action.get('verification_scenario_ids',[]))
        if action['kind'] in ('repair_scenario','implement_or_verify_scenario'):ids.add(action['target'])
        # Diagnosis can target either a scenario or an infrastructure check.
        # Only a known scenario target implies an expected scenario execution.
        scenario_diagnosis=action['kind']=='diagnose_failure' and action['target'] in scenario_ids
        if scenario_diagnosis:ids.add(action['target'])
        if action['kind']=='implement_and_map_requirement':
            mapping=requirements.get(action['target'],{}).get('obligation_scenarios',{})
            ids.update(sid for cid in action.get('condition_ids',[]) for sid in mapping.get(cid,[]))
        executed=[{'id':sid,'status':rows[sid].get('status'),'test_status':rows[sid].get('test_status')}
                  for sid in sorted(ids) if sid in rows and rows[sid].get('test_status') in ('PASS','FAIL')]
        observed.append({'target':action['target'],'expected_progress':expected,'executed_scenarios':executed,
                         'execution_applicable':scenario_diagnosis or action['kind'] in ('implement_and_map_requirement','repair_scenario','implement_or_verify_scenario'),
                         'execution_observed':bool(executed),
                         'note':'Execution is evidence for the scenario only; full source coverage requires independent review.'})
    return {'schema_version':1,'actions':observed,'establishes_requirement_pass':False}
