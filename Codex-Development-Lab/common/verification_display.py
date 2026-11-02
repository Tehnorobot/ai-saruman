"""Read-only UI labels. Historical success never upgrades a current verdict."""
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import PurePosixPath
import re

from common.history import _open
from common.memory_archive import read_json


def summarize(profile):
    runs={}
    for row in profile.get('history',[]):
        ref=row.get('result_ref') or {}
        rid=ref.get('record_id')
        if not rid:continue
        run=runs.setdefault(rid,{'round':row['round'],'source_revision':row['source_revision'],
            'record_id':rid,'statuses':[]})
        run['statuses'].append(row['status'])
    ordered=[]
    for run in runs.values():
        statuses=run.pop('statuses')
        run['outcome']='failed' if any(s in ('FAIL','ERROR') for s in statuses) else (
            'passed' if statuses and all(s=='PASS' for s in statuses) else 'incomplete')
        ordered.append(run)
    return {'latest':ordered[-1] if ordered else None,
            'successes':{r['record_id']:r for r in ordered if r['outcome']=='passed'}}


@lru_cache(maxsize=256)
def _profile_summary(root,name,expected_hash):
    # Read only controller-owned snapshots; never follow a workspace reference.
    if not re.fullmatch(r'condition-history-[0-9a-f]{64}\.json',name):raise ValueError('Invalid history snapshot')
    with _open(root,('pipeline','condition-progress',name)) as handle:raw=handle.read()
    if hashlib.sha256(raw).hexdigest()!=expected_hash:raise ValueError('History snapshot hash mismatch')
    return summarize(json.loads(raw.decode('utf-8')))


def _key(status,history):
    if status in ('verified','failed','manual_pending','blocked'):return status
    if status=='needs_retest' or history['successes']:return 'needs_retest'
    if history['latest'] and history['latest']['outcome']=='incomplete':return 'incomplete'
    return 'untested'


def build(overview,histories):
    conditions={};private={}
    for row in overview.get('status',{}).get('coverage',[]):
        cid=row['condition_id'];history=histories.get(cid,{'latest':None,'successes':{}})
        successes=list(history['successes'].values())
        private[cid]=history
        conditions[cid]={'state':_key(row['status'],history),'latest':history['latest'],
                         'last_success':successes[-1] if successes else None}
    tasks={}
    for task in (overview.get('graph') or {}).get('tasks',[]):
        ids=task['condition_ids'];values=[conditions.get(cid,{'state':'untested'}) for cid in ids]
        keys=[x['state'] for x in values]
        if keys and all(s=='verified' for s in keys):key='verified'
        elif 'failed' in keys:key='failed'
        elif 'needs_retest' in keys:key='needs_retest'
        elif 'incomplete' in keys:key='incomplete'
        elif keys and all(s in ('manual_pending','verified') for s in keys):key='manual_pending'
        elif 'verified' in keys:key='partial'
        else:key='untested'
        histories_for_task=[private.get(cid,{'latest':None,'successes':{}}) for cid in ids]
        common=set.intersection(*(set(h['successes']) for h in histories_for_task)) if histories_for_task else set()
        first=histories_for_task[0]['successes'] if histories_for_task else {}
        success=max((first[rid] for rid in common),key=lambda r:(r['round'],r['record_id']),default=None)
        latest=max((h['latest'] for h in histories_for_task if h['latest']),
                   key=lambda r:(r['round'],r['record_id']),default=None)
        # At the last run any incomplete/failed member remains visible.
        members=[h['latest'] for h in histories_for_task if h['latest'] and latest
                 and h['latest']['record_id']==latest['record_id']]
        reason='checks_failed' if any(m['outcome']=='failed' for m in members) else (
            'execution_incomplete' if any(m['outcome']=='incomplete' for m in members) else (
                'partial_selection' if len(members)<len(ids) else 'checks_passed'))
        outcome={'checks_failed':'failed','execution_incomplete':'incomplete',
                 'partial_selection':'incomplete','checks_passed':'passed'}[reason]
        tasks[task['id']]={'state':key,'last_common_success':success,
            'latest':{**latest,'outcome':outcome,'reason':reason} if latest else None,
            'has_history':bool(latest),'confirmed':Counter(keys)['verified'],'total':len(keys)}
    return {'conditions':conditions,'tasks':tasks}


def for_pipeline(root,overview):
    index=read_json(root,'pipeline/condition-progress.json') or {}
    scope=overview.get('scope',{})
    compatible=(index.get('project_id')==scope.get('project_id')
                and index.get('spec_revision')==scope.get('spec_revision')
                and (overview.get('decomposition') or {}).get('semantic_review')!='pending')
    histories={}
    for row in index.get('conditions',[]) if compatible else []:
        ref=row.get('history_ref') or {}
        name=PurePosixPath(ref.get('path','')).name
        try:histories[row['condition_id']]=_profile_summary(str(root),name,ref['sha256'])
        except (OSError,ValueError,KeyError,TypeError):continue
    return build(overview,histories)
