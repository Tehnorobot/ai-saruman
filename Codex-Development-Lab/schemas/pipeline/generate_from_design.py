"""Maintain image-owned v1 copies of the reviewed documentation contracts.

This is an explicit maintenance command, never called while processing a job.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
source = json.loads((ROOT/'docs/pipeline/contracts.schema.json').read_text(encoding='utf-8'))
examples = json.loads((ROOT/'docs/pipeline/examples.json').read_text(encoding='utf-8'))
tree = ast.parse((ROOT/'audit/build_pipeline_explainer.py').read_text(encoding='utf-8'))
help_text = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'FIELD_HELP' for t in n.targets))

def patch(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'version' and value == 'proposal-v1': node[key] = 'v1'
            elif key == 'const' and value == 'proposal-v1': node[key] = 'v1'
            else: patch(value)
        for name, field in node.get('properties', {}).items():
            if name in help_text: field['description'] = help_text[name].replace('proposal-v1', 'v1')
    elif isinstance(node, list):
        for value in node: patch(value)

patch(source)
patch(examples)
defs = source['$defs']
# Scenario IDs also appear in trusted recording-window markers and directories.
# Keep one portable identity across contracts, runner requests and media capture.
scenario_id = {'type':'string','pattern':r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$','maxLength':64}
defs['Scenario']['properties']['id'] = dict(scenario_id)
defs['ScenarioResult']['properties']['scenario_id'] = dict(scenario_id)
for name in ('scenario_ids','regression_scenario_ids'):
    defs['VerificationRequest']['properties'][name]['items'] = dict(scenario_id)

def normalize_example_scenario_ids(node):
    if isinstance(node, dict):
        return {key:normalize_example_scenario_ids(value) for key,value in node.items()}
    if isinstance(node, list): return [normalize_example_scenario_ids(value) for value in node]
    if isinstance(node, str): return node.replace('S1','s1')
    return node

examples = normalize_example_scenario_ids(examples)
# Artifact paths are names within an authorized root, never provider URLs or
# host paths. Filesystem readers additionally reject symlink-based escapes.
defs['ArtifactRef']['properties']['path']['pattern'] = r'^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*//)(?!.*[\\:\x00])[^/]+(?:/[^/]+)*$'
def object_schema(**fields):
    return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
def text(): return {'type':'string','minLength':1}
def array(item): return {'type':'array','items':item}

# Optional for previously saved catalogs. New unresolved questions receive an
# explicit lifecycle; neither string presence nor model agreement is a PASS.
question_resolution = object_schema(
    question=text(),
    kind={'type':'string','enum':['implementation_decision','experiment','user_clarification']},
    status={'type':'string','enum':['open','resolved']},
    owner={'type':'string','enum':['planner','coder','user']},
    condition_ids=array(text()),
    answer={'anyOf':[text(),{'type':'null'}]},
    reason=text(),
    source_refs=array({'$ref':'#/$defs/SourceRef'}))
defs['RequirementsCatalog']['properties']['question_resolutions'] = array(question_resolution)
examples['RequirementsCatalog']['question_resolutions'] = []
defs['ReviewRequest']['properties']['subject']['enum'].append('catalog')
test = object_schema(classname=text(), name=text())
checkpoint_test = object_schema(checkpoint_id=text(), classname=text(), name=text())
execution = object_schema(
    kind={'type':'string','enum':['gameplay','unit','integration']},
    gradle_task={'type':'string','pattern':'^[A-Za-z][A-Za-z0-9_]{0,95}$'},
    expected_tests=array(test), checkpoint_tests=array(checkpoint_test))
execution['description'] = 'Safe Gradle/JUnit binding; checkpoint status comes from these independently executed test cases.'
defs['Scenario']['properties']['execution'] = execution
defs['Scenario']['required'].append('execution')

# Let the planner choose relevant regressions without forcing the full growing
# test suite into every development round. Final readiness still runs it all.
defs['PlannerDecision']['properties']['regression_condition_ids'] = {
    'type':'array','items':text(),'uniqueItems':True,
    'description':'Previously implemented conditions whose scenarios must be rerun alongside the selected work. No fixed count.'}
defs['PlannerDecision']['required'].append('regression_condition_ids')
examples['PlannerDecision']['regression_condition_ids'] = []
defs['ReviewResult']['properties']['reviewed_condition_ids'] = {
    'type':'array', 'items':text(), 'minItems':1, 'uniqueItems':True,
    'description':'Conditions actually reviewed against the cited specification, code and tests; not a PASS claim.'}
defs['ReviewResult']['properties']['evidence_refs'] = {
    'type':'array', 'items':{'$ref':'#/$defs/SourceRef'}, 'minItems':1,
    'description':'Literal tool-observed sources supporting this review, including reviews with no findings.'}
defs['ReviewResult']['required'].extend(['reviewed_condition_ids','evidence_refs'])
defs['ReviewResult']['properties']['evidence_refs'].pop('minItems',None)
examples['ReviewResult']['reviewed_condition_ids'] = ['C1']
examples['ReviewResult']['evidence_refs'] = examples['ReviewResult']['findings'][0]['code_refs']
for scenario in examples['ScenarioManifest']['scenarios']:
    names = ['siteFound','saplingPlaced','treeGrown']
    scenario['execution'] = {
        'kind':'gameplay', 'gradle_task':'runClientGameTest',
        'expected_tests':[{'classname':'dev.example.PlantTests','name':name} for name in names],
        'checkpoint_tests':[{'checkpoint_id':cp['id'],'classname':'dev.example.PlantTests','name':name}
                            for cp,name in zip(scenario['checkpoints'],names)]}

def dependencies(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        if '$ref' in node:
            name = node['$ref'].rsplit('/',1)[1]
            if name not in found:
                found.add(name)
                dependencies(defs[name],found)
        for value in node.values(): dependencies(value,found)
    elif isinstance(node, list):
        for value in node: dependencies(value,found)
    return found

# A model edits named records, never rewrites the unchanged catalog or sends
# arbitrary filesystem/JSON-pointer operations. The controller owns snapshots.
defs['CatalogPatch'] = object_schema(
    contract={'const':'CatalogPatch'},version={'const':'v1'},scope={'$ref':'#/$defs/Scope'},
    base_catalog_ref={'$ref':'#/$defs/ArtifactRef'},rationale=text(),
    addressed_finding_ids=array(text()),
    requirements_upsert=array({'$ref':'#/$defs/Requirement'}),requirements_remove=array(text()),
    conditions_upsert=array({'$ref':'#/$defs/Condition'}),conditions_remove=array(text()),
    questions_add=array(text()),question_resolutions_upsert=array(question_resolution),
    unclassified_add=array({'$ref':'#/$defs/SourceRef'}),unclassified_remove=array({'$ref':'#/$defs/SourceRef'}))
examples['CatalogPatch'] = {
    'contract':'CatalogPatch','version':'v1','scope':examples['RequirementsCatalog']['scope'],
    'base_catalog_ref':examples['TaskGraph']['catalog_ref'],
    'rationale':'Clarify only C1; preserve all other records.', 'addressed_finding_ids':['F1'],
    'requirements_upsert':[],'requirements_remove':[],
    'conditions_upsert':[dict(examples['RequirementsCatalog']['conditions'][0],expected='The planted sapling is present at the chosen position.')],
    'conditions_remove':[],'questions_add':[],'question_resolutions_upsert':[],
    'unclassified_add':[],'unclassified_remove':[]}
defs['ValidationRequest']['properties']['schema_name']['enum'].append('CatalogPatch')
defs['PreparationHandoff']=object_schema(
    contract={'const':'PreparationHandoff'},version={'const':'v1'},scope={'$ref':'#/$defs/Scope'},
    catalog_ref={'$ref':'#/$defs/ArtifactRef'},graph_ref={'$ref':'#/$defs/ArtifactRef'},
    rationale=text(),working_decisions=array(text()),
    tasks=array(object_schema(task_id=text(),
        status={'type':'string','enum':['ready','experiment','needs_user','external']},
        reason=text(),experiment={'anyOf':[text(),{'type':'null'}]},return_when=text())),
    unresolved_review_refs=array({'$ref':'#/$defs/ArtifactRef'}))
examples['PreparationHandoff']={
    'contract':'PreparationHandoff','version':'v1','scope':examples['RequirementsCatalog']['scope'],
    'catalog_ref':examples['TaskGraph']['catalog_ref'],
    'graph_ref':examples['PlannerInput']['graph_ref'],
    'rationale':'Proceed with independent planting; test the uncertain growth behavior.',
    'working_decisions':['Use the existing navigation API; confirm behavior through tests.'],
    'tasks':[{'task_id':'T1','status':'ready','reason':'Planting behavior is clear.', 'experiment':None,'return_when':'Available now'},
             {'task_id':'T2','status':'experiment','reason':'Growth geometry is not yet measured.',
              'experiment':'Compare actual world blocks before and after applying bone meal.','return_when':'Use the observed result to choose geometry.'}],
    'unresolved_review_refs':[]}
defs['ValidationRequest']['properties']['schema_name']['enum'].append('PreparationHandoff')

for name, example in examples.items():
    names = dependencies({'$ref':'#/$defs/'+name})
    schema = {**defs[name], '$schema':'https://json-schema.org/draft/2020-12/schema',
              'title':name+' — runtime v1',
              '$defs':{n:defs[n] for n in sorted(names)}}
    (OUT/(name+'.schema.json')).write_text(json.dumps(schema,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    target = OUT/'examples'/(name+'.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(example,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Generated',len(examples),'runtime v1 contracts')

# Same portable generation grammar for CLI and API. Full validation above is
# retained; unsupported provider decoder constraints are never a waiver.
import sys
sys.path.insert(0,str(ROOT))
from common.pipeline_contracts import submission_schema
for name in examples:
    (OUT/(name+'.submission.schema.json')).write_text(
        json.dumps(submission_schema(name),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

import runpy
runpy.run_path(str(OUT/'generate_agent_protocol.py'),run_name='__main__')
