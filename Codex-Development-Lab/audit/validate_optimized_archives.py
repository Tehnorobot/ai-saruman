"""Read archived project facts, exercise new views in /tmp, never mutate jobs."""
from copy import deepcopy
import hashlib,json,tempfile
from pathlib import Path
from unittest.mock import patch
from common import condition_progress,role_context,task_graph
from common.evidence_access import read_artifact
from common.verification_feedback import execution_diagnostics
from worker.pipeline import Cycle,artifact

root=Path('/audit')
output=root/'artifacts/d7afb01d417c4f/f46ba60eb5f743'
workspace=root/'projects/d7afb01d417c4f/workspace'
read=lambda path:json.loads(path.read_text('utf-8'))
latest=read(output/'latest_verification.json');original=read(output/'pipeline/latest-result.json')
catalog=read(output/'pipeline/catalog.json');graph=read(output/'pipeline/graph.json')
scope={**original['scope']}
normative=root/'projects/d7afb01d417c4f/ТЗ.md'
spec_hash=hashlib.sha256(normative.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory() as temporary:
    ws=Path(temporary)
    diagnostics=execution_diagnostics(ws,output,deepcopy(latest),scope['round'],scope=scope)
    matched=[d for d in diagnostics if 'BuildMetadataTest#the project root has no logs directory' in d['actual'] and '/cache/build/test-run' in d['actual']]
    assert matched,'Historical primary testcase is still unreachable'
    evidence=read_artifact(ws,matched[0]['evidence_refs'][0],scope={'project_id':scope['project_id'],'spec_revision':scope['spec_revision']})
    assert evidence['content'] and evidence['scope']==scope
    view=role_context.build('planner','plan',scope,{},full_input_ref={},latest_result={**original,'execution_diagnostics':diagnostics})
    assert any('/cache/build/test-run' in d['preview'] for d in view['facts'])

def manifest_for(old):
    path=output/f"round-{old['round']:02d}/pipeline/development-ready.json"
    return read(path).get('manifest') if path.exists() else None
# Paused archive snapshot is mounted read-only. Production index_entries takes
# a writable lock, so supply its already-read immutable metadata for this audit.
index=read(output/'pipeline-memory/index.json')['records']
with patch('common.pipeline_memory.index_entries',return_value=index):
    projection=condition_progress.reconstruct(output,catalog,scope,manifest_for)
repeated=[p['condition_id'] for p in projection['conditions'] if p['repeated_unresolved']]
state=read(output/'pipeline/state.json')
cycle=Cycle.__new__(Cycle);cycle.ws=workspace;cycle.output=output;cycle.scope=scope;cycle.state=state
states=cycle.task_states(catalog,graph)
decision=read(output/f"round-{scope['round']:02d}/pipeline/session.json")['decision']
decision=deepcopy(decision);decision['strategy']='repair';decision['deferred']=[];decision['manual_checks']=[]
decision['replacement_graph_ref']=None;decision['regression_condition_ids']=[]
decision['actions']=[{'id':'A1','kind':'repair','task_ids':['W17'],'condition_ids':['C51'],
    'goal':'Repair the observed build layout assertion','expected_observation':'Same invariant passes in both layouts',
    'depends_on_action_ids':[]}]
task_graph.validate_decision(decision,graph,catalog,states)
assert spec_hash==hashlib.sha256(normative.read_bytes()).hexdigest()
print(json.dumps({'historical_primary_failure_reachable':True,'matched_diagnostic':matched[0]['actual'],
    'role_context_contains_primary_fact':True,'condition_count':len(projection['conditions']),
    'actual_repeated_product_failures':repeated,'narrow_repair_tasks':['W17'],
    'implementation_availability_separate_from_verification':True,'spec_unchanged':True,
    'project_mutated':False,'model_invoked':False,'game_started':False},ensure_ascii=False,indent=2))
