"""Idempotent image-schema maintenance; never touches projects or live state."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from common.agent_protocol import compact_schema,to_wire

base=ROOT/'schemas/pipeline'
def read(name):return json.loads((base/(name+'.schema.json')).read_text('utf-8'))
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def portable(node):
    result={k:deepcopy(v) for k,v in node.items() if k in ('type','description','required','additionalProperties','$ref','enum')}
    if 'const' in node:
        value=node['const'];result['enum']=[value];result.setdefault('type','string')
    for key in ('properties','$defs'):
        if key in node:result[key]={k:portable(v) for k,v in node[key].items()}
    if 'properties' in result:result['required']=list(result['properties'])
    if 'items' in node:result['items']=portable(node['items'])
    for key in ('anyOf','oneOf','allOf'):
        if key in node:result[key]=[portable(v) for v in node[key]]
    return result

report=read('CoderReport')
report['properties']['action_outcomes']={'type':'array','items':{'type':'object','properties':{
    'action_id':{'type':'string'},'status':{'type':'string','enum':['implemented_claim','partial','not_attempted','needs_experiment','not_assessed']},
    'notes_md':{'type':'string'}},'required':['action_id','status','notes_md'],'additionalProperties':False},
    'description':'Per-action coder claims only; absence remains not_assessed, never PASS.'}
planner=read('PlannerInput')
planner['$defs']['TaskState']['properties']['implementation']={'type':'string','enum':['unknown','absent','present_claim','buildable']}
planner['$defs']['TaskState']['properties']['implementation_refs']={'type':'array','items':{'$ref':'#/$defs/ArtifactRef'}}
manifest=read('ScenarioManifest')
review=read('ReviewResult')
review['properties']['resolved_finding_ids']={'type':'array','items':{'type':'string'},'uniqueItems':True,
    'description':'Existing findings explicitly resolved by this review; review judgement is not independent PASS.'}
manifest['$defs']['Scenario']['properties']['execution']['properties']['reuse']={'anyOf':[
    {'type':'null'}, {'type':'object','properties':{k:{'type':'string','minLength':1,'maxLength':120} for k in ('group','profile','initial_state')},
     'required':['group','profile','initial_state'],'additionalProperties':False}],
    'description':'Explicit reviewed consent to share identical fresh unit execution inputs. Null preserves separate runs; gameplay cannot share.'}
for name,value in [('CoderReport',report),('PlannerInput',planner),('ScenarioManifest',manifest),('ReviewResult',review)]:
    write(base/(name+'.schema.json'),value);write(base/(name+'.submission.schema.json'),portable(value))
for name,key,empty in [('CoderReport','action_outcomes',[]),('ReviewResult','resolved_finding_ids',[])]:
    path=base/'examples'/(name+'.json');value=json.loads(path.read_text('utf-8'));value.setdefault(key,empty);write(path,value)
path=base/'examples/PlannerInput.json';value=json.loads(path.read_text('utf-8'))
for state in value['task_states']:state.setdefault('implementation','unknown');state.setdefault('implementation_refs',[])
write(path,value)
path=base/'examples/ScenarioManifest.json';value=json.loads(path.read_text('utf-8'))
for scenario in value['scenarios']:scenario['execution'].setdefault('reuse',None)
write(path,value)
for wire,target in [('AgentPlan','PlannerDecision'),('AgentReview','ReviewResult'),('AgentReport','CoderReport'),('AgentScenarios','ScenarioManifest')]:
    schema=compact_schema(wire,read(target));write(base/(wire+'.schema.json'),schema);write(base/(wire+'.submission.schema.json'),portable(schema))
    original=json.loads((base/'examples'/(target+'.json')).read_text('utf-8'))
    if wire=='AgentScenarios':
        for s in original['scenarios']:s['execution'].setdefault('reuse',None)
    write(base/'examples'/(wire+'.json'),to_wire(original))
print('Updated optimized optional contracts and compact examples')
