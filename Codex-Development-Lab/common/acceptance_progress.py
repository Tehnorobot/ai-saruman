"""Controller-owned acceptance history and fair scheduling across source revisions.

Historical success is information, never evidence that changed code passed.
Only normalized verifier reports enter this ledger; workspace claims do not.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from common import acceptance

FILENAME = 'acceptance-progress.json'
MAX_BYTES = 32 * 1024 * 1024
MAX_ROUNDS = 2000
MAX_HISTORY = 16
HEX = re.compile(r'[0-9a-f]{64}')
ROUND = re.compile(r'round-([0-9]{2,16})')


def definition_digest(scenario):
    fields = {key: scenario.get(key) for key in
              ('id', 'kind', 'task', 'requirement_ids', 'expected_tests', 'description')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def _number(value):
    return type(value) is int and 0 <= value <= 10**16


def _digest(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _empty(catalog):
    return {'schema_version': 1, 'spec_sha256': catalog['spec_sha256'],
            'backfilled_through_round': 0, 'scenarios': {}}


def _read(root, name):
    return acceptance.read_json(root, name, MAX_BYTES)


def _proof_passed(result, kind):
    proof = result.get('assertion_evidence')
    return (result.get('status') == 'TEST_PASSED' and result.get('test_status') == 'PASS'
            and isinstance(proof, dict) and isinstance(proof.get('reports'), list)
            and bool(proof['reports']) and bool(proof.get('expected_tests'))
            and not proof.get('missing_tests')
            )


def _valid_record(record):
    if not isinstance(record, dict) or not _number(record.get('round')):
        return False
    if not _digest(record.get('source_sha256')):
        return False
    if record.get('definition_sha256') and not _digest(record['definition_sha256']):
        return False
    result = record.get('result')
    return (isinstance(result, dict) and isinstance(result.get('id'), str)
            and result.get('status') in ('TEST_PASSED', 'FAIL', 'NOT_TESTED', 'STALE')
            and isinstance(result.get('requirement_ids'), list)
            and len(result['requirement_ids']) <= 64
            and all(isinstance(rid, str) and len(rid) <= 128 for rid in result['requirement_ids'])
            and result.get('test_status') in ('PASS', 'FAIL', 'ERROR', 'TIMEOUT', 'NOT_TESTED', 'SKIP')
            and len(json.dumps(result, ensure_ascii=False).encode()) <= 128 * 1024)


def _validate(raw, catalog):
    if (not isinstance(raw, dict) or raw.get('schema_version') != 1
            or raw.get('spec_sha256') != catalog['spec_sha256']
            or not _number(raw.get('backfilled_through_round'))
            or not isinstance(raw.get('scenarios'), dict)
            or len(raw['scenarios']) > acceptance.MAX_ITEMS):
        raise ValueError('Invalid acceptance progress ledger')
    for sid, entry in raw['scenarios'].items():
        if (not isinstance(sid, str) or len(sid) > 64 or not acceptance.ID.fullmatch(sid)
                or not isinstance(entry, dict) or not _number(entry.get('last_attempt_round'))
                or not isinstance(entry.get('history'), list) or len(entry['history']) > MAX_HISTORY):
            raise ValueError('Invalid acceptance progress scenario')
        for key in ('latest', 'last_pass'):
            if entry.get(key) is not None and not _valid_record(entry[key]):
                raise ValueError('Invalid acceptance progress evidence')
            if entry.get(key) and entry[key]['result']['id'] != sid:
                raise ValueError('Mismatched acceptance evidence identity')
        for event in entry['history']:
            if (not isinstance(event, dict) or not _number(event.get('round'))
                    or event.get('status') not in ('TEST_PASSED', 'FAIL', 'NOT_TESTED', 'STALE')):
                raise ValueError('Invalid acceptance progress history')
    return raw


def _entries(ledger, plan):
    # Retired scenarios are removed to keep the ledger bounded. Archived reports
    # still retain their complete history; current definitions cannot be evicted.
    current = {s['id'] for s in plan['scenarios']}
    ledger['scenarios'] = {sid: item for sid, item in ledger['scenarios'].items() if sid in current}
    return ledger


def _insert(ledger, scenario, result, round_number, source_sha256, definition=''):
    sid = scenario['id']
    entry = ledger['scenarios'].setdefault(sid, {'last_attempt_round': 0, 'history': []})
    record = {'round': round_number, 'source_sha256': source_sha256,
              'definition_sha256': definition, 'result': copy.deepcopy(result)}
    record['result'].setdefault('artifact_round', f'round-{round_number:02d}')
    record['result'].setdefault('attempt_round', round_number)
    # Per-check explanations can exceed the existing ledger record budget.
    # Compact only this private copy and only optional, reconstructible fields;
    # full facts and XML remain in the immutable round artifacts. Original proof
    # descriptors, expected/missing tests, failures and verdict are never cut.
    if len(json.dumps(record['result'], ensure_ascii=False).encode()) > 128 * 1024:
        record['result'].pop('failure_details', None)
        if len(json.dumps(record['result'], ensure_ascii=False).encode()) > 128 * 1024:
            proof = record['result'].get('assertion_evidence')
            if isinstance(proof, dict):
                proof.pop('test_cases', None)
    if not _valid_record(record):
        return
    # Backfill can encounter carried-forward evidence repeatedly. Its original
    # attempt round, rather than the containing report, identifies the execution.
    event = {'round': round_number, 'source_sha256': source_sha256,
             'definition_sha256': definition, 'status': result.get('status', 'NOT_TESTED'),
             'detail': str(result.get('detail', ''))[:500]}
    history = entry['history']
    identity = (round_number, source_sha256, definition)
    history[:] = [e for e in history if (e['round'], e.get('source_sha256'),
                                         e.get('definition_sha256', '')) != identity]
    history.append(event)
    history.sort(key=lambda e: e['round'])
    entry['history'] = history[-MAX_HISTORY:]
    if round_number >= entry.get('last_attempt_round', 0):
        entry.update(last_attempt_round=round_number, latest=record)
    if _proof_passed(result, scenario['kind']):
        if round_number >= (entry.get('last_pass') or {}).get('round', -1):
            entry['last_pass'] = record


def load_progress(job_root, catalog, plan):
    """Load trusted artifacts and migrate old reports once, without reusing old proofs.

    Legacy reports lack an exact scenario-definition digest. They establish that
    a scenario ran, but cannot verify its present task/assertion mapping.
    """
    root = Path(job_root)
    try:
        ledger = _validate(_read(root, FILENAME), catalog)
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
        ledger = _empty(catalog)
    _entries(ledger, plan)
    known = {s['id']: s for s in plan['scenarios']}
    reports = []
    if root.is_dir() and not root.is_symlink():
        for path in root.iterdir():
            match = ROUND.fullmatch(path.name)
            if match and not path.is_symlink() and int(match[1]) > ledger['backfilled_through_round']:
                reports.append((int(match[1]), path.name))
    # Bounded even for a malicious or unexpectedly huge artifact directory.
    for number, name in sorted(reports)[-MAX_ROUNDS:]:
        try:
            state = _read(root, name + '/acceptance.json')
            if not isinstance(state, dict) or state.get('spec_sha256') != catalog['spec_sha256']:
                continue
            source = state.get('source_sha256')
            requirements = state.get('requirements')
            if not _digest(source) or not isinstance(requirements, list) or len(requirements) > acceptance.MAX_ITEMS:
                continue
            seen = set()
            for requirement in requirements:
                if not isinstance(requirement, dict):
                    continue
                scenarios = requirement.get('scenarios', [])
                if not isinstance(scenarios, list) or len(scenarios) > 64:
                    continue
                for result in scenarios:
                    if not isinstance(result, dict):
                        continue
                    sid = result.get('id')
                    if not isinstance(sid, str) or sid not in known or sid in seen or 'test_status' not in result:
                        continue
                    attempt = result.get('attempt_round', number)
                    if not _number(attempt) or attempt > number:
                        continue
                    seen.add(sid)
                    _insert(ledger, known[sid], result, attempt, source)
            ledger['backfilled_through_round'] = max(number, ledger['backfilled_through_round'])
        except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
            continue
    return ledger


def save_progress(job_root, ledger):
    """Atomically replace a controller-owned artifact; never write to workspace."""
    root = Path(job_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Acceptance progress root must be a real directory')
    raw = json.dumps(ledger, ensure_ascii=False, indent=2).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('Acceptance progress exceeds size limit')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix='.acceptance-progress-', delete=False) as handle:
            temporary = handle.name
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / FILENAME)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def current_results(ledger, catalog, plan, source_sha256):
    """Return exact-source, exact-definition evidence; build_state validates it again."""
    evidence = []
    if ledger.get('spec_sha256') == catalog['spec_sha256']:
        for scenario in plan['scenarios']:
            record = ledger['scenarios'].get(scenario['id'], {}).get('latest')
            if (record and record['source_sha256'] == source_sha256
                    and record['definition_sha256'] == definition_digest(scenario)):
                evidence.append(copy.deepcopy(record['result']))
    return {'spec_sha256': catalog['spec_sha256'], 'scenarios': evidence}


def select_request(catalog, plan, ledger, source_sha256, max_scenarios=4):
    """Unseen/changed definitions first, then oldest attempted pending scenarios.

    An error cannot monopolize the queue: after it runs its attempt round moves
    forward, letting all other pending scenarios advance before its next turn.
    """
    normalized = acceptance.build_state(catalog, plan, current_results(ledger, catalog, plan, source_sha256), source_sha256)
    passed = {s['id'] for r in normalized['requirements'] for s in r['scenarios']
              if s['status'] == 'TEST_PASSED'}
    def priority(scenario):
        entry = ledger['scenarios'].get(scenario['id'], {})
        latest = entry.get('latest')
        changed = latest and latest.get('definition_sha256') and latest['definition_sha256'] != definition_digest(scenario)
        return (0 if not latest or changed else 1, entry.get('last_attempt_round', -1), scenario['id'])
    pending = [s for s in plan['scenarios'] if s['id'] not in passed]
    selected = sorted(pending, key=priority)[:max(0, min(max_scenarios, acceptance.MAX_SCENARIOS_PER_ROUND))]
    return {'schema_version': 1, 'spec_sha256': catalog['spec_sha256'],
            'catalog_ids': [r['id'] for r in catalog['requirements']], 'scenarios': selected}


def record_results(ledger, catalog, plan, state, round_number, source_sha256):
    """Record only executions present in this normalized verifier state.

    Callers must pass fresh results, not merged historical state, so carried
    evidence never masquerades as another execution.
    """
    ledger = copy.deepcopy(ledger) if ledger.get('spec_sha256') == catalog['spec_sha256'] else _empty(catalog)
    _entries(ledger, plan)
    by_id = {s['id']: s for s in plan['scenarios']}
    evidence = {'spec_sha256': state.get('spec_sha256'), 'scenarios':
                [s for r in state.get('requirements', []) for s in r.get('scenarios', []) if 'test_status' in s]}
    normalized = acceptance.build_state(catalog, plan, evidence, source_sha256, round_number)
    seen = set()
    for requirement in normalized['requirements']:
        for result in requirement['scenarios']:
            sid = result['id']
            if sid in seen or 'test_status' not in result:
                continue
            seen.add(sid)
            _insert(ledger, by_id[sid], result, round_number, source_sha256, definition_digest(by_id[sid]))
    ledger['backfilled_through_round'] = max(round_number, ledger['backfilled_through_round'])
    return ledger


def progress_summary(ledger, catalog, plan, source_sha256):
    current = acceptance.build_state(catalog, plan, current_results(ledger, catalog, plan, source_sha256), source_sha256)
    scenario_current = {s['id'] for r in current['requirements'] for s in r['scenarios'] if s['status'] == 'TEST_PASSED'}
    by_id = {s['id']: s for s in plan['scenarios']}
    ever = {sid for sid, entry in ledger['scenarios'].items() if entry.get('last_pass')}
    tested = {sid for sid, entry in ledger['scenarios'].items() if entry.get('latest')}
    requirement_ever = sum((not plan.get('_coverage_required') or plan.get('_coverage',{}).get(r['id'],{}).get('complete',False)
                           or plan.get('_coverage',{}).get(r['id'],{}).get('historically_complete',False))
                           and bool(r.get('scenario_ids')) and all(sid in ever and r['id'] in
                           ledger['scenarios'][sid]['last_pass']['result']['requirement_ids']
                           for sid in r['scenario_ids'])
                           for r in plan['requirements'])
    known = {r['id'] for r in catalog['requirements']}
    requirement_tested = {rid for sid in tested
                          for rid in ledger['scenarios'][sid]['latest']['result']['requirement_ids'] if rid in known}
    return {'scenarios_total': len(plan['scenarios']), 'scenarios_ever_tested': len(tested),
            'scenarios_ever_passed': len(ever), 'scenarios_current_passed': len(scenario_current),
            'scenarios_stale': len(ever - scenario_current),
            'scenarios_unseen': len(set(by_id) - tested),
            'scenarios_pending_current': len(set(by_id) - scenario_current),
            'requirements_ever_tested': len(requirement_tested), 'requirements_ever_passed': requirement_ever,
            'requirements_current_passed': current['summary']['test_passed']}


def decorate_state(state, catalog, plan, ledger, source_sha256):
    """Expose historical progress separately from verified current-source results."""
    by_id = {s['id']: s for s in plan['scenarios']}
    current_evidence = current_results(ledger, catalog, plan, source_sha256)['scenarios']
    attempted = {s['id'] for s in current_evidence}
    summary = progress_summary(ledger, catalog, plan, source_sha256)
    state['history_summary'] = {'test_passed': summary['requirements_ever_passed'],
        'tested': summary['requirements_ever_tested'],
        'scenarios_ever_passed':summary['scenarios_ever_passed'],
        'scenarios_current_passed':summary['scenarios_current_passed'],
        'latest_round': max((e.get('last_attempt_round', 0) for e in ledger['scenarios'].values()), default=0)}
    state['queue_summary'] = {'pending_current': len(set(by_id) - attempted),
        'unseen': summary['scenarios_unseen'], 'total': len(by_id),
        'current_tested': len(attempted),
        'failed_current': sum(s.get('status') == 'FAIL' for s in current_evidence)}
    for requirement in state['requirements']:
        for result in requirement['scenarios']:
            if result['id'] in attempted:
                continue
            record = ledger['scenarios'].get(result['id'], {}).get('last_pass')
            scenario = by_id.get(result['id'])
            if (record and scenario and record.get('definition_sha256') == definition_digest(scenario)
                    and record['result'].get('requirement_ids') == scenario['requirement_ids']):
                result.update(copy.deepcopy(record['result']))
                result.update(status='STALE', detail='Passed on an earlier source revision; recheck required.')
        statuses = [s['status'] for s in requirement['scenarios']]
        if statuses and all(s in ('TEST_PASSED', 'STALE') for s in statuses) and 'STALE' in statuses:
            requirement.update(status='STALE', detail='Earlier successful checks require revalidation.')
    counts = {status: sum(r['status'] == status for r in state['requirements'])
              for status in ('TEST_PASSED', 'FAIL', 'NOT_TESTED', 'STALE')}
    state['summary'].update(test_passed=counts['TEST_PASSED'], failed=counts['FAIL'],
                            not_tested=counts['NOT_TESTED'], stale=counts['STALE'])
    from common.coverage import gate
    gate(state,plan)
    return state
