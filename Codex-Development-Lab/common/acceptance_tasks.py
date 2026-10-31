"""Controller-derived work packages; source requirements and proof stay unchanged.

Use H2 boundaries for ordinary sections. Split large sections at their existing
H3 boundaries so a single integration chapter cannot hide most of the work.
The threshold controls grouping only; it never drops requirements or assertions.
"""
from __future__ import annotations

from collections import Counter
import hashlib


SPLIT_SECTION_SIZE = 32
STATUSES = ('TEST_PASSED', 'FAIL', 'STALE', 'NOT_TESTED', 'IN_PROGRESS')
SUMMARY_KEYS = {'TEST_PASSED': 'test_passed', 'FAIL': 'failed',
                'STALE': 'stale', 'NOT_TESTED': 'not_tested',
                'IN_PROGRESS': 'in_progress'}


def _path(requirement):
    section = requirement.get('section', '')
    return tuple(section.split(' / ')) if section else ()


def build_tasks(catalog):
    """Return an exact, deterministic partition of the immutable source REQs.

    IDs depend on section boundaries, not verdicts, execution order or model
    descriptions. A changed source is still checked through its original REQ
    IDs and spec hash by annotate(). No file is read or written here.
    """
    requirements = catalog.get('requirements', [])
    counts = Counter(_path(item)[:2] for item in requirements)
    grouped = {}
    for item in requirements:
        path = _path(item)
        parent = path[:2]
        key = path[:3] if len(path) > 2 and counts[parent] > SPLIT_SECTION_SIZE else parent
        if key not in grouped:
            section = ' / '.join(key)
            # JSON-like length delimiters prevent ambiguous concatenation.
            identity = ''.join('%d:%s' % (len(part), part) for part in key)
            digest = hashlib.sha256(('acceptance-task-v1:' + identity).encode('utf-8')).hexdigest()[:16]
            grouped[key] = {'id': 'task-' + digest, 'kind': 'group',
                'title': ' / '.join(key[1:]) if len(key) > 1 else (section or 'Общие требования'),
                'section': section, 'requirement_ids': []}
        grouped[key]['requirement_ids'].append(item['id'])
    return list(grouped.values())


def _status(statuses):
    if statuses and all(value == 'TEST_PASSED' for value in statuses):
        return 'TEST_PASSED'
    if 'FAIL' in statuses:
        return 'FAIL'
    if statuses and all(value == 'NOT_TESTED' for value in statuses):
        return 'NOT_TESTED'
    if 'STALE' in statuses and all(value in ('STALE', 'TEST_PASSED') for value in statuses):
        return 'STALE'
    return 'IN_PROGRESS'


def annotate(state, catalog):
    """Add task-level views without changing source requirements or verdicts.

    Only matching-spec, unique, known leaf records can contribute a verdict.
    Missing records and malformed statuses remain unverified. Historical passes
    have no effect on current completion. Proof validation belongs to the
    independent acceptance verifier which produced the leaf records.
    """
    leaves = {}
    duplicates = set()
    known = {item['id'] for item in catalog.get('requirements', [])}
    if state.get('spec_sha256') == catalog.get('spec_sha256'):
        for item in state.get('requirements', []):
            if not isinstance(item, dict) or item.get('id') not in known:
                continue
            rid = item['id']
            if rid in leaves:
                duplicates.add(rid)
            leaves[rid] = item
    for rid in duplicates:
        leaves.pop(rid, None)
    tasks = build_tasks(catalog)
    for task in tasks:
        statuses = []
        scenario_ids = set()
        for rid in task['requirement_ids']:
            leaf = leaves.get(rid, {})
            value = leaf.get('status', 'NOT_TESTED')
            statuses.append(value if value in STATUSES else 'NOT_TESTED')
            scenario_ids.update(sid for sid in leaf.get('scenario_ids', []) if isinstance(sid, str))
            scenario_ids.update(scenario['id'] for scenario in leaf.get('scenarios', [])
                if isinstance(scenario, dict) and isinstance(scenario.get('id'), str))
        task['scenario_ids'] = sorted(scenario_ids)
        task['status'] = _status(statuses)
        task['summary'] = {'total': len(statuses), **{
            summary_key: statuses.count(status) for status, summary_key in SUMMARY_KEYS.items()}}
    state['tasks'] = tasks
    state['task_summary'] = {'total': len(tasks), **{
        summary_key: sum(task['status'] == status for task in tasks)
        for status, summary_key in SUMMARY_KEYS.items()}}
    return state
