"""Compare-and-set JSON repairs. Never writes files or infers model decisions."""
from copy import deepcopy
import hashlib
import json
import re

from common import pipeline_contracts as contracts
from common.correction_journal import select, page


class PatchFieldError(ValueError):
    """A failed read carries navigation data, never fabricated field values."""
    def __init__(self,diagnostic):
        self.diagnostic=diagnostic
        super().__init__(json.dumps(diagnostic,ensure_ascii=False))


def read_field(base,name,args,repair_context=None):
    """Read immutable answer, schema or trusted repair context; no file access."""
    context=repair_context or {}
    path=args.get('path','$');section=args.get('section','value')
    try:
        if section=='format':value=field_format(name,path)
        elif section=='value':value=select(base,path)
        elif section=='context':value=select(context,path)
        else:raise ValueError('Unknown patch read section')
    except (ValueError,KeyError,IndexError,TypeError) as error:
        raise PatchFieldError({'error':'Patch field is unavailable','detail':str(error),
            'requested_path':path,'section':section,
            'available_sections':['value','format','context'],
            'allowed_patch_paths':context.get('allowed_paths',[]),
            'value_root_fields':list(base) if isinstance(base,dict) else [],
            'context_root_fields':list(context) if isinstance(context,dict) else [],
            'instruction':'value reads the saved answer, format reads its schema, context reads trusted repair inputs. '
                'For available tasks/dependencies/condition IDs use section="context", path="$.task_index". '
                'Use path="$" to inspect the selected section with paging; do not repeat the same missing path.'}) from error
    return page(value,args.get('offset',0),args.get('limit',12000))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def parts(path):
    if not isinstance(path,str) or not re.fullmatch(r'\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])+',path):
        raise ValueError('Patch path must name a JSON field; root replacement is forbidden')
    return [int(index) if index else field for field,index in
            re.findall(r'\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]',path[1:])]


def allowed_paths(issues):
    paths=[]
    for issue in issues:
        path=issue.get('path','$')
        # Older persisted feedback attached a duplicate relationship error to
        # its question string. Array replacement is required to remove rows.
        if (issue.get('code')=='question_link' and 'unique' in issue.get('expected','')
                and re.fullmatch(r'\$\.question_resolutions\[\d+\]\.question',path)):
            path='$.question_resolutions'
        if issue.get('code')=='required' and issue.get('expected','').startswith('required fields: '):
            paths.extend(path+'.'+key.strip() for key in issue['expected'][17:].split(','))
        elif path!='$':paths.append(path)
    paths=list(dict.fromkeys(paths))
    return [p for p in paths if not any(p!=a and (p.startswith(a+'.') or p.startswith(a+'[')) for a in paths)]


def loads(raw):
    def reject(value):raise ValueError('Nonfinite JSON values are forbidden: '+value)
    value=json.loads(raw,parse_constant=reject)
    fingerprint(value)
    return value


def apply(base,patch,allowed):
    try:return _apply(base,patch,allowed)
    except (KeyError,IndexError,TypeError) as error:raise ValueError('Patch path does not exist or has the wrong parent type') from error


def _apply(base,patch,allowed):
    contracts.validate_submission('SubmissionPatch',patch)
    if patch['submission_sha256']!=fingerprint(base):raise ValueError('Stale patch base digest')
    changes=patch['changes'];paths=[c['path'] for c in changes]
    if len(paths)!=len(set(paths)):raise ValueError('Duplicate patch paths')
    for path in paths:
        parts(path)
        if not any(path==a or path.startswith(a+'.') or path.startswith(a+'[') for a in allowed):
            raise ValueError('Patch changes a field outside listed errors: '+path)
        if any(other!=path and (other.startswith(path+'.') or other.startswith(path+'[')) for other in paths):
            raise ValueError('Overlapping patch paths')
    result=deepcopy(base)
    # All preconditions refer to the original document, not earlier changes.
    for change in changes:
        tokens=parts(change['path']);parent=base
        for token in tokens[:-1]:parent=parent[token]
        key=tokens[-1]
        exists=(0<=key<len(parent)) if isinstance(parent,list) and type(key) is int else key in parent if isinstance(parent,dict) else False
        if exists!=change['expected_exists']:raise ValueError('Patch existence precondition failed: '+change['path'])
        old=loads(change['expected_old_json'])
        if exists and fingerprint(parent[key])!=fingerprint(old):raise ValueError('Patch old value mismatch: '+change['path'])
        if not exists and old is not None:raise ValueError('Missing fields require expected_old_json=null')
        if change['operation']=='remove' and not exists:raise ValueError('Cannot remove a missing field')
        if isinstance(parent,list) and change['operation']=='remove':raise ValueError('Replace an affected array instead of shifting its indices')
        if isinstance(parent,list) and not exists:raise ValueError('Replace an affected array to add entries')
        new=loads(change['new_json'])
        if change['operation']=='remove' and new is not None:raise ValueError('Removal requires new_json=null')
    for change in changes:
        tokens=parts(change['path']);parent=result
        for token in tokens[:-1]:parent=parent[token]
        if change['operation']=='remove':del parent[tokens[-1]]
        else:parent[tokens[-1]]=loads(change['new_json'])
    if any(p=='$.question_resolutions' or p.startswith('$.question_resolutions[') for p in paths):
        array=lambda v:v if isinstance(v,list) else []
        known={q for q in array(base.get('open_questions')) if isinstance(q,str)}
        previous=[r for r in array(base.get('question_resolutions')) if isinstance(r,dict) and isinstance(r.get('question'),str)]
        required={r.get('question') for r in previous if r.get('question') in known}
        current=result.get('question_resolutions',[])
        if not isinstance(current,list):raise ValueError('Question resolutions must remain an array')
        if not required<={r.get('question') for r in current if isinstance(r,dict) and isinstance(r.get('question'),str)}:
            raise ValueError('Array repair must retain every distinct valid question; remove repeats, not unique decisions')
        valid_conditions={c['id'] for c in array(base.get('conditions')) if isinstance(c,dict) and isinstance(c.get('id'),str)}
        for question in required:
            before={cid for r in previous if r.get('question')==question for cid in array(r.get('condition_ids')) if isinstance(cid,str) and cid in valid_conditions}
            after={cid for r in current if isinstance(r,dict) and r.get('question')==question for cid in array(r.get('condition_ids')) if isinstance(cid,str)}
            if not before<=after:raise ValueError('Array repair must preserve affected conditions for question: '+question)
    return result


def field_format(name,path):
    shape=contracts.format_guide(name)['shape']
    if path=='$':return shape
    for token in parts(path):shape=shape['item'] if type(token) is int else shape['fields'][token]
    return shape


def context(base,issues,name=None):
    fields=[]
    allowed=allowed_paths(issues)
    for path in allowed:
        try:value=select(base,path);exists=True
        except (ValueError,KeyError,IndexError,TypeError):value=None;exists=False
        fields.append({'path':path,'exists':exists,'saved_value':page(value,limit=1500)})
        if name:
            try:fields[-1]['expected_format']=page(field_format(name,path),limit=1500)
            except (ValueError,KeyError,TypeError):pass
    compact=[]
    for item in issues:
        item=deepcopy(item)
        if len(item.get('example_json',''))>1500 or item.get('path')=='$':
            item['example_json']=None
            item['example_notice']='Full issue remains in correction journal; exact field format is available with read_patch_field(section="format").'
        compact.append(item)
    result={'submission_sha256':fingerprint(base),'allowed_paths':allowed,'issues':compact,'fields':fields,
        'instruction':'Return SubmissionPatch. Change only listed fields; preserve every other field. '
            'Values in expected_old_json/new_json are JSON strings (e.g. "12", "null", or "[1,2]"). '
            'Read an exact saved field if its preview is paged. Do not analyze source code or reread project history. '
            'read_patch_field section=value reads this saved answer; section=format reads its format; '
            'section=context reads the trusted correction inputs, including $.task_index for tasks, dependencies and condition IDs. '
            'The task index is read-only context, never a field to add to the answer. '
            'The trusted controller validates the entire merged result; this patch does not prove test PASS.'}
    if '$.question_resolutions' in allowed:
        result['instruction']+=' Replace only the affected question_resolutions array to remove repeated records or merge their facts/evidence. Keep each distinct valid question and its condition links. Conflicting decisions need an explicit evidence-based resolution; originals remain in history.'
        rows=base.get('question_resolutions',[])
        rows=[r for r in rows if isinstance(r,dict) and isinstance(r.get('question'),str)] if isinstance(rows,list) else []
        result['duplicate_resolution_groups']=[{'question':q,'indices':[i for i,r in enumerate(rows) if r.get('question')==q]}
            for q in dict.fromkeys(r.get('question') for r in rows)
            if sum(r.get('question')==q for r in rows)>1]
    return result
