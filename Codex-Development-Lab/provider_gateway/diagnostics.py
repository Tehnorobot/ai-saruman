"""Public rejection diagnostics: no credentials, prompts or tool result contents."""
import json
import re


def sanitize(value, secrets=()):
    if isinstance(value,dict):
        return {sanitize(k,secrets):('[REDACTED]' if re.search(r'authorization|api.?key|secret|password|cookie|capability|access.?token|refresh.?token',k,re.I)
                   else sanitize(v,secrets)) for k,v in value.items()}
    if isinstance(value,list):return [sanitize(v,secrets) for v in value]
    if isinstance(value,str):
        value=value.encode('utf-8',errors='replace').decode('utf-8')
        for secret in secrets:
            if secret:value=value.replace(secret,'[REDACTED]')
        value=re.sub(r'(?i)\bBearer\s+[^\s"<>]+','Bearer [REDACTED]',value)
        value=re.sub(r'\bsk-[A-Za-z0-9_-]+','[REDACTED]',value)
        value=re.sub(r'(?i)((?:api[-_ ]?key|authorization|password|client_secret|access_token|refresh_token|capability)\s*[=:]\s*)[^\s,;"<>]+',r'\1[REDACTED]',value)
    return value


def request_summary(data):
    messages=[]
    for row in data.get('messages',[]):
        content=row.get('content',[])
        blocks=content if isinstance(content,list) else [{'type':'text','text':content}]
        messages.append({'role':row.get('role'),'blocks':[
            {k:b[k] for k in ('type','id','name','tool_use_id','is_error') if k in b}
            | {'serialized_bytes':len(json.dumps(b,ensure_ascii=False).encode())}
            for b in blocks if isinstance(b,dict)]})
    return {'model':data.get('model'),'max_tokens':data.get('max_tokens'),
        'output_config':data.get('output_config'),'tool_choice':data.get('tool_choice'),
        'request_bytes':len(json.dumps(data,ensure_ascii=False).encode()),
        'system_bytes':len(json.dumps(data.get('system'),ensure_ascii=False).encode()),
        'tools':[{'name':t.get('name'),'schema_bytes':len(json.dumps(t.get('input_schema',{})).encode())}
                 for t in data.get('tools',[])], 'messages':messages}


async def rejection(response,data,*,provider,request_id,secrets=()):
    # Bound untrusted error responses separately from model context. Disclose
    # clipping rather than presenting a partial response as complete.
    raw=bytearray();limit=8*1024*1024;truncated=False
    async for chunk in response.aiter_bytes():
        remaining=limit-len(raw)
        raw.extend(chunk[:remaining])
        if len(chunk)>remaining:truncated=True;break
    text=bytes(raw).decode('utf-8',errors='replace')
    try:body=json.loads(text)
    except (ValueError,RecursionError):body=text
    try:body=sanitize(body,secrets)
    except RecursionError:body=sanitize(text,secrets)
    headers={k:v for k,v in response.headers.items()
             if k.lower() in ('request-id','x-request-id','x-correlation-id','trace-id','retry-after','content-type')}
    diagnostic={'provider':provider,'gateway_request_id':request_id,'upstream_status':response.status_code,
        'response_headers':headers,'response_body':body,'body_bytes_captured':len(raw),
        'body_truncated':truncated,'request':request_summary(data)}
    return {'message':'Provider rejected request (HTTP %d)'%response.status_code,
            'diagnostic':sanitize(diagnostic,secrets)}
