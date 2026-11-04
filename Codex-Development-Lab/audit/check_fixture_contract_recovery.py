"""Read-only reproduction of the real Arcane Rituals manifest conflicts."""
import hashlib
import json
from pathlib import Path

from common.pipeline_contracts import ContractError
from common.task_graph import validate_scenarios
from worker.pipeline_verification import _prepare

root=Path(__file__).resolve().parents[1]
job=root/'artifacts/d7afb01d417c4f/f46ba60eb5f743'
paths=[job/'pipeline/catalog.json',job/'pipeline/graph.json',job/'pipeline/manifest.json',
       job/'round-01/pipeline/session.json',job/'round-01/pipeline/development-ready.json',
       root/'projects/d7afb01d417c4f/ТЗ.md']
originals={path:path.read_bytes() for path in paths}
catalog,graph,manifest,session,ready=[json.loads(originals[path]) for path in paths[:5]]
rejections=[]
for check in (lambda:validate_scenarios(manifest,catalog,graph),
              lambda:_prepare(catalog,manifest,session['decision'])):
    try:check()
    except ContractError as error:rejections.append(error.issues)
    else:raise AssertionError('Invalid manifest unexpectedly accepted')
assert rejections[0]==rejections[1]
assert len(rejections[0])==2
assert all(issue['code']=='product_assertion_as_fixture' for issue in rejections[0])
assert ready['manifest']==manifest
assert all(path.read_bytes()==raw for path,raw in originals.items())
print(json.dumps({'actual_job_reproduced':True,'all_conflicts_reported':rejections[0],
    'submission_runner_checks_agree':True,'saved_ready_requires_correction':True,
    'files_unchanged':True,'model_invoked':False,'game_started':False,
    'spec_sha256':hashlib.sha256(originals[paths[-1]]).hexdigest()},ensure_ascii=False,indent=2))
