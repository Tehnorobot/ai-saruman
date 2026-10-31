"""Anthropic-compatible gateway with credential-redacted rejection diagnostics."""
import asyncio
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from provider_gateway.openai_client import OpenAIClient, safe_error
from provider_gateway.diagnostics import rejection
from common.provider_settings import MINIMAX_MODELS, MINIMAX_EFFORTS
from common.token_usage import metrics

MODELS = {"claude-sonnet-5-5": (200, 1000, 250, 400, 20),
          "claude-opus-5-5": (400, 2000, 500, 800, 20)}
LITELLM_MODELS = {
    'deepseek-ai/DeepSeek-V4-Flash-0731': ('default',),
    'Qwen/Qwen3-Next-80B-A3B-Instruct-FP8': ('default',),
    'google/gemma-4-26B-A4B-it': ('default',),
    'zai-org/GLM-5.3-Flash': ('default',),
    'Qwen/Qwen3.8-27B': ('default',),
}
ID = re.compile(r"^[a-f0-9]{14}$")
TASK = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
MAX_BODY = 8 * 1024 * 1024
FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, project_id TEXT NOT NULL,
                model TEXT NOT NULL, effort TEXT NOT NULL, token_budget INTEGER NOT NULL,
                max_output_tokens INTEGER NOT NULL, max_calls INTEGER NOT NULL,
                expires REAL NOT NULL, capability_hash TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, job_id TEXT NOT NULL,
                model TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL,
                status TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_rub REAL NOT NULL DEFAULT 0, billing_unknown INTEGER NOT NULL DEFAULT 1);
              CREATE INDEX IF NOT EXISTS requests_job ON requests(job_id, started_at);
              CREATE TABLE IF NOT EXISTS task_roles (task_id TEXT PRIMARY KEY, role TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS task_providers (task_id TEXT PRIMARY KEY, provider TEXT NOT NULL);
            """)
            columns={row[1] for row in conn.execute('PRAGMA table_info(requests)')}
            if 'diagnostic_json' not in columns:
                conn.execute('ALTER TABLE requests ADD COLUMN diagnostic_json TEXT')

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def task(self, capability):
        digest = hashlib.sha256(capability.encode()).hexdigest()
        with self.connect() as conn:
            row = conn.execute("SELECT t.*,COALESCE(p.provider,'dslab') AS provider FROM tasks t LEFT JOIN task_providers p USING(task_id) WHERE capability_hash=?", (digest,)).fetchone()
        return dict(row) if row else None

    def totals(self, task_id):
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens+output_tokens+cache_creation_input_tokens+cache_read_input_tokens),0) AS tokens, COALESCE(SUM(billing_unknown),0) AS unknown FROM requests WHERE task_id=?", (task_id,)).fetchone()
        return dict(row)

    def progress(self, rid, model, usage, status=None):
        values = [usage.get(field, 0) for field in FIELDS]
        rates = MODELS.get(model)
        # Cached creation has two separately priced lifetime buckets when reported.
        creation = values[2]
        cost = ((values[0] * rates[0] + values[1] * rates[1]
                + usage.get("cache_creation_1h", 0) * rates[3]
                + max(0, creation - usage.get("cache_creation_1h", 0)) * rates[2]
                + values[3] * rates[4]) / 1_000_000) if rates else 0
        with self.connect() as conn:
            conn.execute("UPDATE requests SET input_tokens=?,output_tokens=?,cache_creation_input_tokens=?,cache_read_input_tokens=?,estimated_rub=? WHERE request_id=?", (*values, cost, rid))
            if status:
                conn.execute("UPDATE requests SET status=?, finished_at=?, billing_unknown=? WHERE request_id=?", (status, time.time(), int(status != "completed"), rid))

    def job_usage(self, jid):
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT r.rowid AS sequence, r.*, COALESCE(t.role,'unknown') AS role FROM requests r LEFT JOIN task_roles t USING(task_id) WHERE r.job_id=? ORDER BY r.rowid DESC LIMIT 200", (jid,))]
            roles = [dict(row) for row in conn.execute("SELECT COALESCE(t.role,'unknown') AS role, COUNT(*) AS requests, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, SUM(cache_creation_input_tokens) AS cache_creation_input_tokens, SUM(cache_read_input_tokens) AS cache_read_input_tokens, SUM(estimated_rub) AS estimated_rub, SUM(billing_unknown) AS billing_unknown_requests FROM requests r LEFT JOIN task_roles t USING(task_id) WHERE r.job_id=? GROUP BY COALESCE(t.role,'unknown')", (jid,))]
            totals = dict(conn.execute("SELECT COUNT(*) AS requests, COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cache_creation_input_tokens),0) AS cache_creation_input_tokens, COALESCE(SUM(cache_read_input_tokens),0) AS cache_read_input_tokens, COALESCE(SUM(estimated_rub),0) AS estimated_rub, COALESCE(SUM(billing_unknown),0) AS billing_unknown_requests FROM requests WHERE job_id=?", (jid,)).fetchone())
            providers = {row['task_id']: row['provider'] for row in conn.execute(
                "SELECT task_id,provider FROM task_providers WHERE task_id IN (SELECT task_id FROM requests WHERE job_id=?)", (jid,))}
            unknown_roles = {row['role'] for row in conn.execute(
                "SELECT DISTINCT COALESCE(t.role,'unknown') AS role FROM requests r LEFT JOIN task_roles t USING(task_id) JOIN task_providers p USING(task_id) WHERE r.job_id=? AND p.provider!='dslab'", (jid,))}
            used_providers = {row['provider'] for row in conn.execute(
                "SELECT DISTINCT COALESCE(p.provider,'dslab') AS provider FROM requests r LEFT JOIN task_providers p USING(task_id) WHERE r.job_id=?", (jid,))}
        for row in rows:
            row['provider'] = providers.get(row['task_id'], 'dslab')
            row['cost_available'] = row['provider'] == 'dslab'
            if not row['cost_available']:row['estimated_rub'] = None
        for role in roles:
            role['cost_available'] = role['role'] not in unknown_roles
            if not role['cost_available']:role['estimated_rub'] = None
        totals['cost_available'] = not bool(unknown_roles)
        if not totals['cost_available']:totals['estimated_rub'] = None
        provider = next(iter(used_providers)) if len(used_providers)==1 else 'mixed' if used_providers else 'dslab'
        return {"provider": provider, "job_id": jid, "currency": "RUB", "cost_is_estimate": True,
                "cost_available":totals['cost_available'],
                "token_source": "provider_reported", "totals": totals, "requests": rows, "roles": roles}

    def job_requests(self, jid, limit=50, cursor=None):
        """Read insertion order; wall clocks and later status updates cannot reorder requests."""
        where = "r.job_id=?"
        values = [jid]
        if cursor is not None:
            where += " AND r.rowid<?"
            values.append(cursor)
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT r.rowid AS sequence, r.*, COALESCE(t.role,'unknown') AS role, "
                "COALESCE(p.provider,'dslab') AS provider FROM requests r "
                "LEFT JOIN task_roles t USING(task_id) LEFT JOIN task_providers p USING(task_id) "
                "WHERE " + where + " ORDER BY r.rowid DESC LIMIT ?", (*values, limit + 1))]
        more = len(rows) > limit
        rows = rows[:limit]
        for row in rows:
            row['cost_available'] = row['provider'] == 'dslab'
            if not row['cost_available']:
                row['estimated_rub'] = None
        return {'available': True, 'job_id': jid, 'requests': [metrics(row) for row in rows],
                'next_cursor': rows[-1]['sequence'] if more else None}


def merge_usage(usage, event):
    reported = event.get("usage") or event.get("message", {}).get("usage") or {}
    for field in FIELDS:
        value = reported.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[field] = max(usage.get(field, 0), value)
    creation = reported.get("cache_creation") or {}
    value = creation.get("ephemeral_1h_input_tokens")
    if isinstance(value, int) and value >= 0:
        usage["cache_creation_1h"] = max(usage.get("cache_creation_1h", 0), value)


class SSEUsage:
    def __init__(self):
        self.pending = b""
        self.usage = {}
        self.stopped = False

    def feed(self, chunk):
        self.pending += chunk
        if len(self.pending) > MAX_BODY:
            raise ValueError("Oversized provider event")
        self.pending = self.pending.replace(b"\r\n", b"\n")
        blocks = []
        while b"\n\n" in self.pending:
            block, self.pending = self.pending.split(b"\n\n", 1)
            data = b"\n".join(line[5:].lstrip() for line in block.split(b"\n") if line.startswith(b"data:"))
            if not data:
                blocks.append(block + b"\n\n")
                continue
            try:
                event = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "error":
                raise ValueError("Provider stream error")
            merge_usage(self.usage, event)
            self.stopped |= event.get("type") == "message_stop"
            blocks.append(block + b"\n\n")
        return blocks


def _text_blocks(value):
    if isinstance(value, str):return value
    if not isinstance(value, list):raise ValueError('Text content required')
    if any(not isinstance(block, dict) or block.get('type')!='text' or not isinstance(block.get('text'),str)
           for block in value):raise ValueError('Unsupported content block')
    return '\n'.join(block['text'] for block in value)


def minimax_request(data, task):
    """Native Anthropic tool history; no Claude thinking or cache controls."""
    value=copy.deepcopy(data)
    # Strip only protocol metadata, never user tool arguments or JSON schemas
    # whose ordinary field may happen to be named cache_control.
    groups=[value.get('system'),value.get('tools')]
    for message in value.get('messages',[]):
        if isinstance(message,dict):groups.append(message.get('content'))
    for group in groups:
        if isinstance(group,list):
            for block in group:
                if isinstance(block,dict):block.pop('cache_control',None)
    value.pop('thinking',None)
    value.pop('temperature',None)
    value.pop('top_p',None)
    value.pop('stop_sequences',None)
    value['output_config']={'effort':'max' if task['effort']=='default' else task['effort']}
    return value


def openai_request(data, task):
    """Translate native tool messages without extracting commands from prose."""
    messages = []
    if data.get('system'):messages.append({'role':'system','content':_text_blocks(data['system'])})
    for message in data['messages']:
        if not isinstance(message,dict) or message.get('role') not in ('user','assistant'):
            raise ValueError('Unsupported message role')
        role, content = message['role'], message.get('content')
        if isinstance(content,str):
            messages.append({'role':role,'content':content});continue
        if not isinstance(content,list):raise ValueError('Unsupported message content')
        if role=='assistant':
            result={'role':'assistant','content':''};texts=[];thinking=[];calls=[]
            for block in content:
                if not isinstance(block,dict):raise ValueError('Invalid message block')
                kind=block.get('type')
                if kind=='text':texts.append(_text_blocks([block]))
                elif kind=='thinking' and isinstance(block.get('thinking'),str):thinking.append(block['thinking'])
                elif kind=='tool_use':
                    if (not isinstance(block.get('id'),str) or not 1<=len(block['id'])<=160
                            or not isinstance(block.get('name'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',block['name'])
                            or not isinstance(block.get('input'),dict)):raise ValueError('Invalid tool call')
                    calls.append({'id':block['id'],'type':'function','function':{
                        'name':block['name'],'arguments':json.dumps(block['input'],ensure_ascii=False)}})
                else:raise ValueError('Unsupported assistant block')
            result['content']='\n'.join(texts) or None
            if thinking:result['reasoning_content']=''.join(thinking)
            if calls:result['tool_calls']=calls
            messages.append(result)
        else:
            texts=[]
            for block in content:
                if not isinstance(block,dict):raise ValueError('Invalid message block')
                if block.get('type')=='text':texts.append(_text_blocks([block]))
                elif block.get('type')=='tool_result':
                    if texts:messages.append({'role':'user','content':'\n'.join(texts)});texts=[]
                    call_id=block.get('tool_use_id')
                    if not isinstance(call_id,str) or not 1<=len(call_id)<=160:raise ValueError('Invalid tool result')
                    result=_text_blocks(block.get('content',''))
                    if block.get('is_error'):result='Tool error: '+result
                    messages.append({'role':'tool','tool_call_id':call_id,'content':result})
                else:raise ValueError('Unsupported user block')
            if texts:messages.append({'role':'user','content':'\n'.join(texts)})
    result={'model':task['model'],'messages':messages,'stream':True,
            'stream_options':{'include_usage':True},'max_tokens':data['max_tokens']}
    # LiteLLM uses provider defaults exclusively, including recovered tasks
    # registered before effort controls were removed. Never forward tuning.
    if data.get('tools'):
        if not isinstance(data['tools'],list) or len(data['tools'])>128:raise ValueError('Invalid tools')
        result['tools']=[]
        for tool in data['tools']:
            if (not isinstance(tool,dict) or not isinstance(tool.get('name'),str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',tool['name'])
                    or not isinstance(tool.get('input_schema'),dict)):raise ValueError('Invalid tool definition')
            result['tools'].append({'type':'function','function':{'name':tool['name'],
                'description':str(tool.get('description','')),'parameters':tool['input_schema']}})
    choice=data.get('tool_choice')
    if choice:
        if not isinstance(choice,dict):raise ValueError('Invalid tool choice')
        kind=choice.get('type')
        if kind in ('auto','none'):result['tool_choice']=kind
        elif kind=='any':result['tool_choice']='required'
        elif kind=='tool' and isinstance(choice.get('name'),str):
            result['tool_choice']={'type':'function','function':{'name':choice['name']}}
        else:raise ValueError('Invalid tool choice')
    for field in ('temperature','top_p'):
        if field in data:result[field]=data[field]
    if 'stop_sequences' in data:result['stop']=data['stop_sequences']
    return result


def _sse(event):
    return ('event: '+event['type']+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode('utf-8')


class OpenAIStream:
    """Bounded OpenAI SSE -> native Anthropic text/thinking/tool-use events."""
    def __init__(self,model):
        self.model=model;self.pending=b'';self.usage={};self.stopped=False
        self.started=False;self.finished=None;self.blocks={};self.tools={}
        self.next_index=0;self.output_bytes=0;self.visible=False

    def _block(self,kind):
        if kind in self.blocks:return self.blocks[kind],[]
        index=self.next_index;self.next_index+=1;self.blocks[kind]=index
        block={'type':kind, 'thinking' if kind=='thinking' else 'text':''}
        return index,[_sse({'type':'content_block_start','index':index,'content_block':block})]

    def _usage(self,reported):
        if not isinstance(reported,dict):raise ValueError('Invalid provider usage')
        def count(value):
            if type(value) is not int or not 0<=value<=10**12:raise ValueError('Invalid token count')
            return value
        if 'prompt_tokens' in reported:
            prompt=count(reported['prompt_tokens'])
            details=reported.get('prompt_tokens_details') or {}
            if not isinstance(details,dict):raise ValueError('Invalid prompt details')
            cached=count(details.get('cached_tokens',0))
            created=count(details.get('cache_write_tokens',0))
            if cached+created>prompt:raise ValueError('Invalid cached token count')
            self.usage.update(input_tokens=prompt-cached-created,cache_read_input_tokens=cached,
                              cache_creation_input_tokens=created)
        if 'completion_tokens' in reported:self.usage['output_tokens']=count(reported['completion_tokens'])

    def _event(self,event):
        if not isinstance(event,dict) or event.get('error'):raise ValueError('Provider stream error')
        output=[]
        if not self.started:
            self.started=True
            output.append(_sse({'type':'message_start','message':{'id':'msg_'+secrets.token_hex(12),
                'type':'message','role':'assistant','model':self.model,'content':[],'usage':{}}}))
        if event.get('usage') is not None:
            self._usage(event['usage'])
            output.append(_sse({'type':'message_delta','delta':{},'usage':dict(self.usage)}))
        choices=event.get('choices',[])
        if not isinstance(choices,list) or len(choices)>1:raise ValueError('Exactly one completion supported')
        for choice in choices:
            if not isinstance(choice,dict) or choice.get('index',0)!=0:raise ValueError('Invalid completion choice')
            delta=choice.get('delta') or {}
            if not isinstance(delta,dict):raise ValueError('Invalid completion delta')
            if delta.get('refusal') or delta.get('function_call'):raise ValueError('Unsupported provider completion')
            if self.finished and any(delta.get(k) for k in ('content','reasoning','reasoning_content','tool_calls')):
                raise ValueError('Content after completion finish')
            for field,kind in (('reasoning_content','thinking'),('reasoning','thinking'),('content','text')):
                text=delta.get(field)
                if text is None or text=='':continue
                if not isinstance(text,str):raise ValueError('Unsupported completion content')
                self.output_bytes+=len(text.encode('utf-8'))
                index,start=self._block(kind);output.extend(start)
                key='thinking' if kind=='thinking' else 'text'
                output.append(_sse({'type':'content_block_delta','index':index,
                    'delta':{'type':key+'_delta',key:text}}))
                if kind=='text':self.visible=True
            calls=delta.get('tool_calls') or []
            if not isinstance(calls,list):raise ValueError('Invalid tool calls')
            for call in calls:
                if not isinstance(call,dict) or type(call.get('index')) is not int or not 0<=call['index']<64:
                    raise ValueError('Invalid tool call index')
                item=self.tools.setdefault(call['index'],{'id':'','name':'','arguments':''})
                if call.get('type','function')!='function':raise ValueError('Unsupported tool type')
                function=call.get('function') or {}
                if not isinstance(function,dict):raise ValueError('Invalid tool function')
                for key,value in (('id',call.get('id')),('name',function.get('name')),('arguments',function.get('arguments'))):
                    if value is not None:
                        if not isinstance(value,str):raise ValueError('Invalid tool delta')
                        self.output_bytes+=len(value.encode('utf-8'));item[key]+=value
                if len(item['id'])>160 or len(item['name'])>128:raise ValueError('Oversized tool identity')
            finish=choice.get('finish_reason')
            if finish is not None:
                if finish not in ('stop','tool_calls','length','content_filter'):raise ValueError('Unknown completion finish')
                self.finished=finish
        if self.output_bytes>MAX_BODY:raise ValueError('Oversized completion')
        return output

    def _done(self):
        if self.stopped or not self.finished:raise ValueError('Provider stream incomplete')
        if self.finished=='content_filter':raise ValueError('Provider content filtered')
        if bool(self.tools)!=(self.finished=='tool_calls'):raise ValueError('Incomplete native tool calls')
        output=[_sse({'type':'content_block_stop','index':index}) for index in self.blocks.values()]
        for item in self.tools.values():
            if (not item['id'] or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',item['name'])
                    or not isinstance(json.loads(item['arguments']),dict)):raise ValueError('Invalid native tool call')
            index=self.next_index;self.next_index+=1
            output.extend((_sse({'type':'content_block_start','index':index,'content_block':{
                'type':'tool_use','id':item['id'],'name':item['name'],'input':{}}}),
                _sse({'type':'content_block_delta','index':index,'delta':{
                    'type':'input_json_delta','partial_json':item['arguments']}}),
                _sse({'type':'content_block_stop','index':index})))
        if not self.visible and not self.tools:raise ValueError('No final assistant content')
        reason={'stop':'end_turn','tool_calls':'tool_use','length':'max_tokens'}[self.finished]
        output.extend((_sse({'type':'message_delta','delta':{'stop_reason':reason},'usage':dict(self.usage)}),
                       _sse({'type':'message_stop'})))
        self.stopped=True
        return output

    def feed(self,chunk):
        self.pending+=chunk
        if len(self.pending)>MAX_BODY:raise ValueError('Oversized provider event')
        self.pending=self.pending.replace(b'\r\n',b'\n');output=[]
        while b'\n\n' in self.pending:
            block,self.pending=self.pending.split(b'\n\n',1)
            data=b'\n'.join(line[5:].lstrip() for line in block.split(b'\n') if line.startswith(b'data:'))
            if not data:continue
            if data.strip()==b'[DONE]':output.extend(self._done())
            else:
                if self.stopped:raise ValueError('Content after stream end')
                output.extend(self._event(json.loads(data)))
        return output


def create_app(*, store=None, transport=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    usage_store = store
    locks = {}

    def database():
        nonlocal usage_store
        if usage_store is None:
            usage_store = Store(os.environ.get("DSLAB_USAGE_DB", "/usage/usage.sqlite3"))
        return usage_store

    def control(request):
        expected = os.environ.get("DSLAB_CONTROL_TOKEN", "")
        actual = request.headers.get("authorization", "")
        if len(expected) < 32 or not hmac.compare_digest(actual.encode(), ("Bearer " + expected).encode()):
            raise HTTPException(401, "Control authentication required")

    def endpoint(provider='dslab'):
        if provider=='minimax':
            base=os.environ.get('MINIMAX_BASE_URL','https://api.minimax.io/anthropic').rstrip('/')
            try:
                parts=urlsplit(base)
                valid=(parts.scheme=='https' and parts.hostname=='api.minimax.io'
                    and parts.port in (None,443) and not parts.username and not parts.password
                    and not parts.query and not parts.fragment and parts.path in ('/anthropic','/anthropic/v1'))
            except ValueError:valid=False
            if not valid:raise HTTPException(503,'Invalid MiniMax endpoint configuration')
            return base+('/messages' if parts.path.endswith('/v1') else '/v1/messages')
        if provider=='litellm':
            base=os.environ.get('LITELLM_BASE_URL','').rstrip('/')
            try:
                parts=urlsplit(base)
                # Explicit user-authorized campus proxy. Other HTTP hosts and
                # ports remain prohibited; request payloads cannot select URLs.
                transport_ok=((parts.scheme=='https' and parts.hostname and parts.port in (None,443))
                    or (parts.scheme=='http' and parts.hostname=='lnsigo.mipt.ru' and parts.port==4000))
                valid=(transport_ok
                    and not parts.username and not parts.password and not parts.query and not parts.fragment
                    and parts.path in ('','/v1','/v1/chat/completions'))
            except ValueError:valid=False
            if not valid:raise HTTPException(503,'Invalid LiteLLM endpoint configuration')
            if parts.path=='/v1/chat/completions':return base
            return base+('/chat/completions' if parts.path=='/v1' else '/v1/chat/completions')
        base = os.environ.get("DSLAB_BASE_URL", "https://api.dslab.tech").rstrip("/")
        parts = urlsplit(base)
        if parts.scheme != "https" or parts.hostname != "api.dslab.tech" or parts.port not in (None, 443) or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/v1"):
            raise HTTPException(503, "Invalid provider endpoint configuration")
        return base + ("/messages" if parts.path == "/v1" else "/v1/messages")

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BODY:
                raise HTTPException(413, "Request too large")
        try:
            value = json.loads(data)
        except ValueError:
            raise HTTPException(400, "Invalid JSON")
        if not isinstance(value, dict):
            raise HTTPException(400, "JSON object required")
        return value, len(data)

    @app.get("/health")
    def health():
        ready = (bool(os.environ.get("DSLAB_API_KEY")) or
                 bool(os.environ.get('MINIMAX_API_KEY')) or
                 bool(os.environ.get('LITELLM_API_KEY') and os.environ.get('LITELLM_BASE_URL'))) and len(os.environ.get("DSLAB_CONTROL_TOKEN", "")) >= 32
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    @app.post("/internal/tasks")
    async def register(request: Request):
        control(request)
        data, _ = await body(request)
        try:
            tid, jid, pid = (str(data[key]) for key in ("task_id", "job_id", "project_id"))
            model = str(data["model"])
            provider=data.get('provider','dslab')
            if provider not in ('dslab','litellm','minimax'):raise ValueError()
            effort = str(data.get('effort', 'default'))
            if provider=='dslab' and effort=='default':
                effort='high' if model=='claude-sonnet-5-5' else 'medium'
            budget = int(data.get("token_budget", 0))
            output = int(data.get("max_output_tokens", 16384))
            calls = int(data.get("max_calls", 200))
            if any(value>2**53-1 for value in (budget,output,calls)):raise ValueError()
            ttl = int(data.get("ttl_seconds", 86400))
            role = data.get('role','unknown')
            if role not in ('planner','coder','reviewer','unknown'):raise ValueError()
            if provider=='minimax':valid_model=model in MINIMAX_MODELS and effort in MINIMAX_EFFORTS
            else:valid_model=(model in MODELS and effort in ('low','medium','high','xhigh','max')) if provider=='dslab' else (model in LITELLM_MODELS and effort in LITELLM_MODELS[model])
            if not (TASK.fullmatch(tid) and ID.fullmatch(jid) and ID.fullmatch(pid) and valid_model and budget >= 0 and output >= 1 and calls >= 1 and 60 <= ttl <= 7 * 86400):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Invalid task limits or identity")
        # Deterministic keyed capability supports controller crash recovery without
        # storing reusable bearer credentials or resetting the accumulated budget.
        cap = hmac.new(os.environ["DSLAB_CONTROL_TOKEN"].encode(), ("task:" + tid).encode(), hashlib.sha256).hexdigest()
        with database().connect() as conn:
            stored_role=conn.execute('SELECT role FROM task_roles WHERE task_id=?',(tid,)).fetchone()
            if stored_role and stored_role['role']!=role:
                raise HTTPException(409,'Task role cannot change')
            stored_provider=conn.execute('SELECT provider FROM task_providers WHERE task_id=?',(tid,)).fetchone()
            if stored_provider and stored_provider['provider']!=provider:
                raise HTTPException(409,'Task provider cannot change')
            try:
                conn.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)", (tid, jid, pid, model, effort, budget, output, calls, time.time() + ttl, hashlib.sha256(cap.encode()).hexdigest()))
            except sqlite3.IntegrityError:
                existing = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
                expected = (jid, pid, model, effort, budget, output, calls)
                actual = tuple(existing[key] for key in ("job_id", "project_id", "model", "effort", "token_budget", "max_output_tokens", "max_calls"))
                if actual != expected or existing["expires"] <= time.time() or existing["capability_hash"] != hashlib.sha256(cap.encode()).hexdigest():
                    raise HTTPException(409, "Task already registered with incompatible or expired authorization")
            conn.execute('INSERT OR IGNORE INTO task_roles VALUES(?,?)',(tid,role))
            conn.execute('INSERT OR IGNORE INTO task_providers VALUES(?,?)',(tid,provider))
        return {"capability": cap, "task_id": tid}

    @app.post("/internal/tasks/{task_id}/renew")
    async def renew(task_id: str, request: Request):
        """Extend a live controller-owned lease without resetting its budget.

        Revocation is terminal: even a racing renewal cannot resurrect a task.
        A short renewable lease replaces coupling authorization to an agent's
        soft analysis deadline, while abandoned capabilities still expire.
        """
        control(request)
        data, _ = await body(request)
        ttl = data.get('ttl_seconds', 3600)
        jid, pid = data.get('job_id'), data.get('project_id')
        if (not TASK.fullmatch(task_id) or not isinstance(jid, str) or not ID.fullmatch(jid)
                or not isinstance(pid, str) or not ID.fullmatch(pid)
                or type(ttl) is not int or not 60 <= ttl <= 7 * 86400):
            raise HTTPException(400, 'Invalid task lease or identity')
        with database().connect() as conn:
            # Taking the write lock before inspecting the row serializes this
            # decision with revoke. Expiry cannot change between read/update.
            conn.execute('BEGIN IMMEDIATE')
            now = time.time()
            task = conn.execute('SELECT job_id,project_id,expires FROM tasks WHERE task_id=?',
                                (task_id,)).fetchone()
            if not task:
                raise HTTPException(404, 'Task not found')
            if (task['job_id'], task['project_id']) != (jid, pid):
                raise HTTPException(409, 'Task identity cannot change')
            if task['expires'] <= now:
                raise HTTPException(409, 'Task authorization revoked or expired')
            expires = max(task['expires'], now + ttl)
            conn.execute('UPDATE tasks SET expires=? WHERE task_id=? AND expires>?',
                         (expires, task_id, now))
        return {'task_id': task_id, 'expires': expires}

    @app.delete("/internal/tasks/{task_id}")
    def revoke(task_id: str, request: Request):
        control(request)
        with database().connect() as conn:
            conn.execute("UPDATE tasks SET expires=0 WHERE task_id=?", (task_id,))
        return {"revoked": True}

    @app.post("/internal/tasks/{task_id}/revoke")
    def revoke_post(task_id: str, request: Request):
        return revoke(task_id, request)

    @app.get("/internal/jobs/{job_id}/usage")
    def usage(job_id: str, request: Request):
        control(request)
        if not ID.fullmatch(job_id):
            raise HTTPException(400, "Invalid job identity")
        return database().job_usage(job_id)

    @app.get("/internal/jobs/{job_id}/requests")
    def requests(job_id: str, request: Request, limit: str = '50', cursor: str | None = None):
        control(request)
        if not ID.fullmatch(job_id):
            raise HTTPException(400, "Invalid job identity")
        if (not re.fullmatch(r'[0-9]{1,3}', limit) or not 1 <= int(limit) <= 200
                or (cursor is not None and (not re.fullmatch(r'[0-9]{1,19}', cursor)
                    or not 1 <= int(cursor) <= 2**63 - 1))):
            raise HTTPException(400, "Invalid request page limit or cursor")
        return database().job_requests(job_id, int(limit), int(cursor) if cursor is not None else None)

    @app.post("/v1/messages")
    async def messages(request: Request):
        auth = request.headers.get("authorization", "")
        task = database().task(auth[7:]) if auth.startswith("Bearer ") else None
        if not task or task["expires"] <= time.time():
            raise HTTPException(401, "Task authentication expired or invalid")
        data, body_size = await body(request)
        if data.get("model") != task["model"] or data.get("stream") is not True or not isinstance(data.get("messages"), list):
            raise HTTPException(400, "Streaming messages and registered model required")
        allowed = {"model", "messages", "system", "tools", "tool_choice", "max_tokens", "stream", "thinking", "output_config", "temperature", "top_p", "stop_sequences", "metadata"}
        if set(data) - allowed:
            raise HTTPException(400, "Unsupported request fields")
        requested = data.get("max_tokens", task["max_output_tokens"])
        if not isinstance(requested, int) or not 1 <= requested <= task["max_output_tokens"]:
            raise HTTPException(400, "Output token limit exceeded")
        data["max_tokens"] = requested
        output_config = data.get("output_config", {})
        if not isinstance(output_config, dict):
            raise HTTPException(400, "Invalid output configuration")
        data["output_config"] = {**output_config, "effort": task["effort"]}
        provider=task.get('provider','dslab')
        key = os.environ.get({'litellm':'LITELLM_API_KEY','minimax':'MINIMAX_API_KEY'}.get(provider,'DSLAB_API_KEY'), "")
        if not key:
            raise HTTPException(503, "Provider API key is not configured")
        upstream_url = endpoint(provider)
        try:
            upstream_data=(openai_request(data,task) if provider=='litellm' else
                           minimax_request(data,task) if provider=='minimax' else data)
        except (ValueError,TypeError,KeyError):
            raise HTTPException(400,'Unsupported native chat/tool payload') from None
        if provider=='litellm':
            body_size=max(body_size,len(json.dumps(upstream_data).encode('utf-8')))
            if body_size>MAX_BODY:raise HTTPException(413,'Converted request too large')
        lock = locks.setdefault(task["task_id"], asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "Task request already running")
        await lock.acquire()
        client = None
        response = None
        rid = secrets.token_hex(16)
        try:
            totals = database().totals(task["task_id"])
            if totals["calls"] >= task["max_calls"]:
                raise HTTPException(429, "Task request budget exhausted")
            if task["token_budget"] and totals["unknown"]:
                raise HTTPException(429, "Task billing is incomplete; budget cannot be safely enforced")
            # An upper bound on text tokenization, including message framing. Image/document
            # blocks are prohibited below, since their token counts do not follow byte size.
            if task["token_budget"] and totals["tokens"] + body_size + 4096 + requested > task["token_budget"]:
                raise HTTPException(429, "Insufficient remaining task token budget")
            def text_only(value):
                if isinstance(value, dict):
                    if value.get("type") in ("image", "document"):
                        return False
                    return all(text_only(v) for v in value.values())
                if isinstance(value, list):
                    return all(text_only(v) for v in value)
                return True
            if not text_only(data):
                raise HTTPException(400, "Image and document input is not enabled")
            with database().connect() as conn:
                conn.execute("INSERT INTO requests(request_id,task_id,job_id,model,started_at,status) VALUES(?,?,?,?,?,?)", (rid, task["task_id"], task["job_id"], task["model"], time.time(), "connecting"))
            if provider=='litellm':
                client=OpenAIClient(key,upstream_url,transport=transport)
                response=await client.send(upstream_data)
            else:
                client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600, connect=30), follow_redirects=False)
                credentials={'authorization':'Bearer '+key} if provider=='minimax' else {'x-api-key':key}
                upstream = client.build_request("POST", upstream_url, json=upstream_data,
                    headers={**credentials, "anthropic-version": "2023-06-01", "accept": "text/event-stream"})
                response = await client.send(upstream, stream=True)
            if response.status_code != 200:
                database().progress(rid, task["model"], {}, "upstream_rejected")
                detail=await rejection(response,upstream_data,provider=provider,request_id=rid,
                    secrets=(key,auth[7:],os.getenv('DSLAB_CONTROL_TOKEN','')))
                with database().connect() as conn:
                    conn.execute('UPDATE requests SET diagnostic_json=? WHERE request_id=?',
                                 (json.dumps(detail,ensure_ascii=False),rid))
                raise HTTPException(502, detail)
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise HTTPException(502, "Provider returned an invalid stream")
        except BaseException as exc:
            # The OpenAI SDK raises before returning a response on HTTP errors.
            # Preserve the same sanitized diagnostics as the native transports.
            sdk_response=getattr(exc,'response',None) if provider=='litellm' else None
            if isinstance(sdk_response,httpx.Response):
                detail=await rejection(sdk_response,upstream_data,provider=provider,request_id=rid,
                    secrets=(key,auth[7:],os.getenv('DSLAB_CONTROL_TOKEN','')))
                with database().connect() as conn:
                    conn.execute('UPDATE requests SET diagnostic_json=?,status=?,finished_at=? WHERE request_id=?',
                        (json.dumps(detail,ensure_ascii=False),'upstream_rejected',time.time(),rid))
                exc=HTTPException(502,detail)
            if response is not None:
                await response.aclose()
            if client is not None:
                await client.aclose()
            lock.release()
            # Do not include exception text: transport errors can contain credentials.
            sdk_error=safe_error(exc) if provider=='litellm' and not isinstance(exc,HTTPException) else None
            with database().connect() as conn:
                conn.execute("UPDATE requests SET status=CASE WHEN status='connecting' THEN 'connection_failed' ELSE status END,finished_at=? WHERE request_id=?", (time.time(), rid))
                if sdk_error:
                    conn.execute('UPDATE requests SET status=? WHERE request_id=?',(sdk_error[0],rid))
            if sdk_error:
                raise HTTPException(502,sdk_error[1]) from None
            if isinstance(exc, httpx.HTTPError):
                raise HTTPException(502, "Provider connection failed") from None
            if isinstance(exc,HTTPException):raise exc
            raise

        async def stream():
            parser = OpenAIStream(task['model']) if provider=='litellm' else SSEUsage()
            status = "interrupted"
            try:
                with database().connect() as conn:
                    conn.execute("UPDATE requests SET status='streaming' WHERE request_id=?", (rid,))
                events=response.aiter_events() if provider=='litellm' else response.aiter_bytes()
                async for chunk in events:
                    live_task = database().task(auth[7:])
                    if not live_task or live_task["expires"] <= time.time():
                        raise ValueError("Task authorization revoked or expired")
                    blocks = parser._event(chunk) if provider=='litellm' else parser.feed(chunk)
                    database().progress(rid, task["model"], parser.usage)
                    for block in blocks:
                        yield block
                if provider=='litellm' and response.complete:
                    for block in parser._done():yield block
                if parser.stopped and "input_tokens" in parser.usage and "output_tokens" in parser.usage:
                    status = "completed"
                else:
                    yield b'event: error\ndata: {"type":"error","error":{"type":"gateway_error","message":"Provider stream incomplete"}}\n\n'
            except asyncio.CancelledError:
                raise
            except Exception:
                yield b'event: error\ndata: {"type":"error","error":{"type":"gateway_error","message":"Provider stream interrupted"}}\n\n'
            finally:
                database().progress(rid, task["model"], parser.usage, status)
                await response.aclose()
                await client.aclose()
                lock.release()
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    return app


app = create_app()
