"""LiteLLM smoke tests. Standard library only; no automatic retries.

Run: python test_litellm.py
Offline check: python test_litellm.py --self-test
Each model: text, streaming, tool call and tool-result continuation (4 requests).
The real server is contacted only when this script is run without --self-test.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MODELS = [
    'deepseek-ai/DeepSeek-V4-Flash-0731',
    'Qwen/Qwen3-Next-80B-A3B-Instruct-FP8',
    'google/gemma-4-26B-A4B-it',
    'zai-org/GLM-5.3-Flash',
    'Qwen/Qwen3.8-27B',
]
LIMIT = 2 * 1024 * 1024


def load_settings(filename):
    # Read only these two settings; never execute .env as shell code.
    wanted = {'LITELLM_BASE_URL', 'LITELLM_API_KEY'}
    values = {k: os.environ[k] for k in wanted if os.environ.get(k)}
    if filename.is_file():
        for line in filename.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].lstrip()
            name, separator, value = line.partition('=')
            name = name.strip()
            if not separator or name not in wanted or name in values:
                continue
            value = value.strip()
            if value[:1] in ('"', "'"):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 0:
                    raise ValueError('Unclosed quote for ' + name)
                value = value[1:end]
            else:
                value = value.split(' #', 1)[0].strip()
            values[name] = value
    if not all(values.get(k) for k in wanted):
        raise ValueError('Set LITELLM_BASE_URL and LITELLM_API_KEY in the .env file or environment')
    return values


def endpoint(base):
    base = base.strip().rstrip('/')
    parts = urllib.parse.urlsplit(base)
    if (parts.scheme not in ('http', 'https') or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError('Use a plain http(s) LiteLLM base URL without credentials or query parameters')
    # Also accept proxies mounted under a prefix and a complete endpoint URL.
    if parts.path.endswith('/chat/completions'):
        return base
    return base + ('/v1/chat/completions' if not parts.path else '/chat/completions')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('HTTP redirect refused: configure the final endpoint URL')


def stream_result(lines):
    content, calls, usage, finish, done = '', {}, {}, None, False
    data_lines = []

    def event(raw):
        nonlocal content, usage, finish, done
        if raw.strip() == '[DONE]':
            done = True
            return
        value = json.loads(raw)
        if value.get('error'):
            raise ValueError('Provider returned an error event')
        if value.get('usage'):
            usage = value['usage']
        for choice in value.get('choices', []):
            delta = choice.get('delta') or {}
            content += delta.get('content') or ''
            if choice.get('finish_reason'):
                finish = choice['finish_reason']
            for call in delta.get('tool_calls') or []:
                current = calls.setdefault(call['index'], {'id': '', 'type': 'function',
                    'function': {'name': '', 'arguments': ''}})
                current['id'] += call.get('id') or ''
                for key in ('name', 'arguments'):
                    current['function'][key] += (call.get('function') or {}).get(key) or ''

    for raw in lines:
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            data_lines.append(line[5:].lstrip())
        elif not line and data_lines:
            event('\n'.join(data_lines))
            data_lines.clear()
    if data_lines:
        event('\n'.join(data_lines))
    if not done or not finish:
        raise ValueError('Incomplete stream: finish_reason or [DONE] missing')
    return {'choices': [{'message': {'role': 'assistant', 'content': content,
             'tool_calls': list(calls.values())}, 'finish_reason': finish}], 'usage': usage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path(__file__).parent / 'Codex-Development-Lab/.env')
    parser.add_argument('--model', action='append', help='Test only this model ID; may repeat')
    parser.add_argument('--max-tokens', type=int, default=2048)
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--report', type=Path, default=Path(__file__).with_name('litellm-test-results.json'))
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        assert endpoint('https://example.test/v1/') == 'https://example.test/v1/chat/completions'
        assert endpoint('http://localhost:4000/v1/chat/completions').endswith('/v1/chat/completions')
        chunks = [b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n', b'\n',
                  b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}\n', b'\n',
                  b'data: [DONE]\n', b'\n']
        result = stream_result(chunks)
        assert result['choices'][0]['message']['content'] == 'OK'
        assert result['usage']['prompt_tokens'] == 10
        try:
            stream_result(chunks[:-2])
        except ValueError:
            pass
        else:
            raise AssertionError('Truncated stream accepted')
        print('SELF-TEST PASS (no server calls)')
        return 0
    if not 64 <= args.max_tokens <= 32768 or not 1 <= args.timeout <= 600:
        parser.error('max-tokens must be 64..32768; timeout must be 1..600 seconds')
    config = load_settings(args.env_file)
    key = config['LITELLM_API_KEY']
    url = endpoint(config['LITELLM_BASE_URL'])
    parts = urllib.parse.urlsplit(url)
    gateway_compatible = (parts.path == '/v1/chat/completions' and
        ((parts.scheme == 'https' and parts.port in (None, 443)) or
         (parts.scheme == 'http' and parts.hostname == 'lnsigo.mipt.ru' and parts.port == 4000)))
    print('Endpoint:', url)
    print('Project gateway URL format:', 'OK' if gateway_compatible else
          'DIFFERENT: use HTTPS:443 or the configured lnsigo.mipt.ru:4000 HTTP exception')
    print('Up to 4 paid requests per model; no retries. Output cap per request:', args.max_tokens)
    if parts.scheme == 'http':
        print('HTTP endpoint: the connection does not encrypt the API key.')
    opener = urllib.request.build_opener(NoRedirect())
    report = {'endpoint': url, 'gateway_url_format_compatible': gateway_compatible, 'results': []}

    def request(model, messages, stream=False, tools=None):
        body = {'model': model, 'messages': messages, 'max_tokens': args.max_tokens, 'stream': stream}
        if stream:
            body['stream_options'] = {'include_usage': True}
        if tools:
            body.update(tools=tools, tool_choice='auto')
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
        with opener.open(req, timeout=args.timeout) as response:
            if stream:
                def bounded_lines():
                    total = 0
                    while True:
                        line = response.readline(LIMIT + 1)
                        if not line:
                            return
                        total += len(line)
                        if total > LIMIT:
                            raise ValueError('Response exceeds 2 MiB limit')
                        yield line
                return stream_result(bounded_lines())
            raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError('Response exceeds 2 MiB limit')
            return json.loads(raw)

    def check(model, name, operation):
        start = time.monotonic()
        row = {'model': model, 'test': name}
        try:
            response = operation()
            choice = response['choices'][0]
            if choice.get('finish_reason') in ('length', 'content_filter'):
                raise ValueError('Completion did not finish normally: ' + choice['finish_reason'])
            row.update(status='PASS', usage=response.get('usage') or {},
                       usage_available=bool(response.get('usage')))
            return response
        except urllib.error.HTTPError as exc:
            # Do not persist raw headers/body: errors can reflect credentials or internal details.
            row.update(status='FAIL', error='HTTP ' + str(exc.code))
        except Exception as exc:
            row.update(status='FAIL', error=(type(exc).__name__ + ': ' + str(exc)).replace(key, '[REDACTED]')[:300])
        finally:
            row['seconds'] = round(time.monotonic() - start, 2)
            report['results'].append(row)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print('  {test}: {status} ({seconds}s) {error}'.format(error=row.get('error', ''), **{k:v for k,v in row.items() if k!='error'}))

    tools = [{'type': 'function', 'function': {'name': 'add_numbers', 'description': 'Add two integers.',
              'parameters': {'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
                             'required': ['a', 'b'], 'additionalProperties': False}}}]
    for model in args.model or MODELS:
        print('\nModel:', model)
        prompt = [{'role': 'user', 'content': 'Reply with only OK.'}]
        def text_test(stream=False):
            result = request(model, prompt, stream=stream)
            if (result['choices'][0]['message'].get('content') or '').strip() != 'OK':
                raise ValueError('Expected the text OK (reasoning may have exhausted the output cap)')
            return result
        check(model, 'text', text_test)
        check(model, 'stream+usage', lambda: text_test(True))
        messages = [{'role': 'user', 'content': 'Use add_numbers to calculate 17 + 25. Call the tool; do not calculate yourself.'}]
        def tool_test():
            result = request(model, messages, tools=tools)
            calls = result['choices'][0]['message'].get('tool_calls') or []
            if len(calls) != 1 or calls[0]['function']['name'] != 'add_numbers':
                raise ValueError('Expected one native add_numbers tool call')
            values = json.loads(calls[0]['function']['arguments'])
            if values != {'a': 17, 'b': 25} or not calls[0].get('id'):
                raise ValueError('Incorrect tool arguments or missing tool-call ID')
            return result
        result = check(model, 'tool-call', tool_test)
        if result:
            assistant = result['choices'][0]['message']
            messages += [assistant, {'role': 'tool', 'tool_call_id': assistant['tool_calls'][0]['id'], 'content': '42'},
                         {'role': 'user', 'content': 'Reply with only the result returned by the tool.'}]
            def continuation():
                result = request(model, messages, tools=tools)
                if (result['choices'][0]['message'].get('content') or '').strip() != '42':
                    raise ValueError('Model failed to consume the tool result')
                return result
            check(model, 'tool-result', continuation)
    failed = sum(row['status'] != 'PASS' for row in report['results'])
    unknown = sum(row.get('status') == 'PASS' and not row.get('usage_available') for row in report['results'])
    print('\nFAIL:', failed, '| successful requests without usage:', unknown)
    print('Report:', args.report)
    print('This checks connectivity/protocol, not coding quality. Direct calls are not in the project usage counter.')
    return 1 if failed or unknown else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print('Configuration/local error:', type(exc).__name__, file=sys.stderr)
        sys.exit(2)
