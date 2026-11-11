"""Read public Codex CLI counters, without reading profiles or estimating billing.

The gateway represents input categories as disjoint counters. Codex's input
counter includes cache hits, so normalization must subtract those hits once.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from threading import RLock

from common import history

FIELDS=('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens')
MAX_SCAN_BYTES=32*1024*1024
MAX_LINE=1024*1024
MAX_CACHE_FILES=2048
MAX_JOB_SCAN_BYTES=128*1024*1024
_CACHE=OrderedDict()
_LOCK=RLock()


def _empty():
    return {**dict.fromkeys(FIELDS,0),'requests':0,'billing_unknown_requests':0,
            'estimated_rub':None,'cost_available':False}


def metrics(row):
    """Enrich disjoint usage counters; cache hits are not extra input tokens."""
    row=dict(row)
    incoming=sum(row.get(k,0) or 0 for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens'))
    cached=row.get('cache_read_input_tokens',0) or 0
    row.update(input_tokens_total=incoming,total_tokens=incoming+(row.get('output_tokens',0) or 0),
               saved_input_tokens=cached,reused_input_tokens=cached,
               cache_savings_money=None,cache_savings_meaning='Cache reads are reused input, not free tokens. Monetary savings require provider tariffs.',
               cache_hit_percent=round(100*cached/incoming,2) if incoming else 0)
    return row


def _normalize(value):
    if not isinstance(value,dict):return None
    # Missing and malformed counters are unknown, never zero-cost successes.
    if any(type(value.get(k)) is not int or not 0<=value[k]<=2**53-1
           for k in ('input_tokens','output_tokens')):return None
    cached=value.get('cached_input_tokens',0)
    if type(cached) is not int or not 0<=cached<=value['input_tokens']:return None
    return {'input_tokens':value['input_tokens']-cached,'output_tokens':value['output_tokens'],
            'cache_creation_input_tokens':0,'cache_read_input_tokens':cached}


def _scan(root,parts,budget=None,*,full_scan=False):
    """Cache counters only; append-only live logs are scanned incrementally."""
    key=(str(Path(root).absolute()),tuple(parts),full_scan)
    try:
        with history._open(root,parts) as source:
            info=os.fstat(source.fileno());identity=(info.st_dev,info.st_ino)
            with _LOCK:
                old=_CACHE.get(key)
                if old and old['identity']==identity and old['size']==info.st_size and old['mtime']==info.st_mtime_ns:
                    _CACHE.move_to_end(key);return copy.deepcopy(old['value'])
                if budget is not None and budget[0]<=0:return None
                append=bool(old and old['identity']==identity and info.st_size>old['size']
                            and not old['value']['truncated'])
                if append:
                    # Size growth alone is not proof of append: a writer can
                    # rewrite/truncate the same inode between dashboard polls.
                    source.seek(0);prefix=source.read(min(4096,old['size']))
                    source.seek(max(0,old['size']-4096));tail=source.read(min(4096,old['size']))
                    append=(hashlib.sha256(prefix).digest()==old['prefix_hash']
                            and hashlib.sha256(tail).digest()==old['tail_hash'])
                value=copy.deepcopy(old['value']) if append else {'counts':dict.fromkeys(FIELDS,0),
                      'turns':0,'turn_events':[],'malformed_usage_events':0,'truncated':False}
                offset=old['offset'] if append else 0
            source.seek(offset)
            scanned=0;limit=info.st_size if full_scan else min(MAX_SCAN_BYTES,budget[0]) if budget is not None else MAX_SCAN_BYTES
            while scanned<limit:
                position=source.tell();line=source.readline(min(MAX_LINE+1,limit-scanned))
                if not line:break
                scanned+=len(line)
                if len(line)>MAX_LINE:
                    while not line.endswith(b'\n') and scanned<limit:
                        line=source.readline(min(MAX_LINE,limit-scanned));scanned+=len(line)
                        if not line:break
                    if not line.endswith(b'\n'):
                        value['truncated']=True;break
                    offset=source.tell();continue
                if not line.endswith(b'\n'):
                    # An active writer may still append the rest of this event.
                    source.seek(position);break
                offset=source.tell()
                try:event=json.loads(line)
                except (ValueError,UnicodeError,RecursionError):continue
                if not isinstance(event,dict) or event.get('type')!='turn.completed':continue
                usage=_normalize(event.get('usage'))
                if usage is None:
                    value['malformed_usage_events']+=1;continue
                value['turns']+=1
                value['turn_events'].append({'offset':position,'counts':usage})
                for field in FIELDS:value['counts'][field]+=usage[field]
            if scanned>=limit and source.tell()<info.st_size:value['truncated']=True
            if budget is not None:budget[0]-=scanned
            source.seek(0);prefix=source.read(min(4096,info.st_size))
            source.seek(max(0,info.st_size-4096));tail=source.read(min(4096,info.st_size))
            state={'identity':identity,'size':info.st_size,'mtime':info.st_mtime_ns,'offset':offset,'value':value,
                   'prefix_hash':hashlib.sha256(prefix).digest(),'tail_hash':hashlib.sha256(tail).digest()}
            with _LOCK:
                _CACHE[key]=state;_CACHE.move_to_end(key)
                while len(_CACHE)>MAX_CACHE_FILES:_CACHE.popitem(last=False)
            return copy.deepcopy(value)
    except (OSError,ValueError):return None


def _date(value):
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).date().isoformat()
    except (AttributeError,ValueError,OverflowError):return 'unknown'


def _timestamp(value):
    """Comparable UTC sort keys for gateway epoch seconds and CLI ISO dates.

    Malformed timestamps remain visible at the end, never compare unlike types
    or depend on the dashboard machine's local timezone.
    """
    if type(value) in (int,float):
        return value if math.isfinite(value) and 0<=value<=253402300799 else 0
    if isinstance(value,str) and len(value)<=64:
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
            result=parsed.timestamp()
            return result if 0<=result<=253402300799 else 0
        except (ValueError,OverflowError,OSError):pass
    return 0


def codex_usage(artifacts,mailboxes,pid,jid,*,request_limit=200):
    """Aggregate each archived/live attempt once. Only public completed-turn usage counts.

    mailboxes may be role->Path, a sequence of Paths, or one Path. A missing
    provider on legacy public requests is treated as Codex only if CLI usage exists.
    """
    history._ids(pid,jid)
    boxes=list(mailboxes.values()) if isinstance(mailboxes,dict) else (
        [mailboxes] if isinstance(mailboxes,(str,Path)) else list(mailboxes))
    if not boxes:boxes=[Path(artifacts)/'.no-mailbox']
    candidates={};truncated=False
    for mailbox in boxes:
        for item in history._candidates(artifacts,mailbox,pid,jid,limit=None if request_limit is None else history.MAX_ATTEMPTS):
            public,request,log=history._describe(item,artifacts,mailbox,pid,jid)
            provider=(request or {}).get('provider')
            if provider not in (None,'codex'):continue
            options=candidates.setdefault(item['id'],[])
            options.append((public,request,log,item))
            if request_limit is not None and len(candidates)>=history.MAX_ATTEMPTS:truncated=True;break
    totals=_empty();roles=defaultdict(_empty);daily=defaultdict(_empty);requests=[];missing=0
    malformed=0;budget=None if request_limit is None else [MAX_JOB_SCAN_BYTES]
    for tid,options in (list(candidates.items()) if request_limit is None else list(candidates.items())[:history.MAX_ATTEMPTS]):
        selected=None;attempt_values=[]
        # Archived completed counters survive transport retirement and take
        # precedence over stale mailbox copies of exactly the same attempt.
        archived=next((x for x in options if x[3].get('archive')),None)
        if archived:
            meta=history._json(artifacts,(*archived[3]['archive'],'meta.json')) or {}
            if meta.get('status') in ('PASS','FAIL','TIMEOUT','STOPPED','USER_PAUSED','CANCELLED'):
                value=_scan(artifacts,(*archived[3]['archive'],'events.jsonl'),budget,full_scan=request_limit is None)
                if value:attempt_values.append(value)
                if value and value['turns']:selected=(archived[0],archived[1],value)
        if selected is None:
            for public,request,log,item in options:
                value=_scan(*log,budget,full_scan=request_limit is None) if log else None
                if value:attempt_values.append(value)
                if value and value['turns']:
                    selected=(public,request,value);break
        if selected is None:
            if attempt_values:
                malformed+=max(v['malformed_usage_events'] for v in attempt_values)
                truncated=truncated or any(v['truncated'] for v in attempt_values)
            missing+=1;continue
        public,request,value=selected
        malformed+=value['malformed_usage_events'];truncated=truncated or value['truncated']
        role=public.get('role') if public.get('role') in ('planner','coder','reviewer') else 'unknown'
        row={**_empty(),**value['counts'],'requests':value['turns'],'task_id':tid,
             'role':role,'provider':'codex','model':public.get('model',''),
             'status':public.get('status','ARCHIVED'),'started_at':public.get('started_at'),
             'round':public.get('round'),'request_unit':'completed_turns',
             'billing_unknown':False,'cost_unknown':True}
        if request_limit is None:
            for event in value['turn_events']:
                requests.append(metrics({**row,**event['counts'],'requests':1,
                    'request_id':'codex:'+tid+':'+str(event['offset']),
                    'request_unit':'completed_turn','turn_offset':event['offset']}))
        else:requests.append(metrics(row))
        day=_date(row['started_at'])
        for target in (totals,roles[role],daily[day]):
            for field in (*FIELDS,'requests'):target[field]+=row[field]
    return {'available':bool(requests),'codex_usage_available':bool(requests),
            'provider':'codex','source':'codex_cli','token_source':'public_completed_turn_usage',
            'request_unit':'completed_turns','cost_available':False,'cost_is_estimate':True,
            'totals':metrics(totals),'roles':[metrics({**r,'role':role}) for role,r in sorted(roles.items())],
            'requests':sorted(requests,key=lambda r:(_timestamp(r.get('started_at')),r['task_id'],r.get('turn_offset',0)),reverse=True)[:request_limit],
            'daily':[metrics({**r,'day':day}) for day,r in sorted(daily.items())],
            'missing_usage_attempts':missing,'malformed_usage_events':malformed,
            'truncated':truncated or (budget is not None and budget[0]<=0),'timezone':'UTC',
            'completed_turns':totals['requests']}


def merge_usage(api_report,codex_report):
    """Combine CLI usage with gateway counters, preserving unknown monetary cost."""
    api=copy.deepcopy(api_report or {});codex=codex_report or {}
    report={**api,'api_available':bool(api.get('available')),
            'codex_usage_available':bool(codex.get('codex_usage_available')),
            'available':bool(api.get('available') or codex.get('available'))}
    totals=dict(api.get('totals') or _empty())
    for field in (*FIELDS,'requests','billing_unknown_requests'):totals[field]=totals.get(field) or 0
    for field in (*FIELDS,'requests'):totals[field]+=(codex.get('totals') or {}).get(field,0) or 0
    role_rows={r['role']:copy.deepcopy(r) for r in api.get('roles',[]) if isinstance(r,dict) and 'role'in r}
    for role in codex.get('roles',[]):
        row=role_rows.setdefault(role['role'],{**_empty(),'role':role['role']})
        for field in (*FIELDS,'requests'):row[field]=(row.get(field) or 0)+(role.get(field) or 0)
        row.update(estimated_rub=None,cost_available=False)
    if codex.get('codex_usage_available'):
        totals.update(estimated_rub=None,cost_available=False)
        report.update(provider='mixed' if (api.get('totals') or {}).get('requests') else 'codex',
                      cost_available=False,request_unit='api_requests_and_codex_completed_turns')
    totals.update(api_requests=(api.get('totals') or {}).get('requests',0),
                  codex_completed_turns=(codex.get('totals') or {}).get('requests',0))
    report.update(totals=metrics(totals),roles=[metrics(r) for r in role_rows.values()],
                  requests=sorted([*api.get('requests',[]),*codex.get('requests',[])],
                                  key=lambda r:_timestamp(r.get('started_at')),reverse=True)[:200],
                  api_totals=metrics(api.get('totals') or _empty()),
                  codex_daily=[{**r,'date':r.get('day')} for r in codex.get('daily',[])],
                  missing_usage_attempts=codex.get('missing_usage_attempts',0),
                  malformed_usage_events=codex.get('malformed_usage_events',0),
                  codex_truncated=codex.get('truncated',False))
    if not report['available'] and 'totals' not in api:report.pop('totals',None)
    return report
