"""Atomic in-memory catalog changes. No filesystem writes or model calls."""
from copy import deepcopy
import hashlib
import json

from common.pipeline_contracts import validate_submission
from common.task_graph import validate_catalog


def content_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def apply(catalog,patch,base_ref,read_source=None):
    validate_submission('CatalogPatch',patch)
    if patch['base_catalog_ref']!=base_ref:
        raise ValueError('Stale CatalogPatch: base_catalog_ref must match the supplied exact snapshot')
    for key in ('project_id','spec_revision'):
        if patch['scope'][key]!=catalog['scope'][key]:raise ValueError('CatalogPatch belongs to another project/specification')
    result=deepcopy(catalog)
    for field,prefix in (('requirements','requirements'),('conditions','conditions')):
        original={item['id']:item for item in result[field]}
        updates=patch[prefix+'_upsert'];removes=patch[prefix+'_remove']
        ids=[item['id'] for item in updates]
        if len(ids)!=len(set(ids)) or len(removes)!=len(set(removes)):
            raise ValueError('Duplicate patch operation IDs in '+field)
        if set(ids)&set(removes):raise ValueError('Cannot upsert and remove the same '+field+' ID')
        if not set(removes)<=set(original):raise ValueError('Cannot remove unknown '+field+' IDs')
        for key in removes:del original[key]
        for item in updates:original[item['id']]=deepcopy(item)
        result[field]=list(original.values())
    if len(patch['questions_add'])!=len(set(patch['questions_add'])):
        raise ValueError('Duplicate questions_add entries')
    if set(patch['questions_add'])&set(result['open_questions']):
        raise ValueError('Question already exists; update its resolution, not its history key')
    result['open_questions']+=patch['questions_add']
    resolutions={item['question']:item for item in result.get('question_resolutions',[])}
    keys=[item['question'] for item in patch['question_resolutions_upsert']]
    if len(keys)!=len(set(keys)):raise ValueError('Duplicate question resolution updates')
    for item in patch['question_resolutions_upsert']:resolutions[item['question']]=deepcopy(item)
    if resolutions or 'question_resolutions' in result:result['question_resolutions']=list(resolutions.values())
    refs={content_hash(item):item for item in result['unclassified_source_refs']}
    for field in ('unclassified_add','unclassified_remove'):
        keys=[content_hash(item) for item in patch[field]]
        if len(keys)!=len(set(keys)):raise ValueError('Duplicate source reference patch operations')
    added={content_hash(item) for item in patch['unclassified_add']}
    removed={content_hash(item) for item in patch['unclassified_remove']}
    if added&removed:raise ValueError('Cannot add/remove the same source reference')
    if not removed<=set(refs):raise ValueError('Cannot remove unknown unclassified source')
    for key in removed:del refs[key]
    for item in patch['unclassified_add']:refs[content_hash(item)]=deepcopy(item)
    result['unclassified_source_refs']=list(refs.values())
    validate_catalog(result,read_source)
    return result


def impact(before,after):
    """Changed conditions plus siblings of touched requirements/questions."""
    touched=set();conditions=set();removed=set()
    old={r['id']:r for r in before['requirements']};new={r['id']:r for r in after['requirements']}
    touched.update(key for key in old.keys()|new.keys() if old.get(key)!=new.get(key))
    oldc={c['id']:c for c in before['conditions']};newc={c['id']:c for c in after['conditions']}
    for key in oldc.keys()|newc.keys():
        if oldc.get(key)!=newc.get(key):
            if key in newc:conditions.add(key);touched.add(newc[key]['requirement_id'])
            if key in oldc:touched.add(oldc[key]['requirement_id'])
            if key not in newc:removed.add(key)
    oldq={q['question']:q for q in before.get('question_resolutions',[])}
    for q in after.get('question_resolutions',[]):
        if oldq.get(q['question'])!=q:
            conditions.update(q['condition_ids'])
            conditions.update(oldq.get(q['question'],{}).get('condition_ids',[]))
    conditions.update(c['id'] for c in after['conditions'] if c['requirement_id'] in touched)
    if before['unclassified_source_refs']!=after['unclassified_source_refs']:
        conditions.update(newc)
    return {'condition_ids':sorted(conditions & newc.keys()),'requirement_ids':sorted(touched),
            'removed_condition_ids':sorted(removed)}


def graph_impact(before,after):
    old={t['id']:t for t in before['tasks']};new={t['id']:t for t in after['tasks']}
    changed={key for key in old.keys()|new.keys() if old.get(key)!=new.get(key)}
    # Both the previous and the new dependent chains matter when edges move.
    while True:
        expanded=changed|{t['id'] for t in list(old.values())+list(new.values()) if set(t['depends_on'])&changed}
        if expanded==changed:break
        changed=expanded
    return sorted({cid for key in changed for cid in new.get(key,{}).get('condition_ids',[])})
