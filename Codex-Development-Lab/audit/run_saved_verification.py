"""Run inside the trusted worker via stdin, without resuming a paused model job."""
import json,uuid
from pathlib import Path
from common import acceptance,db
from common.project_lock import project_lock
from common.verification import infrastructure_failure
from worker import engine
from worker.sandbox import DockerSandbox

jid='f86241b7d5b24e'
before=db.job_row(jid)
assert before and before['status']=='USER_PAUSED','Pause job before maintenance verification'
root=engine.WORKSPACE_ROOT/before['project_id'];workspace=root/'workspace'
output=engine.ARTIFACTS/before['project_id']/jid/('audit-isolated-verifier-'+uuid.uuid4().hex[:8])
assert not (root/'.lab-runner.json').exists(),'Existing runner has not returned'
assert engine.EULA is True,'Existing owner consent is required for gameplay verification'
def poll():
    current=db.job_row(jid)
    return 'STOPPED' if (not current or current['status']!='USER_PAUSED' or current.get('cancel_requested')) else None
with project_lock(root):
    source=acceptance.source_fingerprint(workspace)
    report=DockerSandbox().verify(workspace,output,900,jid,poll,eula=engine.EULA)
    current_source=acceptance.source_fingerprint(workspace)
after=db.job_row(jid)
for field in ('status','round','provider','model','reasoning_effort','role_models'):
    assert before.get(field)==after.get(field),'Job changed during verification: '+field
result={'output':str(output),'source_unchanged':source==current_source,'model_invoked':False,
        'job_remains_paused':after['status']=='USER_PAUSED',
        'infrastructure_error':infrastructure_failure(report),
        'checks':[{'label':c.get('label'),'status':c.get('status'),'detail':c.get('detail','')[:500]}
                  for c in report.get('checks',[])],
        'scenarios':[{'id':s['id'],'status':s.get('status'),'detail':s.get('detail','')[:700],
                      'counts':s.get('failure_details',{}).get('counts')}
                     for s in report.get('acceptance_results',{}).get('scenarios',[])]}
(output/'maintenance-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
assert source==current_source,'Verification changed executable project source'
assert not result['infrastructure_error'],'Verification infrastructure is still broken'
assert result['scenarios'],'No acceptance scenario actually executed'
