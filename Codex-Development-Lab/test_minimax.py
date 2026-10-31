#!/usr/bin/env python3
"""Compare MiniMax role/effort responses. Python 3.10+, standard library only.

Preview (no credentials/network): python test_minimax.py --dry-run
Run 15 requests: python test_minimax.py --run --verify-code
Small run: python test_minimax.py --run --roles planner --efforts low high
Offline checks: python test_minimax.py --self-test

Uses MINIMAX_API_KEY and MINIMAX_BASE_URL from process env or this root's .env.
Never edits .env, calls the Lab queue, sends project files, or runs model code
on the host. Direct API usage is NOT included in the Lab dashboard counters.
API reference: https://platform.minimax.io/docs/api-reference/text-anthropic-api
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import http.client
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
ROLES = ('planner', 'coder', 'reviewer')
MODEL = 'MiniMax-M3.1-Flash'
BASE = 'https://api.minimax.io/anthropic'


def obj(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


STR = {'type': 'string'}
INT = {'type': 'integer'}


def arr(item):
    return {'type': 'array', 'items': item}


SCHEMAS = {
    'planner': obj(
        summary=STR,
        tasks=arr(obj(id=STR, title=STR, requirement_ids=arr(STR), depends_on=arr(STR))),
        actions=arr(obj(kind={'type': 'string', 'enum': ['implement', 'repair', 'diagnose', 'test']},
                        task_ids=arr(STR), goal=STR, evidence_ids=arr(STR))),
        hypotheses=arr(obj(claim=STR, evidence_ids=arr(STR), experiment=STR)),
        deferred=arr(obj(task_id=STR, reason=STR, return_when=STR))),
    'coder': obj(java_source=STR, explanation=STR, proposed_tests=arr(STR)),
    'reviewer': obj(
        findings=arr(obj(line_start=INT, line_end=INT, explanation=STR, risk=STR, reproduction=STR)),
        correct_behaviors=arr(STR), uncertainties=arr(STR)),
}

SELECTION_SPEC = """Target selection rules:
- radius is 32, inclusive. Ignore unreachable targets and nonfinite/negative distances.
- In proactive mode choose the nearest eligible target.
- In defensive mode choose only threatening targets, then the nearest of those.
- Equal distances: lexicographically smallest id wins.
- Retain currentId only if it is still eligible and its distance <= best distance + 1.5.
- No eligible target: return null. Never mutate the candidate list. IDs are unique,
  non-null strings. The list and its Target elements are non-null.
"""

PROMPTS = {
    'planner': """You are a development planner. Read this complete fictional specification:
R1. Choose the nearest reachable target within 32 blocks; defensive mode filters to threatening targets.
R2. Approach that target, attack only within 2.8 blocks, and reselect when the target becomes ineligible.
R3. Cancel immediately when requested; cancel only this addon's navigation, preserving unrelated user tasks.
R4. Supply independent automated tests for selection, approach, attack and cancellation.
R5. Report observed partial success/failure separately. Never infer successful hits from arrival alone.
Current observed evidence:
E1: selection test passed; the existing filter and selection implementation is present.
E2: a game test found the target and approached it, but recorded zero attacks and zero damage.
E3: cancellation has no implementation or test yet; its implementation is independent of the attack defect.
No stack trace or known root cause is available. Test infrastructure works.
Return a compact dependency graph covering R1..R5, and a useful next work package.
Reuse working selection, investigate the attack failure and include the independent cancellation work.
Separate a cause hypothesis from facts; specify an experiment and return conditions for deferred work.
Choose task count by cohesion, not one task per sentence. Do not add product restrictions.
""",
    'coder': SELECTION_SPEC + """
Implement Java 21 code with no dependencies, no package, no I/O or external side effects:
public final class Selector {
  public record Target(String id, double distance, boolean threatening, boolean reachable) {}
  public static String select(java.util.List<Target> candidates, String currentId, boolean defensive) { ... }
}
Use record accessors. Return the complete compilable file as java_source, concise explanation
and proposed tests. Preserve the exact public signature. This is a pure helper, not a full mod.
""",
    'reviewer': SELECTION_SPEC + """
Review this proposed equivalent Python helper against the rules. Report actual defects with
inclusive line numbers and concrete reproductions. State correct behavior too. Do not repair
the code or invent extra requirements. Distinguish a static defect from an untested runtime claim.
1 def select(candidates, current_id, defensive):
2     targets = [t for t in candidates if t['distance'] < 32]
3     if defensive:
4         targets.sort(key=lambda t: not t['threatening'])
5     else:
6         targets.sort(key=lambda t: t['distance'])
7     if current_id:
8         for t in candidates:
9             if t['id'] == current_id:
10                return current_id
11    return targets[0]['id'] if targets else None
""",
}


def env_config(path):
    """Read only the two MiniMax settings; no shell evaluation/interpolation."""
    result = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, sep, value = line.partition('=')
            key = key.strip()
            if not sep or key not in ('MINIMAX_API_KEY', 'MINIMAX_BASE_URL'):
                continue
            value = value.strip()
            if value[:1] in ('"', "'"):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0:
                    raise ValueError('Malformed quoted MiniMax setting in env file')
                trailing = value[end+1:].strip()
                if trailing and not trailing.startswith('#'):
                    raise ValueError('Malformed quoted MiniMax setting in env file')
                value = value[1:end]
            else:
                value = value.split(' #', 1)[0].rstrip()
            result[key] = value
    for key in ('MINIMAX_API_KEY', 'MINIMAX_BASE_URL'):
        if os.environ.get(key, '').strip():
            result[key] = os.environ[key].strip()
    return result


def endpoint(base):
    parts = urlsplit(base.rstrip('/'))
    if (parts.scheme != 'https' or parts.hostname != 'api.minimax.io'
            or parts.port not in (None, 443) or parts.username or parts.password
            or parts.query or parts.fragment
            or parts.path not in ('/anthropic', '/anthropic/v1', '/anthropic/v1/messages')):
        raise ValueError('Use official HTTPS MiniMax Anthropic base URL: '+BASE)
    return '/anthropic/v1/messages'


def request_payload(role, effort, max_tokens):
    return {'model': MODEL, 'max_tokens': max_tokens, 'stream': True,
            'output_config': {'effort': effort},
            'system': 'Solve the given fictional task. Submit the final answer with submit_result alone. '
                      'Use the exact JSON types; arrays remain arrays even for one item. '
                      'Give concise explanations of conclusions. Do not claim game tests were run.',
            'messages': [{'role': 'user', 'content': PROMPTS[role]}],
            'tools': [{'name': 'submit_result', 'description': 'Submit the complete role result as the direct object.',
                       'input_schema': SCHEMAS[role]}], 'tool_choice': {'type': 'auto'}}


def collect_events(stream, show=True, deadline=None):
    """SSE parser with multiline data support; never expose thinking/signatures."""
    blocks = {}; usage = {}; text = []; error = None; stop = None; stopped = False
    data = []; thinking_chars = 0; first_output = None; started = time.monotonic()
    def event(raw):
        nonlocal error, stop, stopped, thinking_chars, first_output
        if not raw or raw == '[DONE]':
            return
        value = json.loads(raw); kind = value.get('type')
        if kind == 'message_start':
            usage.update(value.get('message', {}).get('usage', {}))
        elif kind == 'content_block_start':
            block = value.get('content_block', {})
            blocks[value['index']] = {'type': block.get('type'), 'name': block.get('name'),
                                      'input': block.get('input'), 'json': ''}
            if block.get('type') == 'text' and block.get('text'):
                text.append(block['text'])
                if show: print(block['text'], end='', flush=True)
            thinking_chars += len(block.get('thinking', ''))
        elif kind == 'content_block_delta':
            delta = value.get('delta', {}); dtype = delta.get('type')
            fragment = delta.get('text', '') if dtype == 'text_delta' else delta.get('partial_json', '') if dtype == 'input_json_delta' else ''
            if dtype == 'text_delta':
                text.append(fragment)
            elif dtype == 'input_json_delta':
                blocks.setdefault(value['index'], {'type': 'tool_use', 'name': None, 'input': None, 'json': ''})['json'] += fragment
            elif dtype == 'thinking_delta':
                thinking_chars += len(delta.get('thinking', ''))
            if fragment:
                if first_output is None:first_output = time.monotonic()-started
                if show:print(fragment, end='', flush=True)
        elif kind == 'message_delta':
            usage.update(value.get('usage', {})); stop = value.get('delta', {}).get('stop_reason', stop)
        elif kind == 'message_stop':stopped = True
        elif kind == 'error':error = 'Provider returned an error event (message omitted to avoid credential disclosure)'
    try:
        for raw in stream:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError('Configured request deadline reached')
            line = raw.decode('utf-8').rstrip('\r\n')
            if not line:
                if data:event('\n'.join(data));data = []
            elif line.startswith('data:'):data.append(line[5:].lstrip(' '))
        if data:event('\n'.join(data))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, http.client.HTTPException):
        error = 'Stream interrupted or invalid; partial output and usage retained'
    tools = []
    for block in blocks.values():
        if block['type'] != 'tool_use':continue
        raw = block['json']
        try:value = json.loads(raw) if raw else block['input']
        except ValueError:value = None
        tools.append({'name': block['name'], 'input': value, 'raw_arguments': raw})
    return {'text': ''.join(text), 'tool_calls': tools, 'usage': usage, 'stop_reason': stop,
            'stream_complete': stopped, 'stream_error': error, 'returned_thinking_chars': thinking_chars,
            'first_output_seconds': first_output}


def invoke(key, path, payload, timeout):
    connection = http.client.HTTPSConnection('api.minimax.io', timeout=timeout)
    started = time.monotonic()
    try:
        connection.request('POST', path, json.dumps(payload).encode('utf-8'),
                           {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json',
                            'Accept': 'text/event-stream', 'anthropic-version': '2023-06-01'})
        response = connection.getresponse()
        if response.status != 200:
            # Do not print server error bodies, which can echo credentials.
            return {'http_status': response.status, 'error': 'HTTP '+str(response.status), 'usage': {},
                    'text': '', 'tool_calls': [], 'stream_complete': False, 'stop_reason': None}
        def lines():
            while True:
                remaining = timeout-(time.monotonic()-started)
                if remaining <= 0:raise TimeoutError('Request deadline')
                sock = connection.sock or getattr(getattr(response.fp,'raw',None),'_sock',None)
                if sock is not None:sock.settimeout(remaining)
                line = response.readline()
                if not line:break
                yield line
        read_started = time.monotonic()
        result = collect_events(lines(), show=False, deadline=started+timeout)
        if result['first_output_seconds'] is not None:
            result['first_output_seconds'] += read_started-started
        result['http_status'] = response.status
        return result
    finally:
        connection.close()


def shape_issues(value, schema, path='$'):
    kind = schema['type']; issues = []
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int}[kind]
    if not valid:return [path+': expected '+kind]
    if 'enum' in schema and value not in schema['enum']:issues.append(path+': unsupported enum value')
    if kind == 'object':
        if set(value) != set(schema['required']):issues.append(path+': missing or extra fields')
        for key in set(value) & set(schema['properties']):
            issues += shape_issues(value[key], schema['properties'][key], path+'.'+key)
    elif kind == 'array':
        for n, item in enumerate(value):issues += shape_issues(item, schema['items'], path+'['+str(n)+']')
    return issues


def score(role, response):
    tools = response.get('tool_calls', [])
    native = len(tools) == 1 and tools[0]['name'] == 'submit_result'
    value = tools[0]['input'] if native else None
    if not native:
        try:value = json.loads(response.get('text', ''))
        except ValueError:pass
    complete = bool(response.get('stream_complete') and not response.get('stream_error')
                    and response.get('stop_reason') in ('end_turn', 'tool_use'))
    checks = {'completed_response': complete, 'native_submit_result': native,
              'schema_valid': not shape_issues(value, SCHEMAS[role])}
    result = {'checks': checks, 'format_issues': shape_issues(value, SCHEMAS[role]),
              'role_result': value, 'semantic_review_required': True}
    if not complete or not checks['schema_valid']:return result
    if role == 'planner':
        tasks = value['tasks']; ids = {t['id'] for t in tasks}
        requirements = {r for t in tasks for r in t['requirement_ids']}
        checks['unique_task_ids'] = len(ids) == len(tasks) and bool(tasks)
        checks['source_requirements_only'] = requirements == {'R1','R2','R3','R4','R5'}
        visited = set(); pending = list(tasks)
        while pending:
            ready = [t for t in pending if set(t['depends_on']) <= visited]
            if not ready:break
            visited.update(t['id'] for t in ready);pending = [t for t in pending if t not in ready]
        checks['dependency_graph_valid'] = not pending and checks['unique_task_ids']
        checks['actions_link_tasks'] = bool(value['actions']) and all(a['task_ids'] and set(a['task_ids']) <= ids for a in value['actions'])
        checks['diagnosis_links_failure'] = any(a['kind'] in ('diagnose','repair') and 'E2' in a['evidence_ids'] for a in value['actions'])
        checks['hypothesis_has_experiment'] = any('E2' in h['evidence_ids'] and h['experiment'].strip() for h in value['hypotheses'])
        checks['known_evidence_only'] = all(set(item['evidence_ids']) <= {'E1','E2','E3'} for item in value['actions']+value['hypotheses'])
        checks['deferred_has_return_conditions'] = all(d['task_id'] in ids and d['reason'].strip() and d['return_when'].strip() for d in value['deferred'])
        cancellation = {t['id'] for t in tasks if 'R3' in t['requirement_ids']}
        checks['independent_cancellation_selected'] = any(a['kind'] in ('implement','test') and set(a['task_ids']) & cancellation for a in value['actions'])
    elif role == 'reviewer':
        checks['findings_have_real_lines'] = bool(value['findings']) and all(1 <= f['line_start'] <= f['line_end'] <= 11 for f in value['findings'])
        checks['findings_have_reproductions'] = bool(value['findings']) and all(f['reproduction'].strip() for f in value['findings'])
        checks['acknowledges_correct_behavior'] = bool(value['correct_behaviors'])
        # Hints for human inspection, not an LLM or a semantic PASS judge.
        result['human_rubric'] = ['Unreachable targets accepted (line 2)', '32 boundary excluded (line 2)',
            'Negative distances accepted (line 2)', 'Defensive mode sorts but does not filter (lines 3-4)',
            'Defensive targets not ordered by distance (line 4)', 'Tie-break by id missing (lines 4/6)',
            'Current target bypasses eligibility and hysteresis (lines 7-10)',
            'Empty eligible list returns None correctly (line 11); do not flag this as a defect']
    else:
        checks['java_source_present'] = bool(value['java_source'].strip())
        result['code_verification'] = {'status': 'NOT_EXECUTED', 'reason': 'Use --verify-code for isolated Java compilation and behavioral checks'}
    return result


CASES = [
    ([('far', 10, True, True), ('near', 2, True, True)], None, False, 'near'),
    ([('hidden', 1, True, False), ('ok', 5, True, True)], None, False, 'ok'),
    ([('edge', 32, True, True)], None, False, 'edge'),
    ([('outside', 32.01, True, True)], None, False, None),
    ([('neutral', 1, False, True), ('threat', 5, True, True)], None, True, 'threat'),
    ([('neutral', 1, False, True)], None, True, None),
    ([('z', 3, True, True), ('a', 3, True, True)], None, False, 'a'),
    ([('near', 3, True, True), ('old', 4.5, True, True)], 'old', False, 'old'),
    ([('near', 3, True, True), ('old', 4.51, True, True)], 'old', False, 'near'),
    ([('near', 3, True, True), ('old', 2, True, False)], 'old', False, 'near'),
    ([('neutral', 1, False, True), ('threat', 5, True, True)], 'neutral', True, 'threat'),
    ([], 'missing', False, None),
    ([('bad', -1, True, True), ('ok', 2, True, True)], None, False, 'ok'),
    ([('nan', float('nan'), True, True), ('inf', float('inf'), True, True), ('ok', 2, True, True)], None, False, 'ok'),
]


def java_literal(value):
    if value is None:return 'null'
    if isinstance(value, str):return json.dumps(value)
    if isinstance(value, bool):return str(value).lower()
    if not math.isfinite(value):return 'Double.NaN' if math.isnan(value) else 'Double.POSITIVE_INFINITY'
    return str(float(value))


def harness():
    calls = []
    for n, (targets, current, defensive, _) in enumerate(CASES):
        candidates = ','.join('new Selector.Target('+','.join(java_literal(v) for v in target)+')' for target in targets)
        calls.append('System.out.println("CASE '+str(n)+' " + java.util.Objects.toString(Selector.select(java.util.List.of('+candidates+'),'+java_literal(current)+','+java_literal(defensive)+'),"<null>"));')
    return 'public class Probe { public static void main(String[] args) { '+'\n'.join(calls)+' } }'


SANDBOX = r'''import json, pathlib, subprocess, sys
data=json.load(sys.stdin)
pathlib.Path('Selector.java').write_text(data['source'],encoding='utf-8')
pathlib.Path('Probe.java').write_text(data['harness'],encoding='utf-8')
compile_result=subprocess.run(['javac','-J-Xmx192m','Selector.java','Probe.java'],capture_output=True,text=True,timeout=30)
if compile_result.returncode:
 print(json.dumps({'phase':'compile','returncode':compile_result.returncode,'stderr':compile_result.stderr}));sys.exit(0)
run=subprocess.run(['java','-Xmx128m','-XX:ActiveProcessorCount=1','Probe'],capture_output=True,text=True,timeout=15)
print(json.dumps({'phase':'run','returncode':run.returncode,'stdout':run.stdout,'stderr':run.stderr}))
'''


def verify_code(source, docker, image):
    name = 'minimax-probe-'+uuid.uuid4().hex
    command = [docker,'run','--rm','--pull','never','--name',name,'-i','--network','none','--read-only',
               '--user','1000:1000','--cap-drop','ALL','--security-opt','no-new-privileges',
               '--pids-limit','96','--memory','512m','--cpus','1','--tmpfs','/tmp:rw,nosuid,nodev,size=128m',
               '-w','/tmp','--entrypoint','python3',image,'-c',SANDBOX]
    try:
        process = subprocess.run(command, input=json.dumps({'source': source, 'harness': harness()}),
                                 capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if process.returncode:return {'status':'ERROR', 'reason':'Docker/helper failed', 'details':process.stderr}
        result = json.loads(process.stdout)
        if result['phase'] == 'compile':return {'status':'COMPILE_FAILED', **result}
        lines = result.get('stdout','').splitlines(); checks = []
        for n, (_, _, _, expected) in enumerate(CASES):
            prefix = 'CASE '+str(n)+' '; actual = [line[len(prefix):] for line in lines if line.startswith(prefix)]
            checks.append({'case':n,'passed': actual == [expected if expected is not None else '<null>'], 'actual':actual, 'expected':expected})
        return {'status':'CHECKED' if result['returncode']==0 else 'RUNTIME_FAILED',
                'passed':sum(c['passed'] for c in checks) if result['returncode']==0 else 0,
                'total':len(checks), 'cases':checks, **result}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'status':'ERROR','reason':type(exc).__name__+' in isolated code check'}
    finally:
        try:subprocess.run([docker,'rm','-f',name],capture_output=True,timeout=15)
        except (OSError,subprocess.TimeoutExpired):pass


def redact(value, key):
    if isinstance(value,str):return value.replace(key,'[REDACTED]')
    if isinstance(value,list):return [redact(item,key) for item in value]
    if isinstance(value,dict):return {name:redact(item,key) for name,item in value.items()}
    return value


def save_report(folder, report):
    temp = folder/'results.tmp'
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,folder/'results.json')
    entries = []; rows = []
    for row in report['results']:
        evaluation = row.get('evaluation',{});checks = evaluation.get('checks',{})
        summary = f"{row['role']} / {row['effort']} / repeat {row['repeat']} — API {row.get('api_seconds',row['elapsed_seconds']):.1f}s — {sum(bool(v) for v in checks.values())}/{len(checks)} mechanical checks"
        entries.append('<details><summary>'+html.escape(summary)+'</summary><pre>'+html.escape(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></details>')
        usage=row.get('response',{}).get('usage',{});code=evaluation.get('code_verification',{})
        code_summary=str(code.get('passed',0))+'/'+str(code.get('total',0)) if code.get('status')=='CHECKED' else code.get('status','—')
        cells=[row['role'],row['effort'],row['repeat'],row.get('api_seconds',row['elapsed_seconds']),usage.get('input_tokens','—'),
               usage.get('output_tokens','—'),usage.get('cache_read_input_tokens','—'),usage.get('cache_creation_input_tokens','—'),
               str(checks.get('native_submit_result','—')),str(checks.get('schema_valid','—')),code_summary,
               row.get('error') or row.get('response',{}).get('error') or row.get('response',{}).get('stop_reason','—')]
        rows.append('<tr>'+''.join('<td>'+html.escape(str(cell))+'</td>' for cell in cells)+'</tr>')
    table='<div style="overflow:auto"><table><tr>'+''.join('<th>'+name+'</th>' for name in ['Роль','Effort','Повтор','Секунды','Вход','Выход','Кеш: чтение','Кеш: запись','Tool call','Формат','Java cases','Завершение'])+'</tr>'+''.join(rows)+'</table></div>'
    page = '<!doctype html><html lang="ru"><meta charset="utf-8"><title>MiniMax role comparison</title><style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:0 20px;background:#111827;color:#e5e7eb}details{padding:16px;margin:12px 0;background:#1f2937;border-radius:10px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px monospace}h1{font-size:26px}td,th{padding:10px;text-align:left;border-bottom:1px solid #374151}table{border-collapse:collapse;font-size:14px}</style><h1>MiniMax: роли и reasoning</h1><p>Ответы модели и объективные проверки. Это малый тест, не гарантия качества полного мода. Смысл планов и замечаний нужно сравнить вручную. Запросы идут напрямую и не входят в счётчик Lab. Прочерк означает: провайдер не сообщил значение. Thinking не сохраняется; ответы и tool arguments сохраняются целиком.</p>'+table+''.join(entries)+'</html>'
    (folder/'results.html').write_text(page,encoding='utf-8')


def self_test():
    assert endpoint(BASE) == '/anthropic/v1/messages'
    for bad in ('http://api.minimax.io/anthropic','https://other.example/anthropic',BASE+'?key=test'):
        try:endpoint(bad)
        except ValueError:pass
        else:raise AssertionError('Unsafe endpoint accepted')
    assert shape_issues({'java_source': 'class Selector {}','explanation':'test','proposed_tests':[]}, SCHEMAS['coder']) == []
    assert shape_issues({'java_source': 42}, SCHEMAS['coder'])
    events = [{'type':'message_start','message':{'usage':{'input_tokens':8}}},
              {'type':'content_block_start','index':0,'content_block':{'type':'tool_use','name':'submit_result','input':{}}},
              {'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'{"java_source":"class Selector {}","explanation":"test","proposed_tests":[]}'}},
              {'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':20}},
              {'type':'message_stop'}]
    wire = ''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode()
    response = collect_events(io.BytesIO(wire),show=False)
    assert all(score('coder',response)['checks'].values())
    assert response['usage']['output_tokens'] == 20
    response['stream_complete'] = False
    assert not score('coder',response)['checks']['completed_response']
    assert 'CASE 13' in harness()
    print('Offline self-test: OK. No credentials read; no API requests or model code executed.')


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run',action='store_true',help='Make real API requests')
    mode.add_argument('--dry-run',action='store_true',help='Preview requests without keys/network (default)')
    mode.add_argument('--self-test',action='store_true',help='Run offline parser/format checks')
    parser.add_argument('--roles',nargs='+',choices=ROLES,default=list(ROLES))
    parser.add_argument('--efforts',nargs='+',choices=EFFORTS,default=list(EFFORTS))
    parser.add_argument('--repeats',type=int,default=1,help='Repeat each independent task; default 1')
    parser.add_argument('--max-tokens',type=int,default=16384,help='Includes thinking AND answer; identical for all efforts')
    parser.add_argument('--timeout',type=float,default=900,help='Request deadline in seconds')
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--verify-code',action='store_true',help='Compile/test returned Java in isolated Docker; no host mounts/network')
    parser.add_argument('--docker-image',default='codex-lab-worker:local',help='Existing local Java 21/Python3 image; never auto-pulled')
    parser.add_argument('--output-dir',type=Path,help='New directory for full responses and HTML/JSON; default timestamped in project root')
    args = parser.parse_args(argv)
    if args.self_test:self_test();return 0
    if args.repeats < 1 or args.max_tokens < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('repeats/max-tokens/timeout must be positive')
    roles = list(dict.fromkeys(args.roles));efforts = list(dict.fromkeys(args.efforts))
    count = len(roles)*len(efforts)*args.repeats
    print(f'Model: {MODEL}\nRoles: {", ".join(roles)}\nEfforts: {", ".join(efforts)}\nRequests: {count}; no automatic retries; max_tokens/request: {args.max_tokens}')
    print('Direct requests are outside the Lab token counter. API truncation/timeouts are separate from answer quality.')
    if not args.run:
        print('Preview only. Run with --run to send requests. --verify-code adds isolated Java checks.')
        for role in roles:print('\n'+role.upper()+' TASK:\n'+PROMPTS[role]+'\nOUTPUT SCHEMA:\n'+json.dumps(SCHEMAS[role],ensure_ascii=False,indent=2))
        return 0
    docker = None
    if args.verify_code and 'coder' in roles:
        docker = shutil.which('docker')
        if not docker:
            candidate = Path(os.environ.get('LOCALAPPDATA',''))/'Programs/DockerDesktop/resources/bin/docker.exe'
            if candidate.is_file():docker = str(candidate)
        if not docker:parser.error('Docker not found. Omit --verify-code to inspect code without executing it.')
        try:check = subprocess.run([docker,'image','inspect',args.docker_image],capture_output=True,timeout=20)
        except (OSError,subprocess.TimeoutExpired):parser.error('Docker unavailable; no API requests sent')
        if check.returncode:parser.error('Local Docker image unavailable; no API requests sent (no automatic pull)')
    config = env_config(args.env_file)
    key = config.get('MINIMAX_API_KEY','')
    if not key:parser.error('Set MINIMAX_API_KEY (Subscription Key) in .env or process environment; never pass it on the command line')
    path = endpoint(config.get('MINIMAX_BASE_URL') or BASE)
    folder = args.output_dir or ROOT/'minimax-probe-results'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ')
    folder.mkdir(parents=True,exist_ok=False)
    report = {'model':MODEL,'endpoint':BASE+'/v1/messages','created_at':datetime.now(timezone.utc).isoformat(),
              'max_tokens':args.max_tokens,'planned_requests':count,'prompts':PROMPTS,'schemas':SCHEMAS,
              'note':'Mechanical checks are not semantic certification. No Minecraft tests. Direct API usage outside Lab counters.', 'results':[]}
    save_report(folder,report); interrupted = False
    try:
        for repeat in range(1,args.repeats+1):
            for effort in efforts:
                for role in roles:
                    print(f'\n{"="*64}\n{role.upper()} | effort={effort} | repeat={repeat}\nWaiting for model response (deadline {args.timeout:g}s)...',flush=True)
                    started=time.monotonic(); response = {}; api_seconds = None; verification_seconds = None
                    try:
                        response=invoke(key,path,request_payload(role,effort,args.max_tokens),args.timeout)
                        api_seconds=round(time.monotonic()-started,3)
                        evaluation=score(role,response)
                        if role=='coder' and docker and evaluation['checks']['completed_response'] and evaluation['checks']['schema_valid']:
                            verification_started=time.monotonic()
                            evaluation['code_verification']=verify_code(evaluation['role_result']['java_source'],docker,args.docker_image)
                            verification_seconds=round(time.monotonic()-verification_started,3)
                        row={'role':role,'effort':effort,'repeat':repeat,'response':response,'evaluation':evaluation}
                    except (OSError,ValueError,http.client.HTTPException) as exc:
                        row={'role':role,'effort':effort,'repeat':repeat,'error':type(exc).__name__+'; request failed, no retry'}
                    row['elapsed_seconds']=round(time.monotonic()-started,3)
                    row['api_seconds']=api_seconds if api_seconds is not None else row['elapsed_seconds']
                    row['verification_seconds']=verification_seconds
                    # Never persist/print the key, even if an upstream echoes it.
                    row=redact(row,key)
                    response=row.get('response',response)
                    report['results'].append(row);save_report(folder,report)
                    value=row.get('evaluation',{}).get('role_result')
                    print('\nMODEL ANSWER:\n'+json.dumps(value if value is not None else row.get('response',{}),ensure_ascii=False,indent=2))
                    print('METRICS: '+json.dumps({'api_seconds':row['api_seconds'],'verification_seconds':verification_seconds,'stop_reason':response.get('stop_reason'),
                        'usage':response.get('usage',{}),'checks':row.get('evaluation',{}).get('checks'),
                        'code':row.get('evaluation',{}).get('code_verification'),'error':row.get('error') or response.get('error')},ensure_ascii=False))
                    if response.get('http_status') in (401,403,404,429):
                        print('Stopping matrix on access/endpoint/quota error. Saved results are retained.');interrupted=True;return 2
    except KeyboardInterrupt:
        interrupted=True;print('\nStopped by user. Completed responses retained.');return 130
    finally:
        report['interrupted']=interrupted;save_report(folder,report)
        print('\nJSON: '+str(folder/'results.json')+'\nHTML: '+str(folder/'results.html'))
    def successful(row):
        evaluation=row.get('evaluation',{});checks=evaluation.get('checks',{});code=evaluation.get('code_verification',{})
        return (bool(checks) and all(checks.values()) and
                (code.get('status') in (None,'NOT_EXECUTED') or
                 code.get('status')=='CHECKED' and code['passed']==code['total']))
    return 0 if all(successful(row) for row in report['results']) else 1


if __name__ == '__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError) as exc:
        print('Configuration/output error: '+type(exc).__name__+'. Check paths, MiniMax URL and env-file syntax.',file=sys.stderr)
        raise SystemExit(2)
