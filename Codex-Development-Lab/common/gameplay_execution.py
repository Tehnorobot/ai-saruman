"""Retain gameplay lifecycle observations without granting assertion authority.

The optional harness journal is UTF-8 JSONL, flushed after each step. Each row
has schema_version=1, seq (starting at 1), scenario_id, stage, and type. Optional
execution_id binds a row to LAB_GAMEPLAY_EXECUTION_ID. The supervisor supplies a
fresh random LAB_GAMEPLAY_EXECUTION_JOURNAL path for every physical execution.
Neither journal outcomes nor text log lines establish an independent test PASS.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from common.history import _open


MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 512 * 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024
MAX_EVENTS = 10000
STAGES = {'setup', 'action', 'checkpoint', 'scenario'}
TYPES = {'started', 'completed', 'failed', 'crashed'}
STATUSES = {'PASS', 'FAIL', 'ERROR', 'SKIPPED', 'NOT_EXECUTED', 'OBSERVED'}
FIELDS = {'schema_version', 'seq', 'scenario_id', 'execution_id', 'stage', 'type',
          'status', 'detail', 'checkpoint_id', 'timestamp'}
SID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z', re.ASCII)
RUN = re.compile(r'RUN-[a-f0-9]{32}\Z', re.ASCII)
LEGACY = re.compile(r'LAB_ACCEPTANCE_([A-Z_]+):([^\s:]+)(?::(.*))?')
JAVA_ERROR = re.compile(r'^\s*(?:Caused by:\s*)?(?:[A-Za-z_$][\w$]*\.)*(?:[A-Za-z_$][\w$]*)?(?:Error|Exception)(?::.*)?\s*$')


def _parts(relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
            or relative.startswith('/') or any(p in ('', '.', '..') for p in relative.split('/'))):
        raise ValueError('Unsafe gameplay evidence path')
    return tuple(relative.split('/'))


def _read(root, relative, limit):
    with _open(root, _parts(relative)) as handle:
        if os.fstat(handle.fileno()).st_size > limit:
            raise ValueError('Gameplay evidence exceeds parsing bound')
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Gameplay evidence exceeds parsing bound')
    return raw


def _descriptor(path, raw):
    return {'file':path, 'sha256':hashlib.sha256(raw).hexdigest()}


def _snapshot(path, raw):
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _journal_snapshot(root, relative, destination=None):
    """Hash/retain the entire file while bounding only the in-memory parser."""
    digest, prefix, size = hashlib.sha256(), bytearray(), 0
    target = None
    try:
        with _open(root,_parts(relative)) as handle:
            remaining = os.fstat(handle.fileno()).st_size
            if destination is not None:
                target = destination.open('xb')
            while remaining:
                block = handle.read(min(remaining,1024*1024))
                if not block:
                    raise ValueError('Gameplay journal changed during collection')
                remaining -= len(block)
                size += len(block)
                digest.update(block)
                if target is not None:
                    target.write(block)
                if len(prefix) < MAX_JOURNAL_BYTES:
                    prefix.extend(block[:MAX_JOURNAL_BYTES-len(prefix)])
            if os.fstat(handle.fileno()).st_size != size:
                raise ValueError('Gameplay journal changed during collection')
        if target is not None:
            target.flush()
            os.fsync(target.fileno())
    finally:
        if target is not None:
            target.close()
    return bytes(prefix),size,digest.hexdigest()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate gameplay journal key')
        value[key] = item
    return value


def parse_journal(raw, scenario_id, execution_id):
    """Keep valid completed rows even if a crash truncates the final append."""
    if len(raw) > MAX_JOURNAL_BYTES:
        raise ValueError('Gameplay journal exceeds parsing bound')
    if not raw:
        return {'status':'ABSENT','detail':'Harness emitted no execution journal events','events':[]}
    events = []
    status, detail = 'VALID', ''
    for number, line in enumerate(raw.splitlines(keepends=True), 1):
        if not line.endswith(b'\n'):
            status, detail = 'PARTIAL', 'Final journal append is incomplete'
            break
        try:
            if len(line) > MAX_EVENT_BYTES or len(events) >= MAX_EVENTS:
                raise ValueError('Gameplay journal row exceeds parsing bound')
            value = json.loads(line.decode('utf-8'),object_pairs_hook=_unique_object)
            if (not isinstance(value, dict) or set(value) - FIELDS
                    or type(value.get('schema_version')) is not int or value['schema_version'] != 1
                    or type(value.get('seq')) is not int or value['seq'] != number
                    or value.get('scenario_id') != scenario_id
                    or not isinstance(value.get('stage'),str) or value['stage'] not in STAGES
                    or not isinstance(value.get('type'),str) or value['type'] not in TYPES
                    or 'execution_id' in value and value['execution_id'] != execution_id
                    or 'status' in value and (not isinstance(value['status'],str) or value['status'] not in STATUSES)):
                raise ValueError('Invalid gameplay journal event identity or schema')
            for key, maximum in (('detail',4000), ('checkpoint_id',256), ('timestamp',80)):
                if key in value and (not isinstance(value[key],str) or len(value[key]) > maximum
                                     or '\x00' in value[key]):
                    raise ValueError('Invalid gameplay journal text')
            events.append({**value, 'line':number})
        except (ValueError, UnicodeError, RecursionError) as exc:
            status, detail = 'INVALID', 'Journal line %d: %s' % (number, str(exc)[:240])
            break
    return {'status':status, 'detail':detail, 'events':events}


def prepare_journal(execution_id):
    """Create a fresh append target without global UID changes or CHOWN caps.

    In the verifier, this directory/file is supervisor-owned. The client can
    write the file but cannot replace it or change its permissions. Its contents
    remain untrusted diagnostic data, regardless of the supervisor-owned path.
    """
    if not RUN.fullmatch(execution_id):
        raise ValueError('Invalid physical execution identity')
    parent = Path(tempfile.mkdtemp(prefix='lab-gameplay-execution-'))
    target = parent / (execution_id + '.jsonl')
    try:
        with target.open('xb'):
            pass
        target.chmod(0o666)
        parent.chmod(0o755)
    except Exception:
        target.unlink(missing_ok=True)
        parent.rmdir()
        raise
    return target


def release_journal(path):
    """Remove just the known temporary file and its empty supervisor directory."""
    path = Path(path)
    if (not RUN.fullmatch(path.stem) or path.suffix != '.jsonl'
            or not path.parent.name.startswith('lab-gameplay-execution-')
            or path.parent.parent != Path(tempfile.gettempdir())):
        raise ValueError('Invalid supervisor journal cleanup path')
    path.unlink(missing_ok=True)
    path.parent.rmdir()


def log_observations(root, relative, scenario_id):
    """Index exact protocol lines and raw exception declarations, never causes."""
    digest = hashlib.sha256()
    legacy, traces = [], deque(maxlen=32)
    total, indexed_bytes = 0, 0
    legacy_truncated = False
    with _open(root, _parts(relative)) as handle:
        if os.fstat(handle.fileno()).st_size > MAX_LOG_BYTES:
            raise ValueError('Gameplay log exceeds collection bound')
        # Bound every individual read too: a corrupt giant line cannot exhaust memory.
        number = 0
        while True:
            line = handle.readline(64 * 1024)
            if not line:
                break
            digest.update(line)
            total += len(line)
            if total > MAX_LOG_BYTES:
                raise ValueError('Gameplay log exceeds collection bound')
            if len(line) == 64 * 1024 and not line.endswith(b'\n'):
                # Hash the full line, retaining no ambiguous partial selectors.
                while line and not line.endswith(b'\n'):
                    line = handle.readline(64 * 1024)
                    digest.update(line)
                    total += len(line)
                    if total > MAX_LOG_BYTES:
                        raise ValueError('Gameplay log exceeds collection bound')
                number += 1
                continue
            number += 1
            text = line.decode('utf-8',errors='replace').rstrip('\r\n')
            match = LEGACY.search(text)
            if match and (match[2] == scenario_id or match[2].startswith(scenario_id + '.')):
                observation = {'event':match[1], 'detail':text[:4000],
                               'start_line':number, 'end_line':number}
                size = len(json.dumps(observation,ensure_ascii=False).encode('utf-8'))
                if len(legacy) < MAX_EVENTS and indexed_bytes + size <= 512 * 1024:
                    legacy.append(observation)
                    indexed_bytes += size
                else:
                    legacy_truncated = True
            if JAVA_ERROR.fullmatch(text):
                traces.append({'detail':text[:4000], 'start_line':number, 'end_line':number})
    descriptor = {'file':relative, 'sha256':digest.hexdigest()}
    if legacy_truncated:
        descriptor['legacy_index_truncated'] = True
    return descriptor, legacy, list(traces)


def collect_execution_lifecycle(build, directory, output, scenario_id, execution_id, command, *, journal_path=None):
    """Freeze harness observations and supervisor terminal facts after the process."""
    if not SID.fullmatch(scenario_id) or not RUN.fullmatch(execution_id):
        raise ValueError('Invalid gameplay execution identity')
    prefix = directory.relative_to(output).as_posix()
    journal = {'status':'ABSENT', 'detail':'Harness emitted no execution journal', 'events':[]}
    source = 'lab-gameplay-execution/' + execution_id + '.jsonl'
    try:
        journal_root = build if journal_path is None else Path(journal_path).parent
        source = source if journal_path is None else Path(journal_path).name
        raw,source_size,digest = _journal_snapshot(journal_root,source,directory/'gameplay-journal.jsonl')
        journal = parse_journal(raw,scenario_id,execution_id)
        if source_size > len(raw):
            journal.update(status='TRUNCATED',detail='Journal exceeds parsing bound; entire original retained',
                           parsed_prefix_bytes=len(raw),source_size=source_size)
        journal.update(file=prefix+'/gameplay-journal.jsonl',sha256=digest)
    except FileNotFoundError:
        pass
    except (OSError,ValueError,UnicodeError) as exc:
        journal.update(status='INVALID',detail=str(exc)[:500])
    log_error = None
    try:
        log, legacy, traces = log_observations(output,prefix+'/test.log',scenario_id)
    except (OSError,ValueError) as exc:
        log, legacy, traces = None, [], []
        log_error = str(exc)[:500]
    facts = {'status':str(command.get('status','ERROR')),
             'exit_code':command.get('exit_code') if type(command.get('exit_code')) is int else None}
    entered = any(e['stage']=='scenario' and e['type']=='started' for e in journal['events'])
    entered |= any(e['event']=='BEGIN' for e in legacy)
    receipt = {'schema_version':1, 'scenario_id':scenario_id, 'execution_id':execution_id,
               'command':facts, 'scenario_entered':entered, 'journal':journal,
               'legacy_observations':legacy, 'trace_observations':traces, 'log':log,
               'terminal':{'status':'command_succeeded' if facts['status']=='PASS' and facts['exit_code'] in (0,None)
                           else 'command_failed', 'command_status':facts['status'], 'exit_code':facts['exit_code']},
               'authority_note':'Execution diagnostics only. Fresh exact JUnit assertions own test outcomes.'}
    if log_error is not None:
        receipt['log_collection_error'] = log_error
    if command.get('supervisor_error'):
        receipt['supervisor_error'] = str(command['supervisor_error'])[:1000]
    raw = (json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    _snapshot(directory/'gameplay-execution.json',raw)
    return _descriptor(prefix+'/gameplay-execution.json',raw)


def load_execution_lifecycle(stage, item, scenario_id):
    """Read only the selected scenario's hashed supervisor receipt and originals."""
    descriptor = item.get('execution_lifecycle')
    if descriptor is None:
        return None
    expected = 'acceptance/' + scenario_id + '/gameplay-execution.json'
    if not isinstance(descriptor,dict) or descriptor.get('file') != expected:
        raise ValueError('Gameplay receipt is outside the selected scenario')
    raw = _read(stage,expected,MAX_RECEIPT_BYTES)
    if hashlib.sha256(raw).hexdigest() != descriptor.get('sha256'):
        raise ValueError('Gameplay receipt hash mismatch')
    receipt = json.loads(raw,object_pairs_hook=_unique_object)
    if (not isinstance(receipt,dict) or type(receipt.get('schema_version')) is not int
            or receipt['schema_version'] != 1 or receipt.get('scenario_id') != scenario_id
            or not isinstance(receipt.get('execution_id'),str) or not RUN.fullmatch(receipt['execution_id'])
            or receipt['execution_id'] != item.get('execution_id')
            or type(receipt.get('scenario_entered')) is not bool
            or not isinstance(receipt.get('command'),dict) or not isinstance(receipt.get('terminal'),dict)):
        raise ValueError('Invalid gameplay execution receipt')
    command = receipt['command']
    if (command.get('status') != item.get('test_status') or command.get('exit_code') != item.get('exit_code')
            or type(command.get('exit_code')) not in (int,type(None))):
        raise ValueError('Gameplay receipt command does not match supervisor result')
    expected_terminal = {'status':'command_succeeded' if command['status']=='PASS' and command['exit_code'] in (0,None)
                         else 'command_failed', 'command_status':command['status'], 'exit_code':command['exit_code']}
    if receipt['terminal'] != expected_terminal:
        raise ValueError('Gameplay terminal facts do not match supervisor command')
    journal = receipt.get('journal')
    if not isinstance(journal,dict) or journal.get('status') not in ('ABSENT','INVALID','VALID','PARTIAL','TRUNCATED'):
        raise ValueError('Invalid gameplay journal descriptor')
    if 'file' in journal:
        if journal['file'] != 'acceptance/'+scenario_id+'/gameplay-journal.jsonl':
            raise ValueError('Gameplay journal is outside the selected scenario')
        raw_journal,source_size,digest = _journal_snapshot(stage,journal['file'])
        if digest != journal.get('sha256'):
            raise ValueError('Gameplay journal hash mismatch')
        parsed = parse_journal(raw_journal,scenario_id,receipt['execution_id'])
        if journal.get('status') == 'TRUNCATED':
            if (journal.get('parsed_prefix_bytes') != len(raw_journal)
                    or journal.get('source_size') != source_size or source_size <= len(raw_journal)):
                raise ValueError('Invalid gameplay journal truncation descriptor')
            parsed.update(status='TRUNCATED',detail='Journal exceeds parsing bound; entire original retained')
        if any(journal.get(key) != parsed[key] for key in ('status','events','detail')):
            raise ValueError('Gameplay journal summary does not match original')
    elif journal.get('events'):
        raise ValueError('Gameplay events have no original journal')
    log = receipt.get('log')
    if log is None and isinstance(receipt.get('log_collection_error'),str):
        legacy, traces = [], []
        if receipt.get('legacy_observations') != legacy or receipt.get('trace_observations') != traces:
            raise ValueError('Gameplay observations have no readable original log')
    else:
        if not isinstance(log,dict) or log.get('file') != 'acceptance/'+scenario_id+'/test.log':
            raise ValueError('Gameplay log is outside the selected scenario')
        actual_log, legacy, traces = log_observations(stage,log['file'],scenario_id)
        if log != actual_log or receipt.get('legacy_observations') != legacy or receipt.get('trace_observations') != traces:
            raise ValueError('Gameplay log or indexed observations changed')
    entered = any(e['stage']=='scenario' and e['type']=='started' for e in journal.get('events',[]))
    entered |= any(e['event']=='BEGIN' for e in legacy)
    if receipt['scenario_entered'] != entered:
        raise ValueError('Gameplay scenario entry summary does not match originals')
    return receipt


def observed_execution_lifecycle(stage, item, scenario_id):
    """Historical compatibility: exact protocol observations remain diagnostics.

    Older supervisor reports have no journal receipt. Their command outcome and
    selected original log can still show that a scenario entered before it
    stopped; a text PASS never replaces its missing JUnit assertion.
    """
    receipt = load_execution_lifecycle(stage,item,scenario_id)
    if receipt is not None:
        return receipt
    relative = item.get('log')
    if relative != 'acceptance/'+scenario_id+'/test.log':
        return None
    log, legacy, traces = log_observations(stage,relative,scenario_id)
    command = {'status':str(item.get('test_status','NOT_TESTED')),
               'exit_code':item.get('exit_code') if type(item.get('exit_code')) is int else None}
    return {'schema_version':1, 'scenario_id':scenario_id, 'execution_id':item.get('execution_id'),
            'provenance':'historical_supervisor_command_and_original_log', 'command':command,
            'scenario_entered':any(row['event']=='BEGIN' for row in legacy),
            'journal':{'status':'ABSENT','events':[], 'detail':'Historical run has no execution journal'},
            'legacy_observations':legacy, 'trace_observations':traces, 'log':log,
            'terminal':{'status':'command_failed' if command['status'] in ('FAIL','ERROR','TIMEOUT')
                        or command['exit_code'] not in (None,0) else 'command_succeeded',
                        'command_status':command['status'],'exit_code':command['exit_code']},
            'authority_note':'Execution diagnostics only. Fresh exact JUnit assertions own test outcomes.'}
