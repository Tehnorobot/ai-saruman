"""Per-condition observation history, derived only from independent runner records.

This is scheduling advice, never acceptance authority or a pause policy. Error
prose, source revisions and neighbouring tests do not reset a failing checkpoint.
The original plans, code reports and results remain in immutable event memory.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from common import pipeline_memory as memory


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def observations(manifest, result):
    """Keep the same effective verdict rules as the coverage controller."""
    rows=[]
    by_id={s['scenario_id']:s for s in result['scenario_results']}
    for scenario in manifest['scenarios']:
        actual=by_id.get(scenario['id'])
        if actual is None:continue  # An unselected scenario is not another attempt.
        checkpoints={c['checkpoint_id']:c for c in actual['checkpoints']}
        fixture=actual.get('fixture_result') or {}
        raw_checkpoints={c['checkpoint_id']:c for c in fixture.get('raw_checkpoints',[])}
        bindings={b['checkpoint_id']:(b['classname'],b['name'])
                  for b in scenario.get('execution',{}).get('checkpoint_tests',[])}
        failed={c['checkpoint_id'] for c in actual['checkpoints'] if c['status']=='FAIL'}
        global_failure=(actual.get('execution_stage')!='build' and actual['status']=='FAIL' and (not failed or any(
            c['id'] in failed and not c['condition_ids'] for c in scenario['checkpoints'])))
        for checkpoint in scenario['checkpoints']:
            observed=checkpoints.get(checkpoint['id'],{'status':'NOT_EXECUTED'})
            outcome=observed['status']
            fixture_isolates = (fixture and fixture.get('status')!='PASS' and actual['status']!='ERROR'
                and 'affected_checkpoint_ids' in fixture
                and checkpoint['id'] not in fixture['affected_checkpoint_ids'])
            if outcome=='PASS' and ((actual['status'] in ('ERROR','NOT_EXECUTED','SKIPPED') and not fixture_isolates) or global_failure):
                outcome='FAIL' if global_failure else 'NOT_EXECUTED'
            if outcome=='PASS' and result['build_status']!='PASS':outcome='NOT_EXECUTED'
            raw_status=raw_checkpoints.get(checkpoint['id'],observed)['status']
            # A prior build barrier is not a failed product experiment. For
            # historical records, absence of any checkpoint proof plus a failed
            # build is insufficient evidence that the product was attempted.
            attempted=actual.get('attempted')
            if attempted is None:
                attempted=not (result['build_status']!='PASS' and not observed.get('evidence_refs'))
            fixture_invalid=(fixture and fixture.get('status')!='PASS' and
                checkpoint['id'] in fixture.get('affected_checkpoint_ids',[c['id'] for c in scenario['checkpoints']])
                and observed['status']!='FAIL')
            measured=bool(attempted and raw_status in ('PASS','FAIL') and not fixture_invalid)
            attempt_kind='measurement' if measured else 'invalid_evidence' if attempted and raw_status=='ERROR' else 'not_attempted'
            for cid in checkpoint['condition_ids']:
                rows.append({'condition_id':cid,'scenario_id':scenario['id'],
                    'checkpoint_id':checkpoint['id'],'status':outcome,
                    'raw_checkpoint_status':raw_status,'attempt_kind':attempt_kind,
                    'stopped_by':list(actual.get('stopped_by',[])),
                    'execution_id':actual.get('execution_id',result['runner_run_id']),
                    'measurement_id':fingerprint([{k:result['scope'][k] for k in ('project_id','spec_revision','source_revision')},
                        actual.get('execution_id',result['runner_run_id']),
                        bindings.get(checkpoint['id'],(scenario['id'],checkpoint['id']))]),
                    'scenario_status':actual['status'],'build_status':result['build_status'],
                    **({'fixture_status':fixture['status'] if not fixture_isolates else 'PASS'} if fixture else {}),
                    'test_contract':deepcopy(checkpoint)})
    return rows


def same_scope(left,right,*,source=False):
    keys=('project_id','spec_revision','source_revision') if source else ('project_id','spec_revision')
    return all(left.get(k)==right.get(k) for k in keys)


def reconstruct(job_root,catalog,scope,manifest_loader,*,last_plan_id=None):
    """Replay the full trusted archive; old projects need no reset or model call.

    manifest_loader reads a controller-owned historical snapshot, never a mutable
    agent workspace. New observations have their mapping in immutable memory.
    Unavailable historical mappings are reported explicitly, not guessed.
    """
    index=memory.index_entries(job_root)
    selected=[m for m in index if same_scope(m['scope'],scope)]
    metas={m['record_id']:m for m in selected}
    payloads={}
    def payload(rid):
        if rid not in payloads:payloads[rid]=memory.read_payload(job_root,rid)
        return payloads[rid]
    def reference(rid):
        m=metas.get(rid)
        return None if not m else {'record_id':rid,'record_ref':m['full_record_ref'],
                                  'payload_ref':m['payload_ref']}
    mappings={}
    for meta in selected:
        if meta['kind']=='reflection' and meta['producer']=='controller':
            v=payload(meta['record_id'])
            if v.get('artifact_type')=='ConditionObservationMapping':
                mappings[v['verification_record_id']]=v['manifest']
    profiles={c['id']:{'condition_id':c['id'],'requirement_id':c['requirement_id'],
        'history':[],'checkpoints':[]} for c in catalog['conditions']}
    missing=[];plans={};seen_runs=set()
    for meta in selected:
        if meta['kind']!='verification' or meta['producer']!='runner':continue
        rid=meta['record_id'];result=payload(rid)
        if result.get('contract')!='VerificationResult':continue
        run_key=(result['runner_run_id'],result['scope']['source_revision'],result['scope']['round'],
                 result['request_ref']['sha256'])
        if run_key in seen_runs:continue  # Replaying a checkpoint is not a new run.
        seen_runs.add(run_key)
        manifest=mappings.get(rid)
        if manifest is None:manifest=manifest_loader(result['scope'])
        if not manifest or not same_scope(manifest.get('scope',{}),result['scope'],source=True):
            missing.append(reference(rid));continue
        record=memory.load_record(job_root,rid)
        links=record['entry']['links']
        plan_id=next((x['record_id'] for x in links if x['relation']=='tests_hypothesis'),None)
        coder_id=next((x['record_id'] for x in links if x['relation']=='verifies'),None)
        plan=payload(plan_id) if plan_id in metas else {}
        if plan.get('contract')!='PlannerDecision':plan={}
        if plan:plans[plan_id]=plan
        coder=payload(coder_id) if coder_id in metas else {}
        if coder.get('contract')!='CoderReport':coder={}
        rows=observations(manifest,result)
        assigned={c for a in plan.get('actions',[]) for c in a['condition_ids']}
        assigned.update(plan.get('regression_condition_ids',[]))
        missing_observations=assigned-{r['condition_id'] for r in rows}
        rows.extend({'condition_id':cid,'scenario_id':None,'checkpoint_id':None,
            'status':'NOT_EXECUTED','raw_checkpoint_status':'NOT_EXECUTED',
            'attempt_kind':'not_attempted','stopped_by':[],
            'scenario_status':None,'build_status':result['build_status'],'test_contract':None}
            for cid in sorted(missing_observations))
        for row in rows:
            cid=row['condition_id']
            if cid not in profiles:continue
            actions=[a for a in plan.get('actions',[]) if cid in a['condition_ids']]
            proposal={'actions':[{k:a[k] for k in ('kind','goal','expected_observation')} for a in actions],
                      'hypotheses':[h for h in plan.get('hypotheses',[]) if cid in h.get('condition_ids',[])]}
            profiles[cid]['history'].append({**row,'round':result['scope']['round'],
                'source_revision':result['scope']['source_revision'],
                'observation_id':fingerprint([rid,cid,row['scenario_id'],row['checkpoint_id']]),
                'result_ref':reference(rid),'plan_ref':reference(plan_id),
                'coder_ref':reference(coder_id),'proposal_fingerprint':fingerprint(proposal),
                'changed_file_paths':[f['path'] for f in coder.get('changed_files',[])],
                'coder_report_scope':coder.get('scope'),
                'changes_reported_this_round':coder.get('scope',{}).get('round')==result['scope']['round'],
                'interpretation':'Runner outcome is factual; hypotheses and code claims require semantic analysis.',
                'followup_analysis_refs':[]})
    if last_plan_id in metas:
        v=payload(last_plan_id)
        if v.get('contract')=='PlannerDecision':plans[last_plan_id]=v
    # Later accepted plans contain the agent's interpretation of earlier results.
    analyses={}
    for rid in plans:
        rec=memory.load_record(job_root,rid)
        for link in rec['entry']['links']:
            if link['relation']=='responds_to':analyses.setdefault(link['record_id'],[]).append(reference(rid))
    for profile in profiles.values():
        groups={}
        for row in profile['history']:
            row['followup_analysis_refs']=analyses.get(row['result_ref']['record_id'],[])
            # Receiving an actual measurement resolves an earlier lack of any
            # observation. It does not make the condition or checkpoint PASS.
            if (row['scenario_id'] is not None and row['raw_checkpoint_status'] in ('PASS','FAIL','ERROR')
                    and row.get('fixture_status','PASS')=='PASS'):
                gap=groups.get((None,None))
                if gap:
                    gap.update(unresolved_observations=0,first_unresolved_round=None,
                               unresolved_gaps=0,first_gap_round=None,
                               last_status='OBSERVATION_AVAILABLE',observation_available_round=row['round'])
            key=(row['scenario_id'],row['checkpoint_id'])
            g=groups.setdefault(key,{'scenario_id':key[0],'checkpoint_id':key[1],
                'observations':0,'measurements':0,'measurement_ids':[], 'non_measurement_observations':0,
                'unresolved_gaps':0,'first_gap_round':None,
                'unresolved_observations':0,'failures_since_pass':0,
                'last_verified_round':None,'first_unresolved_round':None,'contract_changed':False,
                'exact_proposal_repeats':0})
            g['observations']+=1
            if g.get('last_test_contract') is not None and g['last_test_contract']!=row['test_contract']:
                g['contract_changed']=True
            g['last_test_contract']=row['test_contract']
            measured=row.get('attempt_kind')=='measurement'
            if measured and row.get('measurement_id') in g['measurement_ids']:
                measured=False;row['duplicate_measurement']=True
            elif measured:g['measurement_ids'].append(row.get('measurement_id'))
            if measured:g['measurements']+=1
            else:
                g['non_measurement_observations']+=1
                if not row.get('duplicate_measurement'):
                    g['unresolved_gaps']+=1
                    if g['first_gap_round'] is None:g['first_gap_round']=row['round']
            if measured:
                g.update(unresolved_gaps=0,first_gap_round=None,
                         last_measurement_status=row['raw_checkpoint_status'],last_measurement_round=row['round'],
                         last_measurement_result_ref=row['result_ref'])
            if row['status']=='PASS' and measured:
                g.update(unresolved_observations=0,failures_since_pass=0,
                         first_unresolved_round=None,last_verified_round=row['round'],exact_proposal_repeats=0)
            elif measured and row['status'] in ('FAIL','ERROR'):
                if g['unresolved_observations']==0:g['first_unresolved_round']=row['round']
                g['unresolved_observations']+=1
                if row['status'] in ('FAIL','ERROR'):g['failures_since_pass']+=1
                if g['unresolved_observations']>1 and g.get('last_proposal_fingerprint')==row['proposal_fingerprint']:
                    g['exact_proposal_repeats']+=1
            g.update(last_round=row['round'],last_status=row['status'],latest_result_ref=row['result_ref'])
            if measured:g['last_proposal_fingerprint']=row['proposal_fingerprint']
        profile['checkpoints']=list(groups.values())
        profile['observation_count']=len(profile['history'])
        profile['measurement_count']=len({r['measurement_id'] for r in profile['history'] if r.get('attempt_kind')=='measurement'})
        profile['last_observed_round']=max((r['round'] for r in profile['history']),default=None)
        profile['repeated_unresolved']=any(g['unresolved_observations']>1 for g in groups.values())
        profile['repeated_observation_gaps']=any(g['unresolved_gaps']>1 for g in groups.values())
    return {'schema_version':1,'project_id':scope['project_id'],'spec_revision':scope['spec_revision'],
            'conditions':list(profiles.values()),'unmapped_verification_refs':missing,
            'provenance':'Controller-derived scheduling history; no acceptance overrides or automatic pauses.'}


def selection_advice(projection,graph,coverage):
    """Expose independent choices and dependencies; the planner chooses the size."""
    repeated={p['condition_id'] for p in projection['conditions'] if p['repeated_unresolved']}
    states={c['condition_id']:c['status'] for c in coverage}
    tasks={t['id']:t for t in graph['tasks']}
    affected={t['id'] for t in tasks.values() if any(c in repeated and states.get(c)!='verified'
                                                  for c in t['condition_ids'])}
    def ancestors(tid):
        seen=set();pending=list(tasks[tid]['depends_on'])
        while pending:
            dep=pending.pop()
            if dep in seen or dep not in tasks:continue
            seen.add(dep);pending.extend(tasks[dep]['depends_on'])
        return seen
    options=[];dependent=[]
    for task in tasks.values():
        unresolved=[c for c in task['condition_ids'] if states.get(c) not in ('verified','manual_pending')]
        if not unresolved or task['id'] in affected:continue
        blockers=sorted(ancestors(task['id'])&affected)
        row={'task_id':task['id'],'title':task['title'],'condition_ids':unresolved,
             'depends_on':task['depends_on'],'unresolved_failure_dependencies':blockers,
             'work_kind':'implementation' if any(states.get(c)=='untested' for c in unresolved) else 'repair_or_regression'}
        (dependent if blockers else options).append(row)
    options.sort(key=lambda t:(t['work_kind']!='implementation',t['task_id']))
    return {'repeated_condition_ids':sorted(repeated),'independent_options':options,
            'dependent_options':dependent,'open_return_task_ids':sorted(affected),
            'policy':'Advisory only. Choose related tasks by dependencies and utility, with no fixed action count. '
                     'For each repeated condition choose a discriminating new experiment or defer it with a concrete '
                     'next step/return_when while implementing independent work. Explain when a prerequisite '
                     'makes focused diagnosis more useful. Never repeat a failed hypothesis merely with new wording.'}
