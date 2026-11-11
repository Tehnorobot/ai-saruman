"""Read-only archive measurements; never print prompts, tool contents or secrets.

Outputs derived audit JSON only. Provider usage below is observed native-API
usage in archived JSONL, not a complete invoice or Codex billing estimate.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics

ROOT=Path(__file__).resolve().parents[1]
SAFE=re.compile(r'[a-f0-9]{14}\Z')


def size(value):
    return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))


def distribution(values):
    values=sorted(values)
    if not values:return {'count':0}
    return {'count':len(values),'median_bytes':round(statistics.median(values)),
            'p90_bytes':values[min(len(values)-1,int(.9*(len(values)-1)))],
            'max_bytes':values[-1],'total_bytes':sum(values)}


jobs=[]
for project in sorted((ROOT/'artifacts').iterdir()):
    if not project.is_dir() or not SAFE.fullmatch(project.name):continue
    for job in sorted(project.iterdir()):
        if not job.is_dir() or not SAFE.fullmatch(job.name):continue
        counts=Counter();roles=defaultdict(Counter);prompt_sizes=defaultdict(list)
        context_sizes=defaultdict(lambda:defaultdict(list));usage=Counter();errors=Counter()
        corrections=Counter();tool_counts=Counter();compaction=Counter();seen=set()
        logs=0;log_bytes=0
        for request_file in sorted(job.glob('round-*/agent-history/*/request.json')):
            if request_file.parent.name in seen:continue
            seen.add(request_file.parent.name)
            try:request=json.loads(request_file.read_text('utf-8'))
            except (OSError,ValueError):counts['unreadable_request']+=1;continue
            role=request.get('role','unknown');counts['attempts']+=1;roles[role]['attempts']+=1
            prompt=request.get('prompt','')
            prompt_sizes[role].append(len(prompt.encode('utf-8')))
            try:context=json.loads(prompt.split('Input JSON:\n',1)[1])
            except (ValueError,IndexError):context={}
            for key,value in context.items():context_sizes[role][key].append(size(value))
            result_file=request_file.parent/'result.json'
            try:result=json.loads(result_file.read_text('utf-8'))
            except (OSError,ValueError):result={}
            status=result.get('status','unavailable');counts[status]+=1;roles[role][status]+=1
            provider=result.get('provider',request.get('provider','unknown'))
            for logfile in request_file.parent.glob('*.jsonl'):
                logs+=1;log_bytes+=logfile.stat().st_size
                with logfile.open('r',encoding='utf-8',errors='replace') as lines:
                    for line in lines:
                        # Parse only event metadata needed for this audit. Tool
                        # output stays local and is never copied to the report.
                        try:event=json.loads(line)
                        except ValueError:counts['malformed_log_line']+=1;continue
                        if not isinstance(event,dict):continue
                        kind=event.get('type')
                        if kind=='usage.updated' and provider in ('minimax','litellm','dslab'):
                            for key in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens'):
                                value=event.get('usage',{}).get(key,0)
                                if isinstance(value,int) and not isinstance(value,bool) and value>=0:usage[key]+=value
                        elif kind=='turn.failed':
                            error=event.get('error',{});code=error.get('code') if isinstance(error,dict) else None
                            errors[code or 'legacy_untyped']+=1
                        elif kind=='correction.record':
                            record=event.get('record',{})
                            corrections[str(record.get('layer','unknown'))+':'+str(record.get('outcome','unknown'))]+=1
                        elif kind=='context.compacted':
                            compaction['events']+=1
                            compaction['omitted_exchanges_reported']+=event.get('omitted_exchanges',0)
                        elif kind=='item.completed':
                            item=event.get('item',{})
                            tool=item.get('tool') or item.get('type')
                            if tool:tool_counts[tool]+=1
        verification=[]
        for path in sorted(job.glob('round-*/verification_complete.json')):
            try:report=json.loads(path.read_text('utf-8'))
            except (OSError,ValueError):continue
            result=report.get('pipeline_result',{})
            verification.append({'round':int(path.parent.name.split('-')[1]),'build_status':result.get('build_status'),
                'scenario_statuses':dict(Counter(row.get('status','unknown') for row in result.get('scenario_results',[]))),
                'infrastructure_error_count':len(result.get('infrastructure_errors',[]))})
        jobs.append({'project_id':project.name,'job_id':job.name,'attempts':dict(counts),
            'roles':{key:dict(value) for key,value in roles.items()},
            'prompt_utf8_bytes':{key:distribution(value) for key,value in prompt_sizes.items()},
            'context_fields_json_utf8_bytes':{role:{key:distribution(value) for key,value in fields.items()}
                                            for role,fields in context_sizes.items()},
            'archived_log_count':logs,'archived_log_bytes':log_bytes,
            'native_api_usage_events_sum':dict(usage),'typed_terminal_failures':dict(errors),
            'api_format_correction_events':dict(corrections),'context_compaction':dict(compaction),
            'tool_events':dict(tool_counts),'verification_rounds':verification})

report={'audit_version':1,'observed_at_utc':datetime.now(timezone.utc).isoformat(),
    'scope':'All readable archived request/result/JSONL and verifier summaries under artifacts; live requests may be absent.',
    'limitations':['No hidden model reasoning was inspected.','Native usage sums are not invoices; cached input is not free.',
                  'Provider errors without typed metadata remain unclassified, not guessed by keywords.',
                  'Active archives are not a transactional snapshot. Old legacy verifier summaries lack pipeline_result.'],
    'job_count':len(jobs),'archived_attempt_count':sum(j['attempts'].get('attempts',0) for j in jobs),
    'archived_log_bytes':sum(j['archived_log_bytes'] for j in jobs),'jobs':jobs}
target=ROOT/'audit/pipeline-history-audit-2026-11-04.json'
target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({key:report[key] for key in ['job_count','archived_attempt_count','archived_log_bytes']},ensure_ascii=False))
for job in jobs:
    print(job['job_id'],json.dumps({'attempts':job['attempts'],'prompts':job['prompt_utf8_bytes'],
        'native_api_usage':job['native_api_usage_events_sum'],'terminal':job['typed_terminal_failures'],
        'corrections':job['api_format_correction_events'],'compaction':job['context_compaction']},ensure_ascii=False))
