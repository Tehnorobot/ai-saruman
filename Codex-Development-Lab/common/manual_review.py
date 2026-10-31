"""Explicit human checks; annotations never establish or replace test evidence.

Only an unmapped ``manual`` requirement can be deferred to a user. Missing
automation, a failed build, and provider failures do not establish that a human
check is necessary. No status, count, or evidence is written by this module.
"""
from __future__ import annotations

import re


CATEGORIES = frozenset(('visual_judgment', 'physical_device', 'external_access', 'user_environment'))
_TECHNICAL_FAILURE = re.compile(
    r'HTTP\s*429|provider[_ -]?error|insufficient remaining task token budget|'
    r'build failed|compilation (?:failed|error)|compile(?:r)? error|'
    r'ошибк[аиу]\s+(?:компиляции|сборки|провайдера)|'
    r'ошибка\s+тест[а-я]*|тест[а-я]*\s+(?:пада[а-я]*|не\s+проход[а-я]*)',
    re.IGNORECASE,
)


def _text(value, name, minimum, maximum):
    if not isinstance(value, str):
        raise ValueError('Manual review ' + name + ' must be text')
    value = value.strip()
    if not minimum <= len(value) <= maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('Invalid manual review ' + name)
    return value


def validate(value):
    """Normalize a bounded public instruction, or reject invalid annotations."""
    if not isinstance(value, dict):
        raise ValueError('Manual review must be an object')
    category = value.get('category')
    if not isinstance(category, str) or category not in CATEGORIES:
        raise ValueError('Unsupported manual review category')
    reason = _text(value.get('reason'), 'reason', 32, 1000)
    blocker = _text(value.get('automation_blocker'), 'automation_blocker', 32, 1000)
    if _TECHNICAL_FAILURE.search(reason + '\n' + blocker):
        raise ValueError('Technical or provider failures require repair, not manual deferral')
    steps = value.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise ValueError('Manual review requires one to twelve steps')
    return {'category': category, 'reason': reason, 'automation_blocker': blocker,
            'steps': [_text(step, 'step', 1, 500) for step in steps],
            'expected_result': _text(value.get('expected_result'), 'expected_result', 8, 1000)}


def _reviews(plan):
    if not isinstance(plan, dict) or not isinstance(plan.get('requirements'), list):
        return {}
    result = {}
    duplicates = set()
    seen = set()
    for item in plan['requirements']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        rid = item['id']
        if rid in seen:
            duplicates.add(rid)
        seen.add(rid)
        if item.get('kind') != 'manual' or item.get('scenario_ids') != []:
            continue
        try:
            result[rid] = validate(item.get('manual_review'))
        except ValueError:
            continue
    # Explicit executable scenario links cannot be hidden by a manual annotation,
    # even if a malformed plan omitted the reciprocal requirement mapping.
    scenarios = plan.get('scenarios', [])
    if isinstance(scenarios, list):
        for scenario in scenarios:
            refs = scenario.get('requirement_ids') if isinstance(scenario, dict) else None
            if isinstance(refs, list):
                duplicates.update(rid for rid in refs if isinstance(rid, str))
    return {rid: value for rid, value in result.items() if rid not in duplicates}


def pending_ids(plan):
    """Return only explicitly justified, unmapped manual IDs; never default kinds."""
    return list(_reviews(plan))


def decorate(state, plan):
    """Add human instructions in place without modifying verifier verdicts."""
    reviews = _reviews(plan)
    requirements = state.get('requirements') if isinstance(state, dict) else None
    if isinstance(requirements, list):
        for item in requirements:
            if isinstance(item, dict) and item.get('id') in reviews:
                item['manual_review'] = reviews[item['id']]
                item['review_status'] = 'USER_TEST_REQUIRED'
    return state
