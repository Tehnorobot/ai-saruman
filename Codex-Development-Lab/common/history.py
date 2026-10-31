"""Bounded, read-only browsing of public CLI events and durable attempt records."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

MAX_JSON = 256 * 1024
MAX_LINE = 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_ATTEMPTS = 2000
PRIVATE_FIELDS = {'authorization', 'api_key', 'access_token', 'refresh_token',
                  'id_token', 'encrypted_content', 'encrypted_reasoning', 'dslab_capability',
                  'dslab_api_key', 'dslab_control_token'}


def _ids(pid, jid):
    if any(not isinstance(x, str) or not re.fullmatch('[0-9a-f]{14}', x) for x in (pid, jid)):
        raise ValueError('Invalid project/job identifier')


def _round(tid, jid):
    if not isinstance(tid, str):
        raise ValueError('Invalid attempt identifier')
    match = re.fullmatch(re.escape(jid) + r'-round([0-9]{2,16})(?:-[0-9a-f]{32})?', tid)
    if not match:
        raise ValueError('Invalid attempt identifier')
    return int(match[1])


@contextmanager
def _open(root, parts):
    """Never follow a task-controlled directory or file link, including races."""
    root = Path(root).absolute()
    if any(not x or x in ('.', '..') or '/' in x or '\\' in x for x in parts):
        raise ValueError('Invalid history path')
    if os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open('/', flags)
        try:
            for component in (*root.parts[1:], *parts[:-1]):
                child = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            opened = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)
        with os.fdopen(opened, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('History source is not a regular file')
            yield source
    else:
        # Development-only Windows fallback. Production services run on Linux.
        path = root
        for component in parts:
            if path.is_symlink():
                raise ValueError('History path contains a symbolic link')
            path /= component
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('History source escapes its root')
        with path.open('rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('History source is not a regular file')
            yield source


def _json(root, parts):
    try:
        with _open(root, parts) as source:
            data = source.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            return None
        value = json.loads(data)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def _size(root, parts):
    try:
        with _open(root, parts) as source:
            return os.fstat(source.fileno()).st_size
    except (OSError, ValueError):
        return None


def _request(value, pid, jid, tid):
    if not value or (value.get('project_id'), value.get('job_id'), value.get('id')) != (pid, jid, tid):
        return None
    return {key: value[key][:bound] for key, bound in
            (('id', 80), ('project_id', 14), ('job_id', 14), ('prompt', MAX_JSON),
             ('model', 96), ('reasoning_effort', 32), ('provider', 16), ('role', 16)) if isinstance(value.get(key), str)}


def _modified(root, parts):
    try:
        with _open(root, parts) as source:
            return datetime.fromtimestamp(os.fstat(source.fileno()).st_mtime, timezone.utc).isoformat()
    except (OSError, ValueError):
        return ''


def _atomic(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def archive_attempt(stage, mailbox, pid, jid, tid, *, request=None, result=None, status=None):
    """Called by the worker BEFORE transport retirement, never by the dashboard."""
    _ids(pid, jid)
    number = _round(tid, jid)
    destination = Path(stage) / 'agent-history' / tid
    destination.mkdir(parents=True, exist_ok=True)
    # Artifacts are worker-owned; don't overwrite an archived original prompt.
    if not (destination / 'request.json').exists():
        saved = _request(request, pid, jid, tid)
        for directory in ('running', 'requests'):
            if saved is None:
                saved = _request(_json(mailbox, (directory, tid + '.json')), pid, jid, tid)
        if saved is not None:
            _atomic(destination / 'request.json', saved)
    saved_result = result or _json(mailbox, ('results', tid + '.json'))
    if saved_result and saved_result.get('id') == tid:
        _atomic(destination / 'result.json', saved_result)
    try:
        with _open(mailbox, ('logs', tid + '.jsonl')) as source:
            remaining = os.fstat(source.fileno()).st_size
            temporary = destination / ('events.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('wb') as target:
                    while remaining:
                        block = source.read(min(remaining, 1024 * 1024))
                        if not block:
                            break
                        target.write(block)
                        remaining -= len(block)
                    target.flush()
                    os.fsync(target.fileno())
                temporary.replace(destination / 'events.jsonl')
            finally:
                temporary.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    meta = _json(destination, ('meta.json',)) or {}
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    meta.update({'id': tid, 'project_id': pid, 'job_id': jid, 'round': number,
                 'created_at': meta.get('created_at', now),
                 'status': status or (saved_result or {}).get('status') or meta.get('status', 'QUEUED')})
    if saved_result or status in ('STOPPED', 'USER_PAUSED', 'TIMEOUT', 'CANCELLED', 'FAIL', 'PASS'):
        meta['finished_at'] = now
    _atomic(destination / 'meta.json', meta)


def _stages(job_root):
    for stage in sorted(job_root.glob('round-*')):
        if stage.is_symlink() or not re.fullmatch(r'round-[0-9]{2,16}', stage.name):
            continue
        number = int(stage.name[6:])
        if stage.name == f'round-{number:02d}':
            yield stage, number


def _candidates(artifacts, mailbox, pid, jid, *, limit=MAX_ATTEMPTS):
    _ids(pid, jid)
    artifacts, mailbox = Path(artifacts), Path(mailbox)
    base = (pid, jid)
    found = {}
    job_root = artifacts.joinpath(*base)
    if job_root.is_symlink():
        return []
    # The UI only discovers fixed filenames under this job, never client paths.
    for stage, stage_number in _stages(job_root):
        if stage.is_symlink():
            continue
        for path in sorted((stage / 'agent-history').glob('*')):
            try:
                number = _round(path.name, jid)
            except ValueError:
                continue
            if path.is_symlink() or stage.name != f'round-{number:02d}':
                continue
            folder = (*base, stage.name, 'agent-history', path.name)
            if (path / 'request.json').is_symlink():
                continue
            raw_request = _json(artifacts, (*folder, 'request.json'))
            meta = _json(artifacts, (*folder, 'meta.json'))
            if (_size(artifacts, (*folder, 'request.json')) is not None
                    and _request(raw_request, pid, jid, path.name) is None):
                continue
            if meta and (meta.get('project_id'), meta.get('job_id'), meta.get('id')) != (pid, jid, path.name):
                continue
            if not raw_request and not meta and _size(artifacts, (*folder, 'events.jsonl')) is None:
                continue
            found[path.name] = {'id': path.name, 'round': number,
                                'archive': folder,
                                'created_at': str((meta or {}).get('created_at') or
                                                  _modified(artifacts, (*folder, 'request.json')))}
            if limit is not None and len(found) >= limit:
                break
    for directory, suffix in (('requests', '.json'), ('running', '.json'),
                               ('logs', '.jsonl'), ('results', '.json')):
        for path in sorted((mailbox / directory).glob(jid + '-round*' + suffix)):
            try:
                number = _round(path.stem, jid)
            except ValueError:
                continue
            if path.is_symlink():
                continue
            found.setdefault(path.stem, {'id': path.stem, 'round': number})['live'] = True
            if limit is not None and len(found) >= limit:
                break
    for tid, item in list(found.items()):
        if not item.get('live'):
            continue
        valid = False
        invalid = False
        for directory in ('running', 'requests'):
            raw = _json(mailbox, (directory, tid + '.json'))
            if _size(mailbox, (directory, tid + '.json')) is not None:
                accepted = _request(raw, pid, jid, tid) is not None
                valid = valid or accepted
                invalid = invalid or not accepted
                if accepted and not item.get('created_at'):
                    item['created_at'] = _modified(mailbox, (directory, tid + '.json'))
        if invalid or (not valid and 'archive' not in item):
            del found[tid]
    rounds = {item['round'] for item in found.values()}
    for stage, number in _stages(job_root):
        if stage.is_symlink():
            continue
        parts = (*base, stage.name, 'codex.jsonl')
        if number not in rounds and _size(artifacts, parts) is not None:
            tid = f'legacy-round{number:02d}'
            found[tid] = {'id': tid, 'round': number, 'legacy': parts}
    return sorted(found.values(), key=lambda x: (x['round'], x.get('created_at', ''), x['id']))


def _describe(item, artifacts, mailbox, pid, jid):
    tid = item['id']
    request = meta = result = None
    log = None
    if 'archive' in item:
        folder = item['archive']
        request = _request(_json(artifacts, (*folder, 'request.json')), pid, jid, tid)
        meta = _json(artifacts, (*folder, 'meta.json'))
        result = _json(artifacts, (*folder, 'result.json'))
        if _size(artifacts, (*folder, 'events.jsonl')) is not None:
            log = (artifacts, (*folder, 'events.jsonl'))
    live_status = None
    if 'live' in item:
        for directory in ('running', 'requests'):
            live_request = _request(_json(mailbox, (directory, tid + '.json')), pid, jid, tid)
            if live_request:
                request = request or live_request
                live_status = 'RUNNING' if directory == 'running' else 'QUEUED'
        result = _json(mailbox, ('results', tid + '.json')) or result
        # Active logs keep growing after the last archived snapshot.
        if _size(mailbox, ('logs', tid + '.jsonl')) is not None:
            log = (mailbox, ('logs', tid + '.jsonl'))
    if 'legacy' in item:
        log = (artifacts, item['legacy'])
    if result and result.get('id') != tid:
        result = None
    public = {'id': tid, 'round': item['round'],
              'role': (request or result or {}).get('role', 'coder'),
              'status': (result or {}).get('status') or live_status or (meta or {}).get('status') or 'ARCHIVED',
              'model': (request or result or {}).get('model', ''),
              'reasoning_effort': (request or result or {}).get('reasoning_effort', 'default'),
              'prompt_available': isinstance((request or {}).get('prompt'), str),
              'log_available': log is not None,
              'log_size': _size(*log) if log else 0,
              'source': 'legacy' if 'legacy' in item else 'live' if 'live' in item else 'archive',
              'started_at': item.get('created_at') or None, 'finished_at': (meta or {}).get('finished_at')}
    return public, request, log


def list_attempts(artifacts, mailbox, pid, jid):
    candidates = _candidates(artifacts, mailbox, pid, jid)
    return {'attempts': [_describe(item, artifacts, mailbox, pid, jid)[0] for item in candidates[:MAX_ATTEMPTS]],
            'truncated': len(candidates) >= MAX_ATTEMPTS}


def _public(value, depth=0):
    if depth > 12:
        return '[nested value omitted]'
    if isinstance(value, str):
        return value[:16000] + ('\n[truncated]' if len(value) > 16000 else '')
    if isinstance(value, dict):
        return {str(k): _public(v, depth + 1) for k, v in list(value.items())[:256]
                if str(k).lower() not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [_public(x, depth + 1) for x in value[:256]]
    return value


def _event(value, offset, main_thread):
    if not isinstance(value, dict):
        return None
    item = value.get('item') if isinstance(value.get('item'), dict) else {}
    kind = str(item.get('type') or value.get('type') or 'event')
    # Only exported public messages/actions belong in this viewer.
    if kind in ('reasoning', 'reasoning_content', 'raw_reasoning'):
        return None
    sender = item.get('sender_thread_id') or item.get('agent_id') or value.get('agent_id') or 'main'
    sender = 'main' if sender == main_thread else str(sender)[:128]
    agents = [sender]
    receivers = item.get('receiver_thread_ids', [])
    if isinstance(receivers, list):
        agents.extend(str(x)[:128] for x in receivers[:32] if isinstance(x, str))
    states = item.get('agents_states')
    if isinstance(states, dict):
        agents.extend(str(x)[:128] for x in list(states)[:32])
    return {'offset': offset, 'type': str(value.get('type', 'event')), 'kind': kind,
            'agent': sender, 'agents': list(dict.fromkeys(agents)), 'data': _public(value)}


def read_attempt(artifacts, mailbox, pid, jid, tid, after=0, limit=100, stream=''):
    _ids(pid, jid)
    if not re.fullmatch(r'legacy-round[0-9]{2,16}', tid):
        _round(tid, jid)
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Invalid history cursor or page size')
    item = next((x for x in _candidates(artifacts, mailbox, pid, jid) if x['id'] == tid), None)
    if item is None:
        raise FileNotFoundError('Unknown agent attempt')
    public, request, log = _describe(item, artifacts, mailbox, pid, jid)
    response = {'attempt': public, 'request': request, 'events': [], 'next_offset': after,
                'has_more': False, 'reset': False, 'stream': '', 'pending_line': False}
    if not log:
        response['next_offset'] = 0
        return response
    try:
        with _open(*log) as source:
            identity = os.fstat(source.fileno())
            first = source.readline(4096)
            main_thread = None
            try:
                first_event = json.loads(first)
                if isinstance(first_event, dict) and first_event.get('type') == 'thread.started':
                    main_thread = first_event.get('thread_id')
            except (ValueError, UnicodeError, RecursionError):
                pass
            key = hashlib.sha256(str((identity.st_dev, identity.st_ino)).encode() + first).hexdigest()[:24]
            reset = after > identity.st_size or bool(stream and stream != key)
            source.seek(0 if reset else after)
            response.update(reset=reset, stream=key)
            scanned = 0
            while len(response['events']) < limit and scanned < MAX_PAGE_BYTES:
                position = source.tell()
                line = source.readline(MAX_LINE + 1)
                if not line:
                    break
                scanned += len(line)
                if not line.endswith(b'\n') and len(line) <= MAX_LINE:
                    source.seek(position)
                    response['pending_line'] = True
                    break
                if len(line) > MAX_LINE:
                    event = _event({'type': 'log.warning', 'text': 'Oversized log record omitted'}, position, main_thread)
                else:
                    try:
                        event = _event(json.loads(line), position, main_thread)
                    except (ValueError, UnicodeError, RecursionError):
                        event = _event({'type': 'log.text', 'text': line.decode('utf-8', errors='replace')}, position, main_thread)
                if event:
                    response['events'].append(event)
            response['next_offset'] = source.tell()
            response['has_more'] = source.tell() < os.fstat(source.fileno()).st_size and not response['pending_line']
    except (FileNotFoundError, OSError, ValueError):
        # A live transport can disappear between polls after archival/retirement.
        response.update(reset=True, next_offset=0, has_more=False)
    return response
