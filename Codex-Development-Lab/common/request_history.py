"""Fast dashboard reads with background CLI accounting and stable page snapshots."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from common import token_usage

_LOCK = RLock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='usage-index')
_REPORTS = OrderedDict()
_PAGES = OrderedDict()
REFRESH_SECONDS = 5
MAX_JOBS = 128
MAX_PAGES = 1024
CURSOR_SECONDS = 3600


def _key(artifacts, mailboxes, pid, jid):
    return (str(Path(artifacts).absolute()), tuple(sorted((r, str(Path(p).absolute()))
            for r, p in mailboxes.items())), pid, jid)


def _refresh(key, entry, artifacts, mailboxes, pid, jid):
    try:
        report = token_usage.codex_usage(artifacts, mailboxes, pid, jid, request_limit=None)
        with _LOCK:
            entry.update(report=report, updated=monotonic(), error=False)
    except Exception:
        # Preserve the last successful counters on transient archive errors.
        with _LOCK:
            entry.update(error=True, updated=monotonic())
    finally:
        with _LOCK:
            entry['loading'] = False


def snapshot(artifacts, mailboxes, pid, jid):
    """Return immediately, scheduling disk work at most once per refresh period."""
    key = _key(artifacts, mailboxes, pid, jid)
    with _LOCK:
        entry = _REPORTS.setdefault(key, {'report': {}, 'updated': 0, 'loading': False, 'error': False})
        _REPORTS.move_to_end(key)
        if not entry['loading'] and monotonic() - entry['updated'] >= REFRESH_SECONDS:
            entry['loading'] = True
            _POOL.submit(_refresh, key, entry, artifacts, dict(mailboxes), pid, jid)
        while len(_REPORTS) > MAX_JOBS:
            _REPORTS.popitem(last=False)
        return deepcopy(entry['report']), {
            'codex_loading': entry['loading'],
            'codex_stale': bool(entry['report']) and (entry['loading'] or entry['error']),
            'codex_index_error': entry['error'],
        }


def page(jid, limit, cursor, codex, fetch_native):
    """Merge stream heads without materializing or capping the native history.

    Native rows retain their insertion order. CLI rows describe completed turns
    per attempt, never guessed individual API calls. Continuations freeze the CLI
    snapshot and any unread native rows, so concurrent inserts cannot shift pages.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100')
    if cursor is not None:
        with _LOCK:
            saved = _PAGES.get(cursor)
            if not saved or saved['jid'] != jid or monotonic() - saved['created'] > CURSOR_SECONDS:
                raise ValueError('Invalid or expired request cursor; refresh the first page')
            if limit in saved['responses']:
                return deepcopy(saved['responses'][limit])
            state = {**saved['state'], 'native': deepcopy(saved['state']['native'])}
    else:
        state = {'cli': tuple(deepcopy(codex.get('requests', []))), 'offset': 0,
                 'native': [], 'native_cursor': None, 'native_more': True}
    rows = []
    native_available = True
    while len(rows) < limit:
        if not state['native'] and state['native_more']:
            native = fetch_native(state['native_cursor'], limit)
            native_available = native.get('available', False)
            if not native_available:
                # Keep the continuation retryable while the gateway is offline.
                break
            state['native'] = native.get('requests', [])
            state['native_cursor'] = native.get('next_cursor')
            state['native_more'] = state['native_cursor'] is not None
            if not state['native'] and state['native_more']:
                raise ValueError('Gateway returned an empty continuation')
        cli = state['cli'][state['offset']] if state['offset'] < len(state['cli']) else None
        api = state['native'][0] if state['native'] else None
        if cli is None and api is None:
            break
        if cli is not None and (api is None or token_usage._timestamp(cli.get('started_at')) >
                               token_usage._timestamp(api.get('started_at'))):
            rows.append(cli)
            state['offset'] += 1
        else:
            rows.append(state['native'].pop(0))
    # CLI records remain useful when the gateway is unavailable.
    if not native_available:
        while len(rows) < limit and state['offset'] < len(state['cli']):
            rows.append(state['cli'][state['offset']])
            state['offset'] += 1
    more = bool(state['native'] or state['native_more'] or state['offset'] < len(state['cli']))
    next_cursor = None
    if more:
        next_cursor = uuid4().hex
        with _LOCK:
            _PAGES[next_cursor] = {'jid': jid, 'created': monotonic(), 'state': state, 'responses': {}}
            while len(_PAGES) > MAX_PAGES:
                _PAGES.popitem(last=False)
    result = {'job_id': jid, 'available': bool(native_available or codex.get('available')),
            'api_available': native_available, 'requests': rows, 'next_cursor': next_cursor,
            'codex_truncated': codex.get('truncated', False)}
    if cursor is not None and native_available:
        with _LOCK:
            if cursor in _PAGES:_PAGES[cursor]['responses'][limit] = deepcopy(result)
    return result
