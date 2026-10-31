"""Public correction records and targeted reads; no filesystem mutations."""
from copy import deepcopy
import json
import re
import uuid

from common import pipeline_contracts as contracts

IDENTIFIER=re.compile(r'C-[a-f0-9]{32}\Z')


def validate(record):
    if (not isinstance(record,dict) or record.get('contract')!='CorrectionRecord'
            or record.get('version')!='v1' or not IDENTIFIER.fullmatch(str(record.get('correction_id','')))
            or record.get('schema_name') not in contracts.contract_names()
            or record.get('outcome') not in ('rejected','accepted_format','accepted_controller')
            or record.get('layer') not in ('api_format','controller_validation')
            or not isinstance(record.get('issues'),list) or not isinstance(record.get('resolves'),list)
            or any(not IDENTIFIER.fullmatch(str(key)) for key in record['resolves'])
            or 'submission' not in record):
        raise ValueError('Invalid public correction record')
    json.dumps(record,allow_nan=False)
    return record


def record(name,submission,issues,*,outcome='rejected',layer='api_format',resolves=()):
    try:json.dumps(submission,allow_nan=False)
    except (ValueError,TypeError):submission=json.dumps(submission,ensure_ascii=False,allow_nan=True)
    return validate({'contract':'CorrectionRecord','version':'v1',
        'correction_id':'C-'+uuid.uuid4().hex,'schema_name':name,'outcome':outcome,'layer':layer,
        'submission':deepcopy(submission),'issues':deepcopy(issues),'resolves':list(resolves),
        'meaning':'Format/controller acceptance only. This is not semantic approval or independent test PASS.'})


def compact_feedback(full,record_id=None):
    """Group repetition, retaining exact locations; full details stay on demand."""
    groups={}
    for issue in full.get('issues',[]):
        pattern=re.sub(r'\[\d+\]','[*]',issue.get('path','$'))
        key=(pattern,issue.get('code'),issue.get('expected'),issue.get('received_type'))
        if key not in groups:
            groups[key]={k:deepcopy(v) for k,v in issue.items() if k!='instruction'}
            groups[key].update(pattern=pattern,paths=[],count=0)
            if issue.get('code')=='json_syntax':groups[key]['example_json']=None
        groups[key]['paths'].append(issue.get('path','$'));groups[key]['count']+=1
    return {'contract':'ContractFeedback','version':'v1','schema_name':full.get('schema_name'),
        'attempt':full.get('attempt'),'correction_id':record_id,'issues':list(groups.values()),
        'issue_count':len(full.get('issues',[])),
        'instruction':'Correct only the listed fields; keep intended decisions and existing edits. '
            'Use read_correction for exact issues, rejected field values and accepted examples. '
            'Full trusted format is available through read_output_format. Do not reread the project to fix JSON types.',
        'schema_path':full.get('schema_path'),'example_path':full.get('example_path')}


def select(value,path):
    """Tiny JSON field selector, not Python evaluation or a filesystem path."""
    if path in ('','$'):return value
    if not isinstance(path,str) or not re.fullmatch(r'\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*',path):
        raise ValueError('Use a JSON path such as $.scope.round or $.findings[0].code_refs')
    current=value
    for field,index in re.findall(r'\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]',path[1:]):
        try:current=current[int(index)] if index else current[field]
        except (KeyError,IndexError,TypeError):raise ValueError('Field does not exist in this saved submission') from None
    return current


def page(value,offset=0,limit=12000):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=16000:
        raise ValueError('offset >= 0 and limit between 1 and 16000 characters required')
    raw=json.dumps(value,ensure_ascii=False,indent=2)
    end=min(len(raw),offset+limit)
    result={'value':value if offset==0 and len(raw)<=limit else None,
        'json_fragment':raw[offset:end] if offset or len(raw)>limit else None,
        'offset':offset,'next_offset':end if end<len(raw) else None,'total_characters':len(raw),
        'truncated':offset>0 or end<len(raw),
        'notice':'Read the next offset if needed. The complete saved record remains available.'}
    # Escaping a JSON fragment can double its serialized size. Keep the whole
    # envelope below the executor's tool-result limit, including continuation.
    while len(json.dumps(result,ensure_ascii=False))>30000:
        end=offset+max(1,(end-offset)//2)
        result.update(value=None,json_fragment=raw[offset:end],next_offset=end if end<len(raw) else None,
                      truncated=True)
    return result


class Journal:
    def __init__(self,emit=None,read=None):
        self.records={};self.sources={};self.emit=emit;self.reader=read;self.pending=[]

    def add(self,value):
        validate(value);self.records[value['correction_id']]=deepcopy(value)
        if value['outcome']=='rejected':self.pending.append(value['correction_id'])
        if self.emit:self.emit(value)
        return value

    def reject(self,name,submission,feedback):
        return self.add(record(name,submission,feedback.get('issues',[])))

    def accept(self,name,submission):
        if not self.pending:return None
        value=self.add(record(name,submission,[],outcome='accepted_format',resolves=self.pending))
        self.pending=[]
        return value

    def get(self,key):
        if not IDENTIFIER.fullmatch(str(key)):raise ValueError('Invalid correction ID')
        if key in self.records:return self.records[key]
        if key not in self.sources or self.reader is None:raise ValueError('Unknown correction ID')
        payload=self.reader(self.sources[key]['payload_ref'])
        value=payload.get('correction') if isinstance(payload,dict) else None
        validate(value)
        if value['correction_id']!=key:raise ValueError('Correction source ID mismatch')
        return value

    def read(self,key,section,path='$',offset=0,limit=12000):
        if section=='index':
            rows=[{**{k:v for k,v in item.items() if k!='payload_ref'}} for item in self.sources.values()]
            rows += [{'correction_id':v['correction_id'],'schema_name':v['schema_name'],
                'outcome':v['outcome'],'layer':v['layer'],'issue_count':len(v['issues']),'resolves':v['resolves']}
                for v in self.records.values() if v['correction_id'] not in self.sources]
            return page(rows,offset,limit)
        value=self.get(key)
        if section=='submission':answer=select(value['submission'],path)
        elif section in ('issues','example'):
            answer=[issue for issue in value['issues'] if path in ('','$') or issue.get('path','').startswith(path)]
            if section=='example':answer=[{key:issue.get(key) for key in ('path','expected','example_json')} for issue in answer]
        elif section=='resolution':
            # Follow explicit links only; format acceptance is not test success.
            related=[]
            for candidate in list(self.sources)+[k for k in self.records if k not in self.sources]:
                meta=self.records.get(candidate) or self.sources[candidate]
                if key in meta.get('resolves',[]):related.append(self.get(candidate))
            answer=related
        elif section=='summary':answer={k:v for k,v in value.items() if k not in ('submission','issues')}
        else:raise ValueError('Unknown correction section')
        return page(answer,offset,limit)
