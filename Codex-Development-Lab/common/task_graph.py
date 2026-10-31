"""Deterministic links and DAG checks for LLM-authored functional tasks.

These functions check declared relationships, not the meaning/completeness of a
natural-language specification. A reviewer must inspect the source separately.
"""
from __future__ import annotations

from collections import deque
import hashlib
from pathlib import PurePosixPath

from common.pipeline_contracts import ContractError, validate


def issue(path, code, expected, instruction=None):
    return {'path':path, 'code':code, 'expected':expected, 'received_type':'cross_reference',
            'example_json':'null', 'instruction':instruction or 'Correct the declared links using the accepted catalog and graph.'}


def _raise(name, errors):
    if errors: raise ContractError(name, errors)


def _duplicates(values, path, errors):
    seen = set()
    for n, value in enumerate(values):
        if value in seen:
            errors.append(issue(path + '[' + str(n) + ']', 'duplicate_id', 'unique IDs in this list'))
        seen.add(value)
    return seen


def _same_spec(left, right, errors):
    for key in ('project_id', 'spec_revision'):
        if left['scope'][key] != right['scope'][key]:
            errors.append(issue('$.scope.' + key, 'stale_scope', 'the same accepted project and specification revision'))


def _safe_artifact(artifact, path, errors):
    name = artifact['path']
    parts = PurePosixPath(name).parts
    if ('\\' in name or ':' in name or '\x00' in name or name.startswith('/')
            or not parts or any(p in ('', '.', '..') for p in name.split('/'))):
        errors.append(issue(path + '.path', 'unsafe_path', 'a relative forward-slash file path inside the project'))
        return False
    return True


def _source(ref, path, read_source, errors):
    if not _safe_artifact(ref['artifact'], path + '.artifact', errors): return
    if ref['end_line'] < ref['start_line']:
        errors.append(issue(path + '.end_line', 'line_range', 'end_line >= start_line'))
        return
    if read_source is None: return
    try:
        content = read_source(ref['artifact'])
        if isinstance(content, str): content = content.encode('utf-8')
        if not isinstance(content, bytes): raise TypeError('source callback must return bytes or text')
        if hashlib.sha256(content).hexdigest() != ref['artifact']['sha256']:
            errors.append(issue(path + '.artifact.sha256', 'source_hash', 'SHA-256 of the exact cited source bytes'))
            return
        lines = content.decode('utf-8').splitlines()
    except (OSError, ValueError, TypeError, UnicodeError):
        errors.append(issue(path + '.artifact', 'source_unavailable', 'a readable UTF-8 source supplied by the trusted reader'))
        return
    if ref['end_line'] > len(lines):
        errors.append(issue(path + '.end_line', 'line_range', 'an existing source line'))
    elif ref['excerpt'] not in '\n'.join(lines[ref['start_line'] - 1:ref['end_line']]):
        errors.append(issue(path + '.excerpt', 'source_excerpt', 'literal source text within the declared inclusive line range'))


def validate_catalog(catalog, read_source=None):
    """Validate IDs, bidirectional condition ownership and optional real citations.

    read_source(ArtifactRef) returns bytes (preferred, for exact hashes) or UTF-8
    text. The caller owns confinement and which source files are authoritative.
    Without a reader this performs structural/link checks only.
    """
    validate('RequirementsCatalog', catalog)
    errors = []
    reqs = catalog['requirements']
    conds = catalog['conditions']
    req_ids = _duplicates([r['id'] for r in reqs], '$.requirements', errors)
    cond_ids = _duplicates([c['id'] for c in conds], '$.conditions', errors)
    condition_owner = {c['id']:c['requirement_id'] for c in conds}
    if not reqs:
        errors.append(issue('$.requirements', 'empty_catalog', 'at least one source-backed requirement; unresolved questions remain explicit'))
    for n, req in enumerate(reqs):
        base = '$.requirements[' + str(n) + ']'
        _duplicates(req['condition_ids'], base + '.condition_ids', errors)
        if not req['source_refs']:
            errors.append(issue(base + '.source_refs', 'missing_source', 'at least one literal source reference'))
        if not req['condition_ids']:
            errors.append(issue(base + '.condition_ids', 'missing_condition', 'at least one observable condition, automated or manual'))
        for cid in req['condition_ids']:
            if cid not in cond_ids:
                errors.append(issue(base + '.condition_ids', 'unknown_condition', 'IDs from catalog.conditions'))
            elif condition_owner[cid] != req['id']:
                errors.append(issue(base + '.condition_ids', 'condition_owner', 'conditions owned by this requirement'))
        for i, source in enumerate(req['source_refs']):
            _source(source, base + '.source_refs[' + str(i) + ']', read_source, errors)
    lookup = {r['id']:r for r in reqs}
    for n, condition in enumerate(conds):
        base = '$.conditions[' + str(n) + ']'
        if condition['requirement_id'] not in req_ids:
            errors.append(issue(base + '.requirement_id', 'unknown_requirement', 'an ID from catalog.requirements'))
        elif condition['id'] not in lookup[condition['requirement_id']]['condition_ids']:
            errors.append(issue(base + '.id', 'unlinked_condition', 'the condition listed by its parent requirement'))
    for i, source in enumerate(catalog['unclassified_source_refs']):
        _source(source, '$.unclassified_source_refs[' + str(i) + ']', read_source, errors)
    questions = set(catalog['open_questions'])
    handled = set()
    duplicates={}
    for i, resolution in enumerate(catalog.get('question_resolutions', [])):
        base = '$.question_resolutions[' + str(i) + ']'
        if resolution['question'] not in questions:
            errors.append(issue(base+'.question', 'question_link', 'one exact question from open_questions'))
        if resolution['question'] in handled:
            duplicates.setdefault(resolution['question'],[]).append(i)
        handled.add(resolution['question'])
        if not set(resolution['condition_ids']) <= cond_ids:
            errors.append(issue(base+'.condition_ids', 'unknown_condition', 'IDs from catalog.conditions'))
        if not resolution['source_refs']:
            errors.append(issue(base+'.source_refs', 'missing_source', 'literal normative clauses grounding this question/decision'))
        for n, ref in enumerate(resolution['source_refs']):
            _source(ref, base+'.source_refs['+str(n)+']', read_source, errors)
        if resolution['status']=='resolved' and not resolution['answer']:
            errors.append(issue(base+'.answer', 'missing_answer', 'a concrete recorded resolution, never an invented user reply'))
        if resolution['status']=='open' and (not resolution['condition_ids'] or resolution['answer'] is not None):
            errors.append(issue(base, 'open_question', 'an unanswered question linked to affected conditions'))
        if resolution['kind']=='implementation_decision' and (resolution['owner']!='planner' or resolution['status']!='resolved'):
            errors.append(issue(base, 'engineering_decision', 'a recorded planner decision; not a pending question to the user'))
        if resolution['kind']=='user_clarification' and resolution['owner']!='user':
            errors.append(issue(base+'.owner', 'question_owner', 'user for a genuine normative ambiguity'))
        if resolution['kind']=='experiment' and resolution['owner']!='coder':
            errors.append(issue(base+'.owner', 'question_owner', 'coder for an executable investigation'))
    if duplicates:
        groups=[[i for i,r in enumerate(catalog['question_resolutions']) if r['question']==q] for q in duplicates]
        errors.append(issue('$.question_resolutions','duplicate_question_resolution',
            'one resolution per question; duplicate index groups: '+str(groups),
            'Replace this affected array atomically: remove identical repeats or merge useful facts and evidence. '
            'Resolve conflicting answers explicitly from trusted evidence; do not silently discard them. '
            'Retain every distinct valid question and its affected conditions. Other catalog fields must stay unchanged. '
            'The original records remain in correction history.'))
    _raise('RequirementsCatalog', errors)
    return catalog


def pending_questions(catalog):
    """Legacy unanswered strings stay pending until explicitly reviewed."""
    resolved={r['question'] for r in catalog.get('question_resolutions',[]) if r['status']=='resolved'}
    return [q for q in catalog['open_questions'] if q not in resolved]


def topological_order(tasks):
    """Return task IDs in dependency order, or None when cyclic/unknown."""
    ids = {t['id'] for t in tasks}
    incoming = {t['id']:0 for t in tasks}
    outgoing = {t['id']:[] for t in tasks}
    for task in tasks:
        for dep in set(task['depends_on']):
            if dep not in ids: return None
            incoming[task['id']] += 1
            outgoing[dep].append(task['id'])
    ready = deque(task['id'] for task in tasks if not incoming[task['id']])
    ordered = []
    while ready:
        current = ready.popleft()
        ordered.append(current)
        for other in outgoing[current]:
            incoming[other] -= 1
            if not incoming[other]: ready.append(other)
    return ordered if len(ordered) == len(tasks) else None


def validate_graph(graph, catalog):
    validate_catalog(catalog)
    validate('TaskGraph', graph)
    errors = []
    _same_spec(graph, catalog, errors)
    tasks = graph['tasks']
    task_ids = _duplicates([t['id'] for t in tasks], '$.tasks', errors)
    reqs = {r['id']:r for r in catalog['requirements']}
    conds = {c['id']:c for c in catalog['conditions']}
    covered_reqs, covered_conds = set(), set()
    if not tasks: errors.append(issue('$.tasks', 'empty_graph', 'a nonempty compact functional task graph'))
    for n, task in enumerate(tasks):
        base = '$.tasks[' + str(n) + ']'
        for field in ('requirement_ids', 'condition_ids', 'depends_on'):
            _duplicates(task[field], base + '.' + field, errors)
        if not task['requirement_ids'] or not task['condition_ids']:
            errors.append(issue(base, 'missing_traceability', 'at least one requirement and observable condition per functional task'))
        for rid in task['requirement_ids']:
            if rid not in reqs: errors.append(issue(base + '.requirement_ids', 'unknown_requirement', 'IDs from the accepted requirements catalog'))
            else: covered_reqs.add(rid)
        for cid in task['condition_ids']:
            if cid not in conds: errors.append(issue(base + '.condition_ids', 'unknown_condition', 'IDs from the accepted conditions catalog'))
            elif conds[cid]['requirement_id'] not in task['requirement_ids']:
                errors.append(issue(base + '.condition_ids', 'condition_owner', 'each condition parent included in this task requirement_ids'))
            else: covered_conds.add(cid)
        for dep in task['depends_on']:
            if dep not in task_ids: errors.append(issue(base + '.depends_on', 'unknown_dependency', 'existing task IDs'))
            elif dep == task['id']: errors.append(issue(base + '.depends_on', 'self_dependency', 'another task; a task cannot depend on itself'))
    if covered_reqs != set(reqs): errors.append(issue('$.tasks', 'uncovered_requirements', 'all accepted requirements linked to functional tasks'))
    if covered_conds != set(conds): errors.append(issue('$.tasks', 'uncovered_conditions', 'all accepted conditions linked to functional tasks'))
    if topological_order(tasks) is None: errors.append(issue('$.tasks', 'dependency_cycle', 'an acyclic graph with known dependency IDs'))
    _raise('TaskGraph', errors)
    return graph


def _states(value):
    if isinstance(value, dict):
        return {k: v.get('status') if isinstance(v, dict) else v for k, v in value.items()}
    return {s['task_id']:s['status'] for s in (value or [])}


def validate_decision(decision, graph, catalog, task_states=None):
    """Validate an adaptive package; no group pinning or fixed action quota.

    Feature work needs prerequisites verified, or scheduled earlier in this same
    package. Diagnostics may inspect blocked tasks without pretending a feature
    dependency is already implemented. Explicit action dependency IDs must always
    refer to an earlier action; declaring them never proves an execution result.
    """
    validate_graph(graph, catalog)
    validate('PlannerDecision', decision)
    errors = []
    _same_spec(decision, catalog, errors)
    tasks = {t['id']:t for t in graph['tasks']}
    states = _states(task_states)
    actions = decision['actions']
    _duplicates([a['id'] for a in actions], '$.actions', errors)
    earlier_actions, earlier_tasks = set(), set()
    if not actions and decision['strategy'] not in ('wait', 'manual'):
        errors.append(issue('$.actions', 'empty_package', 'a useful work or diagnostic action; only explained wait/manual may omit actions'))
    for n, action in enumerate(actions):
        base = '$.actions[' + str(n) + ']'
        for field in ('task_ids', 'condition_ids', 'depends_on_action_ids'):
            _duplicates(action[field], base + '.' + field, errors)
        if not action['task_ids']:
            errors.append(issue(base + '.task_ids', 'missing_task', 'at least one existing functional task'))
        selected = [tasks[t] for t in action['task_ids'] if t in tasks]
        for tid in action['task_ids']:
            if tid not in tasks: errors.append(issue(base + '.task_ids', 'unknown_task', 'IDs from the accepted task graph'))
        allowed_conditions = {cid for task in selected for cid in task['condition_ids']}
        for cid in action['condition_ids']:
            if cid not in allowed_conditions:
                errors.append(issue(base + '.condition_ids', 'unrelated_condition', 'conditions belonging to the selected tasks'))
        for dep in action['depends_on_action_ids']:
            if dep not in earlier_actions:
                errors.append(issue(base + '.depends_on_action_ids', 'action_order', 'dependency action IDs occurring earlier in this package'))
        if action['kind'] not in ('diagnose', 'manual'):
            # Tasks listed together in an action can be implemented in their
            # graph order; no extra per-file actions need to be invented.
            bundled = set(action['task_ids'])
            for task in selected:
                for dep in task['depends_on']:
                    if states.get(dep) != 'verified' and dep not in earlier_tasks and dep not in bundled:
                        errors.append(issue(base + '.task_ids', 'unsatisfied_dependency',
                            'prerequisite verified or selected before/with dependent work in this package; task '+task['id']+' requires '+dep,
                            'Correct the action package: include '+dep+' before/with '+task['id']+' or select independent work. Do not erase a real graph dependency.'))
        earlier_actions.add(action['id'])
        if action['kind'] not in ('diagnose', 'manual'):
            earlier_tasks.update(action['task_ids'])
    deferred_ids = _duplicates([t['task_id'] for t in decision['deferred']], '$.deferred', errors)
    for n, deferred in enumerate(decision['deferred']):
        if deferred['task_id'] not in tasks:
            errors.append(issue('$.deferred[' + str(n) + '].task_id', 'unknown_task', 'IDs from the accepted task graph'))
    if deferred_ids & {t for a in actions for t in a['task_ids']}:
        errors.append(issue('$.deferred', 'scheduled_and_deferred', 'a task either selected or deferred for this package'))
    condition_ids = {c['id'] for c in catalog['conditions']}
    if any(cid not in condition_ids for cid in decision['regression_condition_ids']):
        errors.append(issue('$.regression_condition_ids','unknown_condition','existing condition IDs from the accepted catalog'))
    for n, manual in enumerate(decision['manual_checks']):
        if not manual['steps']:
            errors.append(issue('$.manual_checks['+str(n)+'].steps','missing_manual_instructions','at least one concrete user action with an expected result'))
        if not manual['condition_ids'] or any(cid not in condition_ids for cid in manual['condition_ids']):
            errors.append(issue('$.manual_checks[' + str(n) + '].condition_ids', 'unknown_condition', 'nonempty existing condition IDs'))
    _raise('PlannerDecision', errors)
    return decision


def validate_scenarios(manifest, catalog, graph):
    """Check traceability and explicit one-case-per-checkpoint JUnit bindings.

    This does not establish that the assertion body tests its claimed meaning.
    Read-only review and independent execution remain required.
    """
    validate_graph(graph, catalog)
    validate('ScenarioManifest', manifest)
    errors = []
    _same_spec(manifest, catalog, errors)
    tasks = {task['id']:task for task in graph['tasks']}
    all_conditions = {condition['id'] for condition in catalog['conditions']}
    scenarios = manifest['scenarios']
    _duplicates([s['id'] for s in scenarios], '$.scenarios', errors)
    for n, scenario in enumerate(scenarios):
        base = '$.scenarios[' + str(n) + ']'
        for field in ('task_ids', 'condition_ids', 'media'):
            _duplicates(scenario[field], base + '.' + field, errors)
        if not scenario['task_ids'] or any(t not in tasks for t in scenario['task_ids']):
            errors.append(issue(base + '.task_ids', 'unknown_task', 'nonempty task IDs from the accepted graph'))
        allowed_conditions = {cid for tid in scenario['task_ids'] if tid in tasks for cid in tasks[tid]['condition_ids']}
        if not scenario['condition_ids'] or any(cid not in allowed_conditions for cid in scenario['condition_ids']):
            errors.append(issue(base + '.condition_ids', 'unrelated_condition', 'nonempty conditions of the selected tasks'))
        checkpoints = scenario['checkpoints']
        cp_ids = _duplicates([cp['id'] for cp in checkpoints], base + '.checkpoints', errors)
        if not checkpoints:
            errors.append(issue(base + '.checkpoints', 'missing_checkpoint', 'observable checkpoints for this scenario'))
        asserted = set()
        for i, cp in enumerate(checkpoints):
            _duplicates(cp['condition_ids'], base + '.checkpoints[' + str(i) + '].condition_ids', errors)
            for cid in cp['condition_ids']:
                if cid not in all_conditions or cid not in scenario['condition_ids']:
                    errors.append(issue(base + '.checkpoints[' + str(i) + '].condition_ids', 'unrelated_condition', 'conditions explicitly declared by this scenario'))
                else:
                    asserted.add(cid)
        if asserted != set(scenario['condition_ids']):
            errors.append(issue(base + '.checkpoints', 'unasserted_conditions', 'every declared condition linked to an observable checkpoint'))
        expected = scenario['execution']['expected_tests']
        expected_pairs = [(case['classname'],case['name']) for case in expected]
        _duplicates(expected_pairs, base + '.execution.expected_tests', errors)
        if not expected:
            errors.append(issue(base + '.execution.expected_tests', 'missing_test', 'nonempty expected JUnit case identities'))
        bindings = scenario['execution']['checkpoint_tests']
        bound_ids = _duplicates([b['checkpoint_id'] for b in bindings], base + '.execution.checkpoint_tests', errors)
        if bound_ids != cp_ids:
            errors.append(issue(base + '.execution.checkpoint_tests', 'checkpoint_mapping', 'exactly one binding for every existing checkpoint'))
        pairs = [(b['classname'],b['name']) for b in bindings]
        _duplicates(pairs, base + '.execution.checkpoint_tests', errors)
        if any(pair not in expected_pairs for pair in pairs):
            errors.append(issue(base + '.execution.checkpoint_tests', 'unknown_test', 'case identities listed in execution.expected_tests'))
    _raise('ScenarioManifest', errors)
    return manifest
