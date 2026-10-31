"""Read-only offline browser checks for source coverage and stable evidence nodes."""
import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
baseline = (root / 'audit/check_acceptance_ui.py').read_text(encoding='utf-8')
module = ast.parse(baseline)
checks = r'''<output id="acceptance-selftest"></output><script>
setTimeout(async()=>{const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label)};
try{
await refresh();q('acceptanceOpen').click();await new Promise(r=>setTimeout(r,30));
const requirement=fixtureAcceptance.requirements[0];requirement.status='IN_PROGRESS';
requirement.coverage={complete:false,review_round:39,missing_ids:['lava'],obligations:[
 {id:'water',text:'Recover air in water.',start:8,end:8,scenario_ids:['attack'],reviewed:true,reason:'Assertion inspected.'},
 {id:'lava',text:'Avoid lava. <img src=x onerror=alert(1)>',start:9,end:10,scenario_ids:[],reviewed:false,reason:'No executable scenario.'}
]};
fixtureAcceptance.summary={total:3,test_passed:0,in_progress:1,not_tested:2,failed:0};
fixtureAcceptance.tasks=[{id:'TASK-1',title:'Movement and water',kind:'group',status:'IN_PROGRESS',requirement_ids:['REQ-001'],summary:{total:1,in_progress:1,test_passed:0}}];
fixtureAcceptance.task_summary={total:1,test_passed:0,in_progress:1};
await refreshAcceptance();
check(q('acceptanceTaskRows').textContent.includes('Выполнено частично'),'group:partial-not-full-pass');
const grouped=q('acceptanceTaskRows').querySelector('.acceptance-coverage');grouped.open=true;
check(grouped.textContent.includes('1/2 условий сопоставлено'),'coverage:conditions-count');
check(grouped.textContent.includes('Avoid lava.')&&grouped.textContent.includes('не назначены'),'coverage:missing-condition-visible');
check(grouped.textContent.includes('строки')||grouped.textContent.includes('Строки ТЗ: 9–10'),'coverage:exact-source-lines');
check(!grouped.querySelector('img,script'),'coverage:untrusted-text-literal');
q('acceptanceTaskRows').querySelector('button').click();const video=q('acceptanceTaskRows').querySelector('video');
await refreshAcceptance();
check(q('acceptanceTaskRows').querySelector('.acceptance-coverage')===grouped&&grouped.open,'poll:group-coverage-expansion-preserved');
check(q('acceptanceTaskRows').querySelector('video')===video,'poll:group-video-preserved');
q('acceptanceView').value='requirements';updateAcceptanceView();
const source=q('acceptanceRows').querySelector('.acceptance-coverage');source.open=true;
check(q('acceptanceCounts').textContent.includes('Выполнено частично: 1'),'summary:partial-count-visible');
q('acceptanceFilter').value='IN_PROGRESS';filterAcceptanceRows();
check([...q('acceptanceRows').children].filter(x=>!x.hidden).length===1,'filter:partial-requirements');
await refreshAcceptance();check(q('acceptanceRows').querySelector('.acceptance-coverage')===source&&source.open,'poll:source-expansion-preserved');
check(fixtureRequests.every(p=>p.startsWith('/api/jobs')),'fixture:read-only-local-endpoints');
q('acceptance-selftest').textContent=JSON.stringify({status:'PASS',checks});
}catch(error){q('acceptance-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},150);</script>'''
changes = {}
for node in module.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'checks' for t in node.targets):
        changes[node.lineno] = (node.end_lineno, 'checks = ' + repr(checks))
lines = baseline.splitlines()
for start, (end, replacement) in sorted(changes.items(), reverse=True):
    lines[start - 1:end] = [replacement]
source = '\n'.join(lines).replace("root / 'audit' / 'acceptance-ui'", "root / 'audit' / 'acceptance-coverage-ui'")
source = source.replace("'--headless=new', '--disable-gpu'", "'--headless=new', '--no-sandbox', '--disable-gpu'")
exec(compile(source, str(root / 'audit/check_acceptance_ui.py'), 'exec'),
     {'__file__': str(root / 'audit/check_acceptance_ui.py'), '__name__': '__main__'})
