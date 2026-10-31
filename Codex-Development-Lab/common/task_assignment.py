"""Persistent controller-owned focus; planning checkpoints never replace proof."""
from __future__ import annotations

import hashlib
import copy
import json
import os
from pathlib import Path
import tempfile
import re

from common import acceptance, acceptance_progress as progress, acceptance_tasks as tasks
from common import manual_review
from common.history import _open

FILENAME = 'task-assignment.json'
MAX_BYTES = 8 * 1024 * 1024


def validated_ledger(job_root, ledger, plan):
    """Validate artifact bytes before advancing or skipping a cached PASS."""
    safe = copy.deepcopy(ledger)
    for scenario, key in ((s, key) for s in plan['scenarios'] for key in ('latest', 'last_pass')):
        record = safe.get('scenarios', {}).get(scenario['id'], {}).get(key)
        if not record or record['result'].get('status') != 'TEST_PASSED':
            continue
        result = record['result']
        folder = result.get('artifact_round', '')
        base = 'acceptance/' + scenario['id'] + '/'
        proof = result.get('assertion_evidence') or {}
        reports = proof.get('reports', []) if isinstance(proof, dict) else []
        available = bool(reports) and bool(re.fullmatch(r'round-[0-9]{2,16}', str(folder)))
        try:
            for report in reports:
                path = report.get('file', '') if isinstance(report, dict) else ''
                digest = report.get('sha256', '') if isinstance(report, dict) else ''
                if (not isinstance(path, str) or not path.startswith(base + 'assertions/')
                        or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
                    available = False
                    break
                with _open(job_root, (folder, *path.split('/'))) as handle:
                    if (os.fstat(handle.fileno()).st_size > 4 * 1024 * 1024
                            or hashlib.sha256(handle.read(4 * 1024 * 1024 + 1)).hexdigest() != digest):
                        available = False
                        break
            if available and scenario['kind'] == 'gameplay':
                video = base + 'minecraft_client_capture.mp4'
                if result.get('video') != video:
                    available = False
                else:
                    with _open(job_root, (folder, *video.split('/'))) as handle:
                        available = os.fstat(handle.fileno()).st_size > 0
        except (OSError, ValueError, TypeError):
            available = False
        if not available:
            result.update(status='NOT_TESTED', detail='Evidence files missing or changed; recheck required.')
    return safe


def _binding(task, plan):
    members = set(task['requirement_ids'])
    definitions = sorted((s['id'], progress.definition_digest(s)) for s in plan['scenarios']
                         if members.intersection(s['requirement_ids']))
    coverage_binding=[(rid,plan.get('_coverage',{}).get(rid,{}).get('revision'),
                       plan.get('_coverage',{}).get(rid,{}).get('complete',False)) for rid in sorted(members)] if plan.get('_coverage_required') else []
    return hashlib.sha256(json.dumps([sorted(members), definitions,coverage_binding],
                                    separators=(',', ':')).encode()).hexdigest()


def _read(root, catalog):
    try:
        value = acceptance.read_json(root, FILENAME, MAX_BYTES)
        if (not isinstance(value, dict) or value.get('schema_version') != 1
                or value.get('spec_sha256') != catalog['spec_sha256']
                or not isinstance(value.get('completed_bindings'), dict)
                or len(value['completed_bindings']) > acceptance.MAX_ITEMS
                or any(not isinstance(k, str) or not isinstance(v, str) or len(v) != 64
                       for k, v in value['completed_bindings'].items())
                or not isinstance(value.get('current_task_id'), (str, type(None)))):
            return {}
        return value
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        return {}


def _save(root, value):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Task assignment root must be a real directory')
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('Task assignment exceeds size limit')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix='.task-assignment-', delete=False) as handle:
            temporary = handle.name
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / FILENAME)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def read(job_root, catalog):
    """Read the controller snapshot without changing selection or job state."""
    return _read(job_root, catalog)


def _resolved(entry, scenario, failure_round):
    passed = entry.get('last_pass') or {}
    latest = entry.get('latest') or {}
    definition = progress.definition_digest(scenario)
    if (passed.get('definition_sha256') != definition
            or (passed.get('result') or {}).get('status') != 'TEST_PASSED'):
        return False
    return (passed.get('round', -1) > failure_round or
            (passed.get('round') == failure_round and latest.get('round') == failure_round
             and latest.get('definition_sha256') == definition
             and (latest.get('result') or {}).get('status') == 'TEST_PASSED'))


def unresolved_failures(ledger, plan, requirement_ids=None, *, limit=25):
    """Keep independent failures until a later exact-definition verified pass.

    An unsuccessful/nonexecuted recheck and a changed source cannot erase an
    error. Historical records carry bounded details; full logs stay in artifacts.
    """
    allowed = set(requirement_ids) if requirement_ids is not None else None
    failures = []
    for scenario in plan['scenarios']:
        entry = ledger.get('scenarios', {}).get(scenario['id'], {})
        latest = entry.get('latest') or {}
        result = latest.get('result') or {}
        events = [e for e in entry.get('history', []) if e.get('status') == 'FAIL']
        if result.get('status') == 'FAIL' or result.get('test_status') in ('FAIL', 'ERROR', 'TIMEOUT'):
            events.append({**latest, 'detail': result.get('detail', '')})
        if not events:
            continue
        failure = max(events, key=lambda item: item.get('round', 0))
        if _resolved(entry, scenario, failure.get('round', 0)):
            continue
        members = result.get('requirement_ids') or scenario['requirement_ids']
        if allowed is not None and not allowed.intersection(members):
            continue
        same_failure = latest.get('round') == failure.get('round')
        failures.append({'id': scenario['id'], 'requirement_ids': members,
                         'round': failure.get('round', 0),
                         'source_sha256': failure.get('source_sha256', ''),
                         'needs_recheck': True,
                         'detail': str(failure.get('detail', result.get('detail', '')))[:500],
                         'command_tail': str(result.get('command_tail', ''))[-1200:] if same_failure else ''})
    return sorted(failures, key=lambda item: (-item['round'], item['id']))[:limit]


def _failure_memory(old, ledger, plan):
    """Preserve unresolved errors beyond the ledger's short event history."""
    known = {s['id']: s for s in plan['scenarios']}
    memory = {}
    saved = old.get('failure_memory', {})
    if isinstance(saved, dict):
        for sid, row in list(saved.items())[:acceptance.MAX_ITEMS]:
            if (sid in known and isinstance(row, dict) and row.get('id') == sid
                    and type(row.get('round')) is int and row['round'] >= 0
                    and isinstance(row.get('requirement_ids'), list)
                    and len(row['requirement_ids']) <= 64
                    and all(isinstance(rid, str) and len(rid) <= 128 for rid in row['requirement_ids'])):
                memory[sid] = {**row, 'detail': str(row.get('detail', ''))[:500],
                               'command_tail': str(row.get('command_tail', ''))[-1200:]}
    for row in unresolved_failures(ledger, plan, limit=acceptance.MAX_ITEMS):
        if row['round'] >= memory.get(row['id'], {}).get('round', -1):
            memory[row['id']] = row
    for sid in list(memory):
        if _resolved(ledger.get('scenarios', {}).get(sid, {}), known[sid], memory[sid]['round']):
            del memory[sid]
    return memory


def update(job_root, catalog, plan, ledger, source_sha256, round_number=0):
    """Retain one task through edits/resumes; advance only on independent proof."""
    from common.coverage import attach
    attach(plan,catalog,job_root,source_sha256)
    ledger = validated_ledger(job_root, ledger, plan)
    state = acceptance.build_state(catalog, plan,
        progress.current_results(ledger, catalog, plan, source_sha256), source_sha256, round_number)
    progress.decorate_state(state, catalog, plan, ledger, source_sha256)
    tasks.annotate(state, catalog)
    groups = state['tasks']
    by_id = {group['id']: group for group in groups}
    deferred = set(manual_review.pending_ids(plan))
    leaves = {leaf['id']: leaf for leaf in state['requirements']}
    automatic_members = {group['id']: [rid for rid in group['requirement_ids'] if rid not in deferred]
                         for group in groups}
    automatic_groups = {tid for tid, members in automatic_members.items() if members}
    automatic_passed = {tid for tid in automatic_groups
                        if all(leaves[rid]['status'] == 'TEST_PASSED' for rid in automatic_members[tid])}
    old = _read(job_root, catalog)
    failures = _failure_memory(old, ledger, plan)
    completed = {tid: binding for tid, binding in old.get('completed_bindings', {}).items()
                 if tid in by_id and binding == _binding(by_id[tid], plan)}
    # A mixed group's automatic coverage is a regression checkpoint, never a
    # completed task. Its human-only conditions stay unverified in the catalog.
    saved_automatic = old.get('automatable_bindings', {})
    if not isinstance(saved_automatic, dict):saved_automatic = {}
    automatic = {tid: binding for tid, binding in saved_automatic.items()
                 if tid in automatic_groups and binding == _binding(by_id[tid], plan)}
    passed = {group['id'] for group in groups if group['status'] == 'TEST_PASSED'}
    current = old.get('current_task_id')
    reason = 'retained_current_task'
    if current not in automatic_groups:
        current = None
    for tid in passed:
        if tid != current:
            completed[tid] = _binding(by_id[tid], plan)
    for tid in automatic_passed:
        if tid != current:
            automatic[tid] = _binding(by_id[tid], plan)
    checkpoints = set(completed) | set(automatic)
    regression = [rid for tid in checkpoints for rid in automatic_members[tid]]
    if (current in automatic_passed and all(tid in automatic_passed for tid in checkpoints)
            and not any(set(row['requirement_ids']).intersection(
                regression + automatic_members[current]) for row in failures.values())):
        automatic[current] = _binding(by_id[current], plan)
        if current in passed:
            completed[current] = _binding(by_id[current], plan)
        current = None
        reason = ('automatable_conditions_verified_manual_review_pending' if deferred
                  else 'current_task_and_previous_tasks_verified')
    if current is None:
        candidates = [group for group in groups if group['id'] in automatic_groups
                      and group['id'] not in completed and group['id'] not in automatic
                      and group['id'] not in automatic_passed]
        upcoming = progress.select_request(catalog, plan, ledger, source_sha256)['scenarios']
        for scenario in upcoming:
            current = next((group['id'] for group in candidates
                            if set(group['requirement_ids']).intersection(scenario['requirement_ids'])), None)
            if current:
                break
        if not current and candidates:
            current = candidates[0]['id']
        if current and reason == 'retained_current_task':
            reason = 'first_pending_scenario_or_uncovered_requirement'
        if not current:
            current = next((group['id'] for group in groups if group['id'] in automatic_groups
                            and group['id'] not in automatic_passed), None)
            if current:
                reason = 'previous_task_requires_regression_recheck'
    focus = dict(by_id[current]) if current else None
    if focus:
        focus['unresolved_requirement_ids'] = [rid for rid in automatic_members[current]
                                               if leaves[rid]['status'] != 'TEST_PASSED']
    checkpoints = set(completed) | set(automatic)
    regression = [rid for group in groups if group['id'] in checkpoints and group['id'] != current
                  for rid in automatic_members[group['id']]]
    all_automatic_verified = (bool(deferred) and not failures
        and all(leaf['status'] == 'TEST_PASSED' for rid, leaf in leaves.items() if rid not in deferred))
    status = ('all_verified_requires_review' if groups and len(passed) == len(groups)
              else 'all_automatable_verified_requires_review' if all_automatic_verified
              else 'regression_verification' if focus and not focus['unresolved_requirement_ids']
              else 'active' if focus else 'no_requirements')
    value = {'schema_version': 1, 'spec_sha256': catalog['spec_sha256'],
             'source_sha256': source_sha256, 'round': round_number,
             'current_task_id': current, 'current_task': focus,
             'completed_task_ids': list(completed), 'completed_bindings': completed,
             'automatable_bindings': automatic,
             'manual_review_requirement_ids': [r['id'] for r in catalog['requirements'] if r['id'] in deferred],
             'manual_review_requirement_count': len(deferred),
             'regression_requirement_ids': regression,
             'pending_task_count': sum(tid not in automatic_passed for tid in automatic_groups),
             'selection_reason': reason, 'status': status,
             'failure_memory': failures,
             'unresolved_failure_count': len(failures),
             'unresolved_failures': sorted(failures.values(), key=lambda row: (-row['round'], row['id']))[:25]}
    _save(job_root, value)
    return value


def request(catalog, plan, ledger, source_sha256, assignment, max_scenarios=4, *, job_root=None,
            round_actions=None):
    """Check the selected implementation first, then focus and older regressions.

    ``round_actions`` is the controller-validated round package, not workspace
    completion claims. It only orders pending checks; all acceptance proof and
    the normal batch bound remain unchanged.
    """
    if job_root is not None:
        ledger = validated_ledger(job_root, ledger, plan)
    limit = max(0, min(max_scenarios, acceptance.MAX_SCENARIOS_PER_ROUND))
    pending = progress.select_request(catalog, plan, ledger, source_sha256,
                                      max_scenarios=acceptance.MAX_SCENARIOS_PER_ROUND)
    # select_request is normally capped at four; filter the plan before sorting
    # so unrelated unseen scenarios cannot hide the focus's pending scenarios.
    focus = set((assignment.get('current_task') or {}).get('requirement_ids', []))
    regression = set(assignment.get('regression_requirement_ids', []))
    normalized = acceptance.build_state(catalog, plan,
        progress.current_results(ledger, catalog, plan, source_sha256), source_sha256)
    passed = {s['id'] for r in normalized['requirements'] for s in r['scenarios']
              if s['status'] == 'TEST_PASSED'}
    def priority(scenario):
        entry = ledger.get('scenarios', {}).get(scenario['id'], {})
        latest = entry.get('latest')
        unseen = not latest or latest.get('definition_sha256') != progress.definition_digest(scenario)
        return (0 if unseen else 1, entry.get('last_attempt_round', -1), scenario['id'])
    def selected(members):
        filtered = {**plan, 'scenarios': [s for s in plan['scenarios']
                                        if members.intersection(s['requirement_ids'])]}
        # Full plan is used for verdict normalization; a temporary catalog subset
        # would accidentally weaken multi-scenario requirement conjunctions.
        return sorted((s for s in filtered['scenarios'] if s['id'] not in passed), key=priority)

    observed_failure_ids = {failure.get('id') for failure in assignment.get('unresolved_failures', [])
                            if isinstance(failure, dict)}
    eligible = {s['id']: s for s in plan['scenarios']
                if s['id'] not in passed and ((focus | regression).intersection(s['requirement_ids'])
                                            or s['id'] in observed_failure_ids)}
    priorities = []
    seen_priority = set()
    action_candidates = []
    for action in round_actions or []:
        if not isinstance(action, dict):
            continue
        candidates = []
        if action.get('kind') in ('repair_scenario', 'implement_or_verify_scenario', 'diagnose_failure'):
            candidates.append(action.get('target'))
        explicit = action.get('verification_scenario_ids', [])
        if isinstance(explicit, list):
            candidates.extend(explicit)
        if action.get('kind') == 'implement_and_map_requirement':
            conditions=action.get('condition_ids',[])
            if conditions:
                requirement=next((r for r in plan['requirements'] if r['id']==action.get('target')), {})
                condition_scenarios={sid for condition in conditions
                    for sid in requirement.get('obligation_scenarios',{}).get(condition,[])}
                candidates.extend(s['id'] for s in sorted(eligible.values(), key=priority)
                                  if s['id'] in condition_scenarios)
            else:
                candidates.extend(s['id'] for s in sorted(eligible.values(), key=priority)
                                  if action.get('target') in s['requirement_ids'])
        action_candidates.append([sid for sid in candidates if isinstance(sid, str) and sid in eligible])
    # Give each package member a check before one large requirement takes all
    # slots. Exact repaired scenarios lead their member; explicit checks lead
    # generic requirement mappings. Dependency order is the action-list order.
    while any(action_candidates):
        for candidates in action_candidates:
            while candidates and candidates[0] in seen_priority:
                candidates.pop(0)
            if candidates:
                sid = candidates.pop(0)
                seen_priority.add(sid)
                priorities.append(eligible[sid])
    chosen = priorities[:limit]
    chosen_ids = {s['id'] for s in chosen}
    chosen.extend(s for s in selected(focus) if s['id'] not in chosen_ids)
    chosen = chosen[:limit]
    if len(chosen) < limit:
        seen = {s['id'] for s in chosen}
        remaining = [s for s in selected(regression) if s['id'] not in seen]
        chosen.extend(remaining[:1 if chosen else limit])
    chosen_ids = {s['id'] for s in chosen}
    return {**pending, 'scenarios': chosen,
            'prioritized_scenario_ids': [s['id'] for s in priorities if s['id'] in chosen_ids],
            'omitted_priority_scenario_ids': [s['id'] for s in priorities if s['id'] not in chosen_ids]}
