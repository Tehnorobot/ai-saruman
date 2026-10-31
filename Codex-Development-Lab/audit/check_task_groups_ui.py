"""Offline grouped acceptance browser regression. No model or job mutations."""
import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
baseline = (root / 'audit/check_acceptance_ui.py').read_text(encoding='utf-8')
module = ast.parse(baseline)
checks = r'''<output id="acceptance-selftest"></output><script>
setTimeout(async()=>{const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label)};
try{
await refresh();q('acceptanceOpen').click();await new Promise(r=>setTimeout(r,30));
const original=fixtureAcceptance.requirements;
fixtureAcceptance.requirements=Array.from({length:240},(_,i)=>({...original[i%3],id:'REQ-'+(i+1),title:'Condition '+(i+1),status:i===0?'TEST_PASSED':'NOT_TESTED'}));
fixtureAcceptance.summary={total:240,test_passed:1,not_tested:239,failed:0,stale:0};
fixtureAcceptance.tasks=Array.from({length:29},(_,i)=>({id:'TASK-'+(i+1),kind:'group',title:i===0?'<img src=x onerror=alert(1)>':'Function '+(i+1),section:'Section',requirement_ids:fixtureAcceptance.requirements.filter((_,n)=>n%29===i).map(r=>r.id),status:i===0?'IN_PROGRESS':'NOT_TESTED',summary:{total:fixtureAcceptance.requirements.filter((_,n)=>n%29===i).length,test_passed:i===0?1:0,not_tested:8,failed:0,stale:0}}));
fixtureAcceptance.task_summary={total:29,test_passed:0,in_progress:1,not_tested:28,failed:0,stale:0};
await refreshAcceptance();
const counts=()=>q('acceptanceCounts').textContent;
check(q('acceptanceView').value==='tasks'&&!q('acceptanceTaskRows').hidden&&q('acceptanceRows').hidden,'default:grouped-view');
check(q('acceptanceTaskRows').children.length===29,'groups:29-functional-tasks');
check(counts().includes('Задач: 29')&&counts().includes('Исходных условий ТЗ: 240'),'counts:29-tasks-240-source-conditions');
check(q('acceptanceTaskRows').querySelectorAll('details p').length===240,'coverage:all-source-conditions-retained');
check(counts().includes('Подтверждено полностью: 0')&&counts().includes('Выполнено частично: 1'),'partial:not-promoted-to-pass');
check(!q('acceptanceTaskRows').querySelector('img,script'),'untrusted:task-title-is-text');
check(!q('acceptanceTaskRows').querySelector('video'),'video:lazy');
const first=q('acceptanceTaskRows').firstElementChild,details=first.querySelector('details');details.open=true;
first.querySelector('button').click();const video=first.querySelector('video');
check(!!video,'video:explicit-open');
const uniqueScenarios=new Set(fixtureAcceptance.requirements.filter((_,n)=>n%29===0).flatMap(r=>r.scenarios||[]).map(s=>s.id));
check(first.querySelectorAll('.history-card').length===uniqueScenarios.size,'evidence:deduplicated-within-task');
await refreshAcceptance();
check(first===q('acceptanceTaskRows').firstElementChild&&first.querySelector('video')===video&&details.open,'poll:card-details-player-preserved');
q('acceptanceFilter').value='IN_PROGRESS';filterAcceptanceRows();
check([...q('acceptanceTaskRows').children].filter(r=>!r.hidden).length===1,'filter:partial-task');
q('acceptanceFilter').value='TEST_PASSED';filterAcceptanceRows();
check([...q('acceptanceTaskRows').children].filter(r=>!r.hidden).length===0,'filter:partial-is-not-pass');
q('acceptanceFilter').value='ALL';q('acceptanceSearch').value='Condition 240';filterAcceptanceRows();
check([...q('acceptanceTaskRows').children].filter(r=>!r.hidden).length===1,'search:finds-member-condition');
q('acceptanceSearch').value='';q('acceptanceView').value='requirements';q('acceptanceView').dispatchEvent(new Event('change'));
check(!q('acceptanceRows').hidden&&q('acceptanceTaskRows').hidden&&q('acceptanceRows').children.length===240,'toggle:all-original-conditions');
check(counts().includes('Всего: 240'),'toggle:original-summary');
q('acceptanceView').value='tasks';q('acceptanceView').dispatchEvent(new Event('change'));
check(first.querySelector('video')===video&&details.open,'toggle:preserves-group-player-and-details');
check([...q('acceptanceTaskRows').querySelectorAll('a')].every(a=>a.href.startsWith(location.origin+'/api/jobs/')),'evidence:local-job-links-only');
delete fixtureAcceptance.tasks;delete fixtureAcceptance.task_summary;await refreshAcceptance();
check(!q('acceptanceRows').hidden&&q('acceptanceTaskRows').hidden,'legacy:raw-view-without-tasks');
q('acceptanceClose').click();q('acceptance-selftest').textContent=JSON.stringify({status:'PASS',checks});
}catch(error){q('acceptance-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},150);
</script>'''
changes = {}
for node in module.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'checks' for t in node.targets):
        changes[node.lineno] = (node.end_lineno, 'checks = ' + repr(checks))
lines = baseline.splitlines()
for start, (end, replacement) in sorted(changes.items(), reverse=True):
    lines[start - 1:end] = [replacement]
source = '\n'.join(lines).replace("root / 'audit' / 'acceptance-ui'", "root / 'audit' / 'task-groups-ui'")
exec(compile(source, str(root / 'audit/check_acceptance_ui.py'), 'exec'),
     {'__file__': str(root / 'audit/check_acceptance_ui.py'), '__name__': '__main__'})
