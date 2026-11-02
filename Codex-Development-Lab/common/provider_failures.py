"""Typed public execution failures. Diagnostic prose never decides retry policy."""
import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

CODES={'provider_unavailable','stream_interrupted','provider_generation_error','authentication',
       'rate_limit','budget_exhausted','configuration','invalid_request','output_limit','request_limit','protocol_error'}
RETRY={'provider_unavailable':'provider_unavailable','stream_interrupted':'stream_interrupted',
       'provider_generation_error':'provider_generation_error','rate_limit':'rate_limit'}


class ProviderFailure(RuntimeError):
    def __init__(self,message,code,http_status=None,upstream_status=None,*,retry_after_seconds=None):
        if code not in CODES:raise ValueError('Unknown provider failure code')
        super().__init__(message)
        self.code,self.http_status,self.upstream_status=code,http_status,upstream_status
        self.retry_after_seconds=retry_after_seconds

    def metadata(self):
        value={'message':str(self)[:500],'code':self.code,'http_status':self.http_status,'upstream_status':self.upstream_status}
        if self.retry_after_seconds is not None:value['retry_after_seconds']=self.retry_after_seconds
        return value


def parse_retry_after(value):
    """Interpret only a provider Retry-After header, never diagnostic prose."""
    if not isinstance(value,str):return None
    try:
        if value.strip().isdigit():return int(value.strip())
        instant=parsedate_to_datetime(value)
        if instant.tzinfo is None:instant=instant.replace(tzinfo=timezone.utc)
        return max(0,math.ceil((instant-datetime.now(timezone.utc)).total_seconds()))
    except (ValueError,TypeError,OverflowError):return None


def status_code(status):
    if status in (500,502,503,504):return 'provider_unavailable'
    if status in (401,403):return 'authentication'
    if status==429:return 'rate_limit'
    return 'invalid_request'


def from_http(status,detail):
    upstream=None;retry_after=None
    if isinstance(detail,dict):
        diagnostic=detail.get('diagnostic',{})
        if isinstance(diagnostic,dict) and type(diagnostic.get('upstream_status')) is int:
            upstream=diagnostic['upstream_status']
            headers=diagnostic.get('response_headers',{})
            if isinstance(headers,dict):
                retry_after=next((parse_retry_after(v) for k,v in headers.items()
                    if isinstance(k,str) and k.lower()=='retry-after'),None)
    local={
        'Provider API key is not configured':'configuration',
        'Invalid LiteLLM endpoint configuration':'configuration',
        'Task request budget exhausted':'budget_exhausted',
        'Task billing is incomplete; budget cannot be safely enforced':'budget_exhausted',
        'Insufficient remaining task token budget':'budget_exhausted',
        'Output token limit exceeded':'output_limit'}
    code=status_code(upstream if upstream is not None else status)
    if upstream is None and isinstance(detail,str):code=local.get(detail,code)
    rendered=json.dumps(detail,ensure_ascii=False) if isinstance(detail,dict) else detail if isinstance(detail,str) else ''
    return ProviderFailure('API request stopped (HTTP %d)'%status+(': '+rendered if rendered else ''),code,status,upstream,
        retry_after_seconds=retry_after)


def retry_delay(result,failures,*,quota_interval=900):
    """Bound retry frequency, never the number of attempts or total waiting time."""
    if error_code(result)!='rate_limit':return min(60,2**min(failures,6))
    delay=min(max(1,quota_interval),60*2**min(max(0,failures-1),6))
    suggested=terminal(result).get('retry_after_seconds')
    if (type(suggested) is int or type(suggested) is float and math.isfinite(suggested)) and suggested>=0:
        delay=max(delay,math.ceil(suggested))
    return delay


def terminal(result):
    """Only a supervisor error or a top-level terminal event is authoritative."""
    error=result.get('error')
    if isinstance(error,dict):return error
    if isinstance(error,str) and error:return {'message':error}
    tail=result.get('tail','')
    if not isinstance(tail,str):return {}
    transcript=False
    for line in reversed(tail.splitlines()):
        try:event=json.loads(line)
        except (ValueError,TypeError):continue
        if not isinstance(event,dict) or not isinstance(event.get('type'),str):continue
        transcript=True
        if event['type'] in ('turn.failed','error'):
            error=event.get('error')
            if isinstance(error,dict):return error
            return {k:v for k,v in {**event,'message':error if isinstance(error,str) else event.get('message','')}.items()
                    if k in ('message','code','type','http_status','upstream_status')}
    return {} if transcript or len(tail.splitlines())>1 else {'message':tail}


def legacy_code(message):
    """Compatibility with exact historical adapter messages, never body keywords."""
    if not isinstance(message,str):return None
    m=re.match(r'^API request stopped \(HTTP (\d{3})\)(?:: (.*))?$',message,re.S)
    if m:
        status=int(m.group(1));detail=m.group(2) or ''
        if status==502:
            try:structured=json.loads(detail)
            except ValueError:structured=None
            if isinstance(structured,dict):
                diagnostic=structured.get('diagnostic',{})
                if isinstance(diagnostic,dict) and type(diagnostic.get('upstream_status')) is int:
                    return status_code(diagnostic['upstream_status'])
                trusted=structured.get('message','')
                upstream=re.fullmatch(r'Provider rejected request \(HTTP (\d{3})\)',trusted) if isinstance(trusted,str) else None
            elif detail.startswith('{'):
                # Historical terminal messages were clipped mid-JSON. Only the
                # first gateway-owned message field can recover its status.
                upstream=re.match(r'^\{\s*"message"\s*:\s*"Provider rejected request \(HTTP (\d{3})\)"',detail)
            else:upstream=re.fullmatch(r'Provider rejected request \(HTTP (\d{3})\)',detail)
            if upstream:return status_code(int(upstream.group(1)))
        return from_http(status,detail).code
    if message=='API turn incomplete (stop reason: error)':return 'provider_generation_error'
    if message.startswith(('API stream interrupted;', 'API stream ended without completion',
                           'API stream contained invalid JSON event')):return 'stream_interrupted'
    if message.startswith(('API turn incomplete: output limit reached','API turn incomplete (stop reason: max_tokens')):return 'output_limit'
    if message in ('API round request limit reached','DSLab bounded turn limit reached') or message.startswith('API agent step limit reached ('):return 'request_limit'
    return None


def error_code(result):
    error=terminal(result)
    if 'code' in error:return error['code'] if isinstance(error['code'],str) and error['code'] in CODES else None
    return legacy_code(error.get('message',''))
