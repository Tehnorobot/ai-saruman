"""Small deterministic role views. Source meaning belongs to the LLM, facts to runs.

Full inputs and reports remain immutable artifacts. These views are navigation,
not a second source of requirements or evidence of acceptance.
"""
from copy import deepcopy
import difflib
import hashlib
import json
from pathlib import PurePosixPath

from common import acceptance
from common.history import _open


RUNTIME_POLICY=('Use exact run logs, execution receipts and frozen build sources before diagnosing a historical run. '
    'Current workspace code may differ from the code that ran. Missing final JUnit, or an initial SKIP saying '
    'the scenario is running, is not evidence that no action or enable occurred. Command completion, scenario '
    'entry, fixture setup, product action and completed product assertions are separate observations. '
    'Only completed independent checks establish PASS. An exception names an observed failure; its cause '
    'remains a hypothesis until source and an experiment support it. State competing explanations and a '
    'concrete distinguishing experiment when evidence is incomplete; ordinary API/documentation uncertainty '
    'does not block supported implementation or erase useful code, tests, reviews or unresolved findings.')
FIXTURE_POLICY=('The platform supplies a generic runnable world and declared dependencies. Project-specific '
    'blocks, entities, structures and positions require executable setup in the test/fixture code. Prose, '
    'a manifest description or a renamed checkpoint does not prepare that state. Separate executable '
    'preparation, independent fixture assertions, the required product action and product effect assertions. '
    'Report a fixture repair as implemented only when executable setup or its invocation actually changed '
    'and independent fixture assertions cover the required state; otherwise '
    'keep it partial or needs_experiment and identify the missing code. These are review guidance, not new '
    'product conditions or automatic acceptance gates.')


def capture_sources(workspace):
    inventory={}
    revision=acceptance.source_fingerprint(workspace,inventory)
    files={}
    for path,digest in inventory.items():
        parts=PurePosixPath(path).parts
        if any(part.startswith('.env') or part in ('auth.json','credentials','.aws') for part in parts):continue
        with _open(workspace,parts) as source:raw=source.read()
        try:text=raw.decode('utf-8') if b'\0' not in raw else None
        except UnicodeError:text=None
        files[path]={'sha256':digest,'text':text}
    return {'source_revision':revision,'files':files}


def source_diff(before,after):
    lines=[];changed=[]
    for path in sorted(before['files'].keys()|after['files'].keys()):
        old=before['files'].get(path);new=after['files'].get(path)
        if old==new:continue
        changed.append(path)
        if (old is not None and old['text'] is None) or (new is not None and new['text'] is None):
            lines.append('Binary/source digest change: '+path+'\n');continue
        lines.extend(difflib.unified_diff((old or {}).get('text','').splitlines(keepends=True),
            (new or {}).get('text','').splitlines(keepends=True),fromfile='a/'+path,tofile='b/'+path))
    return {'base_source_revision':before['source_revision'],'source_revision':after['source_revision'],
            'changed_paths':changed,'text':''.join(lines),'meaning':'Actual source diff, not a claim of correctness.'}


def model_input(inputs,*,full_input_ref):
    """Keep actionable IDs and refs; move long previous review bodies on demand."""
    result=deepcopy(inputs)
    review=result.get('review')
    if isinstance(review,dict):
        result['review']={k:deepcopy(review[k]) for k in ('contract','version','scope','disposition') if k in review}
        result['review']['findings']=[{k:deepcopy(f[k]) for k in ('id','kind','severity','requirement_ids') if k in f}
                                      for f in review.get('findings',[])]
        result['review']['full_review_in']=full_input_ref
        result['review']['instruction']='Read $.review in this full input artifact for each finding meaning before responding.'
    assignment=result.get('assignment')
    if isinstance(assignment,dict) and 'instructions' in assignment:
        assignment['instructions']=['Implement the accepted plan and preserve test meaning. '
            'Full assignment and previous reviewer explanations are linked in the full input artifact.']
    return result


def build(role,phase,scope,inputs,*,full_input_ref,latest_result=None,ledger=None,diff_ref=None):
    result={'version':1,'role':role,'phase':phase,'scope':deepcopy(scope),'full_input_ref':full_input_ref,
        'facts':[],'links':{},'interpretation_policy':'Observed failures and model hypotheses are different. '
            'A stopped build does not imply failure of unstarted product checks. Historical observations retain their source scope.',
        'runtime_evidence_policy':RUNTIME_POLICY,'fixture_implementation_policy':FIXTURE_POLICY}
    for key in ('catalog_ref','graph_ref','condition_progress_ref','memory_index_ref','latest_result_refs','subject_refs'):
        if key in inputs:result['links'][key]=deepcopy(inputs[key])
    if diff_ref:result['links']['diff_ref']=deepcopy(diff_ref)
    if latest_result:
        result['previous_observation_scope']=deepcopy(latest_result.get('scope'))
        result['previous_build_status']=latest_result.get('build_status','NOT_EXECUTED')
        navigation=latest_result.get('observation_navigation',{})
        if isinstance(navigation,dict):
            for key in ('build_artifacts_ref','scenario_evidence_ref'):
                if key in navigation:result['links'][key]=deepcopy(navigation[key])
            if navigation.get('full_ref'):
                result['links']['observation_navigation_ref']=deepcopy(navigation['full_ref'])
            if navigation.get('errors'):
                result['runtime_navigation_errors']=deepcopy(navigation['errors'][:4])
            summaries=[];observations=[]
            for row in navigation.get('scenarios',[]):
                summaries.append({'stage':'runtime_execution','scenario_id':row['scenario_id'],
                    'scenario_entered':row.get('scenario_entered'),
                    'scenario_entry_observation':'observed' if row.get('scenario_entered') is True
                        else 'not_observed_in_available_markers',
                    'command':deepcopy(row.get('command',{})),
                    'terminal':deepcopy(row.get('terminal',{})),'journal_status':row.get('journal_status'),
                    'evidence_refs':[deepcopy(row['receipt_ref'])]})
                observations.extend({'stage':'runtime_observation','scenario_id':row['scenario_id'],**deepcopy(item)}
                    for item in row.get('observations',[]))
            # Prioritize observed terminal events and exception declarations as
            # symptoms, never guessed causes based on words in their messages.
            observations.sort(key=lambda item:0 if item.get('event') in ('exception_declaration','failed','crashed')
                else 1 if item.get('event') in ('BEGIN','started') else 2)
            runtime=summaries+observations
            # The index retains every observation and original. The prompt is
            # an explicit preview, never a hidden selection of causal evidence.
            result['runtime_fact_count']=len(runtime)
            result['runtime_facts']=runtime[:12]
            result['runtime_facts_partial']=len(runtime)>12
            entered={row['scenario_id'] for row in navigation.get('scenarios',[]) if row.get('scenario_entered') is True}
            conflicts=[row['scenario_id'] for row in latest_result.get('scenario_results',[])
                       if row.get('attempted') is False and row.get('scenario_id') in entered]
            if conflicts:
                result['historical_lifecycle_conflicts']={'scenario_ids':conflicts,
                    'instruction':'Historical not_attempted classification conflicts with exact log/receipt '
                        'entry observations. Preserve the canonical result, inspect original runtime facts, '
                        'and do not infer that no action occurred from this historical classification.'}
            result['frozen_build_navigation']=('Read build_artifacts_ref: /artifacts lists frozen archives; '
                '/artifacts/<index>/source_members lists exact source member paths and refs. Read a '
                'member ref with line selectors. Compare the historical source with the current diff.')
        for diagnostic in latest_result.get('execution_diagnostics',[]):
            fact={k:deepcopy(diagnostic[k]) for k in ('diagnostic_id','stage','label','status','exit_code','argv','evidence_refs') if k in diagnostic}
            # Compact preview is explicit; complete original is reachable by refs.
            actual=diagnostic.get('actual','')
            fact['preview']=actual[:1600];fact['preview_truncated']=len(actual)>1600
            result['facts'].append(fact)
        rows=latest_result.get('scenario_results',[])
        failed=[(row,cp) for row in rows if row.get('attempted') is not False
                for cp in row.get('checkpoints',[]) if cp.get('status') in ('FAIL','ERROR')]
        result['failed_checkpoint_count']=len(failed)
        # Do not fan a suite into an oversized prompt. The first actual failure
        # is visible; every checkpoint remains in the linked immutable result.
        if not result['facts'] and failed:
            row,cp=failed[0]
            text=cp.get('actual','')
            result['facts'].append({'stage':'scenario','scenario_id':row['scenario_id'],
                'checkpoint_id':cp['checkpoint_id'],'status':cp['status'],
                'preview':text[:1600],'preview_truncated':len(text)>1600,
                'evidence_refs':deepcopy(cp.get('evidence_refs',[]))})
        result['not_attempted']=[{'scenario_id':row['scenario_id'],'stopped_by':row.get('stopped_by',[])}
            for row in rows if row.get('attempted') is False]
        if latest_result.get('build_status') in ('FAIL','ERROR') and not latest_result.get('execution_diagnostics'):
            result['diagnostics_unavailable']=('Historical result has no execution diagnostic index. '
                'Use linked canonical logs; do not infer the build cause from missing scenario reports.')
    if ledger:
        result['causal_memory_ref']=deepcopy(ledger)
    return result


def availability(manifest,read_source):
    """A declared, readable scenario is implementation availability, never PASS."""
    rows={}
    if not manifest:return rows
    for scenario in manifest.get('scenarios',[]):
        ref=scenario.get('test_file')
        if not isinstance(ref,dict):continue
        try:current=read_source(ref)
        except (OSError,ValueError,KeyError,TypeError):continue
        for tid in scenario.get('task_ids',[]):
            rows.setdefault(tid,{'implementation':'present_claim','implementation_refs':[]})
            actual=current if isinstance(current,dict) and 'sha256' in current else ref
            if actual not in rows[tid]['implementation_refs']:rows[tid]['implementation_refs'].append(actual)
    return rows


def continuation_key(job_id,round_number,role,phase,binding):
    raw=json.dumps([job_id,round_number,role,phase,binding],separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()
