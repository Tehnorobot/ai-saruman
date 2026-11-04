"""Small model-facing messages; canonical storage and verifier contracts stay v1.

Only the controller hydrates metadata from actual inputs/files. Markdown is
opaque public text, never a command, a new requirement or verification proof.
"""
from copy import deepcopy
import json

TARGETS={'AgentPlan':'PlannerDecision','AgentReview':'ReviewResult','AgentReport':'CoderReport',
         'AgentGraph':'TaskGraph','AgentCatalog':'RequirementsCatalog','AgentCatalogPatch':'CatalogPatch',
         'AgentScenarios':'ScenarioManifest'}
WIRES={v:k for k,v in TARGETS.items()}
OWNED={'RequirementsCatalog':[], 'TaskGraph':['catalog_ref'],
       'CatalogPatch':['base_catalog_ref'], 'ScenarioManifest':[]}


def wire_name(name):return WIRES.get(name,name)


def compact_schema(name,canonical):
    """Metadata-free forms for data the controller must route/execute."""
    target=TARGETS[name]
    def obj(props):return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}
    text={'type':'string'};strings={'type':'array','items':text}
    def array(item):return {'type':'array','items':item}
    def prop(key):return deepcopy(canonical['properties'][key])
    head={'contract':{'type':'string','const':name},'version':{'type':'string','const':'v1'}}
    if name=='AgentPlan':
        action=obj({'kind':deepcopy(canonical['$defs']['Action']['properties']['kind']),
            'task_ids':strings,'condition_ids':strings,'goal':text,'expected_observation':text})
        schema=obj({**head,'strategy':prop('strategy'),'actions':array(action),'notes_md':text,
            'regression_condition_ids':prop('regression_condition_ids'),'deferred':prop('deferred'),
            'manual_checks':prop('manual_checks'),'request_graph_change':{'type':'boolean'},
            'external_updates':array(obj({'task_id':text,'state':{'type':'string','enum':['blocked','resolved']},
                'reason':{'type':'string','minLength':1},'return_when':{'type':'string','minLength':1}}))})
    elif name=='AgentReview':
        finding=canonical['$defs']['Finding']['properties']
        schema=obj({**head,'disposition':prop('disposition'),'reviewed_condition_ids':strings,
            'findings':array(obj({'kind':deepcopy(finding['kind']),'severity':deepcopy(finding['severity']),
                'requirement_ids':strings,'notes_md':text})), 'notes_md':text})
    elif name=='AgentReport':
        schema=obj({**head,'scenario_manifest_path':text,'notes_md':text,
            'responses':array(obj({'finding_id':text,'disposition':{'type':'string','enum':['fixed','rejected_with_reason','unverified']},'notes_md':text})),
            'unverified_claims':strings,'open_issues':strings})
    else:
        schema=deepcopy(canonical)
        schema['properties']={**head,**{k:v for k,v in schema['properties'].items()
            if k not in ('contract','version','scope',*OWNED.get(target,[]))}}
        schema['properties']['notes_md']=schema['properties'].pop('rationale',text)
        schema['required']=list(schema['properties'])
    schema['$defs']=deepcopy(canonical.get('$defs',{}))
    # A file reference on the wire is its path. Hash and MIME come from bytes.
    if 'ArtifactRef' in schema['$defs']:schema['$defs']['ArtifactRef']={'type':'string','minLength':1}
    if 'SourceRef' in schema['$defs']:
        schema['$defs']['SourceRef']=obj({'path':{'type':'string','minLength':1},
            'start_line':{'type':'integer','minimum':1},'end_line':{'type':'integer','minimum':1}})
    if name=='AgentScenarios':
        scenario=schema['$defs']['Scenario']
        scenario['properties'].pop('media',None)
        scenario['required']=[key for key in scenario['required'] if key!='media']
    # Drop unused definitions: compact tool schemas must not carry legacy prose
    # structures and full recursive canonical contracts into every API request.
    needed=set()
    def refs(node):
        if isinstance(node,dict):
            ref=node.get('$ref','')
            if ref.startswith('#/$defs/'):
                key=ref[8:]
                if key not in needed:needed.add(key);refs(schema['$defs'][key])
            for key,value in node.items():
                if key!='$defs':refs(value)
        elif isinstance(node,list):
            for value in node:refs(value)
    refs(schema)
    schema['$defs']={k:v for k,v in schema['$defs'].items() if k in needed}
    schema['title']=name+' - compact agent message'
    return schema


def _expand(value,node,root,artifact,read):
    ref=node.get('$ref','')
    if ref=='#/$defs/ArtifactRef':return artifact(value)
    if ref=='#/$defs/SourceRef':
        reference=artifact(value['path']);lines=read(reference).decode('utf-8-sig').splitlines()
        start,end=value['start_line'],value['end_line']
        if not 1<=start<=end<=len(lines):raise ValueError('Source range outside file: '+value['path'])
        return {'artifact':reference,'start_line':start,'end_line':end,'excerpt':'\n'.join(lines[start-1:end])}
    if ref:return _expand(value,root['$defs'][ref[8:]],root,artifact,read)
    if isinstance(value,list):return [_expand(v,node.get('items',{}),root,artifact,read) for v in value]
    if isinstance(value,dict):return {k:_expand(v,node.get('properties',{}).get(k,{}),root,artifact,read) for k,v in value.items()}
    return deepcopy(value)


def hydrate(value,*,scope,inputs,artifact,read,write,changed_files=(),archive=None):
    from common import pipeline_contracts as contracts
    name=value.get('contract');target=TARGETS[name]
    if name=='AgentScenarios':
        # Stored proposals predate optional fixture metadata. Provider strict
        # schemas require explicit empty arrays, while runtime contracts keep
        # these additions backward compatible. Normalize only these defaults;
        # never repair identities, conditions or test evidence here.
        value=deepcopy(value)
        for scenario in value.get('scenarios',[]):
            execution=scenario.get('execution',{})
            prerequisites=execution.setdefault('precondition_tests',[])
            if isinstance(prerequisites,list):
                for prerequisite in prerequisites:
                    if isinstance(prerequisite,dict):prerequisite.setdefault('checkpoint_ids',[])
    contracts.validate_submission(name,value)
    head={'contract':target,'version':'v1','scope':deepcopy(scope)}
    notes=value.get('notes_md','')
    if name=='AgentPlan':
        actions=[]
        for index,item in enumerate(value['actions']):
            actions.append({**deepcopy(item),'id':'A'+str(index+1),
                'depends_on_action_ids':[] if index==0 else ['A'+str(index)]})
        return {**head,'strategy':value['strategy'],'rationale':notes or 'See the selected work package.',
            'actions':actions,'hypotheses':[],'deferred':deepcopy(value['deferred']),
            'manual_checks':deepcopy(value['manual_checks']),'regression_condition_ids':value['regression_condition_ids'],
            'replacement_graph_ref':inputs['graph_ref'] if value['request_graph_change'] else None}
    if name=='AgentReview':
        return {**head,'request_ref':inputs['context_ref'],'disposition':value['disposition'],
            'reviewed_condition_ids':value['reviewed_condition_ids'],'evidence_refs':[],
            'unknowns':[notes] if notes else [],'findings':[{'id':'F'+str(i+1),'kind':f['kind'],
                'severity':f['severity'],'requirement_ids':f['requirement_ids'],'code_refs':[],
                'observation':f['notes_md'] or 'See reviewer report.',
                'consequence':'See reviewer notes.','suggested_check':'See reviewer notes and executable scenarios.'}
                for i,f in enumerate(value['findings'])]}
    if name=='AgentReport':
        assignment=inputs['assignment'];plan=json.loads(read(assignment['plan_ref']))
        manifest_ref=artifact(value['scenario_manifest_path']);manifest=json.loads(read(manifest_ref))
        if manifest.get('contract')=='AgentScenarios':
            if archive:archive(manifest)
            manifest=hydrate(manifest,scope=scope,inputs={},artifact=artifact,read=read,write=write,archive=archive)
            manifest_ref=write('scenarios-normalized.json',manifest)
        response_refs=[]
        if value['responses']:
            if not inputs.get('review'):raise ValueError('Review responses require an actual supplied review')
            review_ref=write('review-for-response.json',inputs['review'])
            response={ 'contract':'ReviewResponse','version':'v1','scope':deepcopy(scope),'review_ref':review_ref,
                'responses':[{'finding_id':r['finding_id'],'disposition':r['disposition'],
                    'explanation':r['notes_md'] or 'No explanation supplied.','evidence_refs':[]} for r in value['responses']]}
            response_refs=[write('review-response-normalized.json',response)]
        return {**head,'base_source_revision':assignment['scope']['source_revision'],'plan_ref':assignment['plan_ref'],
            'action_ids':[a['id'] for a in plan['actions']],'summary':notes or 'See saved code and executable scenarios.',
            'changed_files':list(changed_files),'scenario_manifest_ref':manifest_ref,
            'review_response_refs':response_refs,'unverified_claims':value['unverified_claims'],'open_issues':value['open_issues']}
    schema=contracts.load_schema(target)
    body={k:v for k,v in value.items() if k not in ('contract','version','notes_md')}
    body={k:_expand(v,schema['properties'][k],schema,artifact,read) for k,v in body.items()}
    if target=='TaskGraph':
        reference=inputs.get('catalog_ref')
        if not reference:reference=write('catalog-for-graph.json',inputs['catalog'])
        body['catalog_ref']=reference
    if target=='CatalogPatch':body.update(base_catalog_ref=inputs['base_catalog_ref'],rationale=notes or 'See changed records.')
    if target=='ScenarioManifest':
        for scenario in body['scenarios']:scenario['media']=[]
    return {**head,**body}


def to_wire(value):
    """Migration/test helper; originals remain in immutable canonical history."""
    target=value['contract'];name=WIRES[target]
    def shrink(v):
        if isinstance(v,list):return [shrink(x) for x in v]
        if not isinstance(v,dict):return v
        if {'path','sha256','media_type'}<=set(v):return v['path']
        if {'artifact','start_line','end_line','excerpt'}<=set(v):
            return {'path':v['artifact']['path'],'start_line':v['start_line'],'end_line':v['end_line']}
        return {k:shrink(x) for k,x in v.items()}
    head={'contract':name,'version':'v1'}
    if name=='AgentPlan':return {**head,'strategy':value['strategy'],
        'actions':[{k:a[k] for k in ('kind','task_ids','condition_ids','goal','expected_observation')} for a in value['actions']],
        'notes_md':value['rationale']+'\n\n'+json.dumps(value['hypotheses'],ensure_ascii=False),
        'deferred':value['deferred'],'manual_checks':value['manual_checks'],
        'regression_condition_ids':value['regression_condition_ids'],'request_graph_change':value['replacement_graph_ref'] is not None,
        'external_updates':[]}
    if name=='AgentReview':return {**head,'disposition':value['disposition'],
        'reviewed_condition_ids':value['reviewed_condition_ids'],'notes_md':'\n\n'.join(value['unknowns']),
        'findings':[{k:f[k] for k in ('kind','severity','requirement_ids')}|{'notes_md':'\n\n'.join(f[k] for k in ('observation','consequence','suggested_check'))} for f in value['findings']]}
    if name=='AgentReport':return {**head,'scenario_manifest_path':value['scenario_manifest_ref']['path'],
        'notes_md':value['summary'],'responses':[], 'unverified_claims':value['unverified_claims'],'open_issues':value['open_issues']}
    body={k:shrink(v) for k,v in value.items() if k not in ('contract','version','scope','rationale',*OWNED.get(target,[]))}
    if name=='AgentScenarios':
        for scenario in body['scenarios']:scenario.pop('media',None)
    return {**head,**body,'notes_md':value.get('rationale','')}


def markdown(value):
    """Keep every free-text section exactly; no model-generated summary."""
    sections=[]
    def visit(node,path):
        if isinstance(node,list):
            for i,item in enumerate(node):visit(item,path+'['+str(i)+']')
        elif isinstance(node,dict):
            for key,item in node.items():
                if key=='notes_md' and isinstance(item,str):sections.append('## '+path+'\n\n'+item)
                else:visit(item,path+'.'+key)
    visit(value,'report')
    return '\n\n'.join(sections)
