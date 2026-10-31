"""Small AsyncOpenAI transport; gateway still owns budgets and native tools.

The SDK parses SSE into typed chunks. The wire guard only bounds event size
and records its terminal marker, so an interrupted response cannot become a
successful task merely because its last received chunk contained usage.
"""
import json
import httpx

MAX_EVENT_BYTES = 8 * 1024 * 1024


class _WireGuard(httpx.AsyncByteStream):
    def __init__(self, source):
        self.source = source
        self.pending = b''
        self.event_bytes = 0
        self.done = False

    async def __aiter__(self):
        async for chunk in self.source:
            self.observe(chunk)
            yield chunk

    def observe(self, chunk):
        self.pending += chunk
        if len(self.pending) > MAX_EVENT_BYTES:
            raise ValueError('Oversized provider line')
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            line = line.rstrip(b'\r')
            self.event_bytes += len(line) + 1
            if self.event_bytes > MAX_EVENT_BYTES:
                raise ValueError('Oversized provider event')
            if not line:
                self.event_bytes = 0
            if line.strip() == b'data: [DONE]':
                self.done = True

    async def aclose(self):
        await self.source.aclose()


class OpenAIResponse:
    def __init__(self, stream, guard):
        self.stream = stream
        self.guard = guard
        self.status_code = stream.response.status_code
        self.headers = stream.response.headers

    @property
    def complete(self):
        return self.guard is not None and self.guard.done

    async def aiter_events(self):
        async for chunk in self.stream:
            value = chunk.model_dump(exclude_none=True)
            if not isinstance(value, dict) or len(json.dumps(value).encode()) > MAX_EVENT_BYTES:
                raise ValueError('Oversized or invalid provider chunk')
            yield value

    async def aclose(self):
        await self.stream.close()


class OpenAIClient:
    def __init__(self, api_key, endpoint, *, transport=None):
        # Explicit credentials and endpoint; never use OpenAI defaults or a
        # project SDK/config file. Import lazily for native DSLab-only service.
        from openai import AsyncOpenAI
        self.guard = None

        async def guard_response(response):
            if response.status_code == 200 and 'text/event-stream' in response.headers.get('content-type', ''):
                self.guard = _WireGuard(response.stream)
                if response.is_stream_consumed:
                    # MockTransport and explicit cached responses bypass the
                    # stream iterator; normal live HTTP responses are unread.
                    self.guard.observe(response.content)
                else:
                    response.stream = self.guard

        self.http = httpx.AsyncClient(transport=transport,
            timeout=httpx.Timeout(600, connect=30), follow_redirects=False,
            event_hooks={'response': [guard_response]})
        base_url = endpoint.removesuffix('/chat/completions') + '/'
        self.sdk = AsyncOpenAI(api_key=api_key, base_url=base_url,
            max_retries=0, timeout=httpx.Timeout(600, connect=30), http_client=self.http)

    async def send(self, payload):
        stream = await self.sdk.chat.completions.create(**payload)
        if not hasattr(stream, 'response') or not hasattr(stream, '__aiter__'):
            raise httpx.RemoteProtocolError('Provider did not return a streaming completion')
        return OpenAIResponse(stream, self.guard)

    async def aclose(self):
        await self.sdk.close()


def safe_error(exc):
    """Return only bounded SDK error classification, never its message/body."""
    try:
        from openai import APIError, APIStatusError
    except ImportError:
        return None
    if isinstance(exc, APIStatusError):
        return 'upstream_rejected', 'Provider rejected request (HTTP %d)' % exc.status_code
    if isinstance(exc, APIError):
        return 'connection_failed', 'Provider connection failed'
    return None
