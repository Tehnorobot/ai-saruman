"""Controller-owned progress accounting; agent completion claims are never evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from common.history import _open

LIMIT = 1024 * 1024
STALL_ROUNDS = 3
MAX_ITEMS = 4000
NAME = 'progress-guard.json'


def _read(root, parts):
    try:
        with _open(root, parts) as source:
            raw = source.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('Progress evidence exceeds size limit')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Invalid progress evidence')
        return value
    except FileNotFoundError:
        return {}


def _write(root, value):
    """Replace a bounded file atomically without following its existing inode."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    if len(raw) > LIMIT:
        raise ValueError('Progress state exceeds size limit')
    root = Path(root).absolute()
    temporary = '.' + NAME + '-' + secrets.token_hex(12)
    if os.name == 'posix':
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open('/', flags)
        try:
            for component in root.parts[1:]:
                child = os.open(component, flags, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, 'wb') as destination:
                    destination.write(raw)
                    destination.flush()
                    os.fsync(destination.fileno())
                os.replace(temporary, NAME, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory)
    else:
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError('Linked progress root')
        path = root / temporary
        try:
            with path.open('xb') as destination:
                destination.write(raw)
            os.replace(path, root / NAME)
        finally:
            path.unlink(missing_ok=True)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _failure(check):
    # Keep meaningful exception text, not Gradle timings, temporary paths or stack line numbers.
    detail = str(check.get('detail') or '')[:2000]
    lines = [line.strip() for line in str(check.get('tail') or '').splitlines()
             if re.search(r'error|exception|permission denied|failed|failure', line, re.I)][:8]
    text = detail + '\n' + '\n'.join(lines)
    text = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ms|seconds?|secs?|s)\b', '<duration>', text)
    text = re.sub(r'line \d+', 'line <n>', text)
    return text[:3000]


def _observations(report, acceptance, results):
    observations = set()
    attempted = set()
    for check in report.get('checks', [])[:MAX_ITEMS]:
        if isinstance(check, dict):
            status = str(check.get('status', ''))
            observations.add(_hash(['check', check.get('label'), status, check.get('exit_code'),
                                    _failure(check) if status in ('FAIL', 'ERROR', 'TIMEOUT') else '']))
    scenarios = list(results.get('scenarios', []))[:MAX_ITEMS]
    for requirement in acceptance.get('requirements', [])[:MAX_ITEMS]:
        if not isinstance(requirement, dict):
            continue
        # Only controller-derived successful requirements count; no source-plan counters or claims.
        if requirement.get('status') == 'TEST_PASSED':
            observations.add(_hash(['requirement_passed', requirement.get('id')]))
        scenarios.extend(requirement.get('scenarios', [])[:MAX_ITEMS])
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get('id'), str):
            continue
        sid = scenario['id'][:128]
        status = scenario.get('status')
        # A NOT_TESTED placeholder or a new attempt_round is not a fresh executed test.
        if scenario.get('test_status') in ('PASS', 'FAIL'):
            attempted.add(sid)
            observations.add(_hash(['scenario', sid, status, scenario.get('test_status'),
                                    scenario.get('video_capture', {}).get('status')
                                    if isinstance(scenario.get('video_capture'), dict) else None]))
    return observations, attempted


def record_round(output, number, sourcehash, report, *, model_ran=True, generation=0):
    """Count three unchanged paid rounds, independently of rotating verification batches.

    ``output`` is the controller artifact job directory. ``generation`` changes only
    on explicit user resume, granting a fresh streak. Replays of a recorded round
    are idempotent. Verification-only progress can reset, but never increase, a streak.
    """
    if type(number) is not int or number < 0 or type(generation) is not int or generation < 0:
        raise ValueError('Invalid round or resume generation')
    if not isinstance(report, dict) or not isinstance(sourcehash, str) or len(sourcehash) > 128:
        raise ValueError('Invalid controller progress input')
    root = Path(output)
    state = _read(root, (NAME,))
    if state and state.get('generation') == generation and number <= state.get('last_round', -1):
        return state
    folder = f'round-{number:02d}'
    acceptance = _read(root, (folder, 'acceptance.json'))
    if not acceptance:
        acceptance = report.get('acceptance', {})
    results = _read(root, (folder, 'acceptance_results.json'))
    if not results:
        results = report.get('acceptance_results', {})
    observations, attempted = _observations(report, acceptance, results)
    task_id=report.get('task_assignment',{}).get('current_task_id')
    if task_id:
        # Assigned work measures independent coverage/recovery. Cosmetic source
        # edits and changing exception text cannot renew its model budget.
        passing={'checks':[c for c in report.get('checks',[]) if isinstance(c,dict) and c.get('status')=='PASS']}
        passed_acceptance={'requirements':[{**r,'scenarios':[s for s in r.get('scenarios',[])
            if isinstance(s,dict) and s.get('status')=='TEST_PASSED']}
            for r in acceptance.get('requirements',[]) if isinstance(r,dict)]}
        passed_results={'scenarios':[s for s in results.get('scenarios',[])
            if isinstance(s,dict) and s.get('status')=='TEST_PASSED']}
        observations,_=_observations(passing,passed_acceptance,passed_results)
    seen = set(state.get('observations', []))
    executed = set(state.get('attempted_scenarios', []))
    reasons = []
    if not state:
        reasons.append('initial_baseline')
    if task_id and task_id!=state.get('task_id'):
        reasons.append('task_started')
    if not task_id and sourcehash and sourcehash != state.get('source_sha256'):
        reasons.append('source_changed')
    if observations - seen:
        reasons.append('new_independent_evidence')
    if attempted - executed:
        reasons.append('new_scenarios_executed')
    streak = state.get('streak', 0) if state.get('generation') == generation else 0
    if reasons:
        streak = 0
    elif model_ran:
        streak += 1
    state = {'schema_version': 1, 'generation': generation, 'last_round': number,
             'task_id':task_id,
             'source_sha256': sourcehash, 'streak': streak, 'threshold': STALL_ROUNDS,
             'stalled': streak >= STALL_ROUNDS, 'model_ran': bool(model_ran),
             'progress': bool(reasons), 'reasons': reasons,
             'observations': sorted(seen | observations)[-MAX_ITEMS:],
             'attempted_scenarios': sorted(executed | attempted)[-MAX_ITEMS:]}
    _write(root, state)
    return state
