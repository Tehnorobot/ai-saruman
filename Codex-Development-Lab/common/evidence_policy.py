"""Explicit controller-owned exclusions for optional capture-only conditions.

No keyword classifier: a gameplay condition mentioning video must remain a
functional requirement. An exclusion is pinned to a reviewed condition hash.
Original catalogs/questions remain in immutable memory, never marked PASS.
"""
from copy import deepcopy
import hashlib
import json


def definition(condition):
    return hashlib.sha256(json.dumps(condition,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def project(catalog,graph,policy):
    if not policy or policy.get('spec_revision')!=catalog['scope']['spec_revision']:return catalog,graph,[]
    pinned=policy.get('supplemental_conditions',{})
    excluded=[c for c in catalog['conditions'] if pinned.get(c['id'])==definition(c)]
    if not excluded:return catalog,graph,[]
    ids={c['id'] for c in excluded};result=deepcopy(catalog)
    result['conditions']=[c for c in result['conditions'] if c['id'] not in ids]
    for requirement in result['requirements']:requirement['condition_ids']=[c for c in requirement['condition_ids'] if c not in ids]
    result['requirements']=[r for r in result['requirements'] if r['condition_ids']]
    removed_questions=set()
    kept=[]
    for question in result.get('question_resolutions',[]):
        if question['condition_ids'] and set(question['condition_ids'])<=ids:
            removed_questions.add(question['question']);continue
        question['condition_ids']=[c for c in question['condition_ids'] if c not in ids];kept.append(question)
    result['question_resolutions']=kept
    result['open_questions']=[q for q in result['open_questions'] if q not in removed_questions]
    if graph is None:return result,None,excluded
    updated=deepcopy(graph);requirements={r['id'] for r in result['requirements']}
    for task in updated['tasks']:
        task['condition_ids']=[c for c in task['condition_ids'] if c not in ids]
        task['requirement_ids']=[r for r in task['requirement_ids'] if r in requirements]
    updated['tasks']=[t for t in updated['tasks'] if t['condition_ids']]
    tasks={t['id'] for t in updated['tasks']}
    old={t['id']:t for t in graph['tasks']}
    def prerequisites(key,seen):
        if key in seen:return []
        if key in tasks:return [key]
        return [parent for dependency in old.get(key,{}).get('depends_on',[]) for parent in prerequisites(dependency,seen|{key})]
    for task in updated['tasks']:
        task['depends_on']=list(dict.fromkeys(parent for dependency in task['depends_on'] for parent in prerequisites(dependency,{task['id']})))
    return result,updated,excluded
