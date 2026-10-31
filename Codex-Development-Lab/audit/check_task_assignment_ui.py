"""Offline browser checks for a single controller-assigned development task."""
import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
baseline = (root / 'audit/check_acceptance_ui.py').read_text(encoding='utf-8')
module = ast.parse(baseline)
checks = r'''<output id="acceptance-selftest"></output><script>
setTimeout(async()=>{const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label)};
try{
await refresh();q('acceptanceOpen').click();await new Promise(r=>setTimeout(r,30));
fixtureAcceptance.tasks=[{id:'TASK-1',kind:'group',title:'<img src=x onerror=alert(1)>',requirement_ids:fixtureAcceptance.requirements.map(r=>r.id),status:'STALE',summary:{total:3,stale:3}},{id:'TASK-2',kind:'group',title:'Next function',requirement_ids:[],status:'NOT_TESTED',summary:{total:0}}];
fixtureAcceptance.task_summary={total:2,test_passed:0,stale:1,not_tested:1};
fixtureAcceptance.assignment={status:'active',current_task:{id:'TASK-1',title:'<img src=x onerror=alert(1)>',unresolved_requirement_ids:['REQ-2','REQ-3']},completed_task_ids:['TASK-1'],regression_requirement_ids:['REQ-1'],pending_task_count:2};
await refreshAcceptance();
const banner=q('acceptanceAssignment'),first=q('acceptanceTaskRows').firstElementChild;
check(!banner.hidden&&banner.textContent.includes('TASK-1'),'assignment:current-task-visible');
check(q('acceptanceAssignmentDetail').textContent.includes('задаче: 2')&&q('acceptanceAssignmentDetail').textContent.includes('проверки: 1'),'assignment:unresolved-and-regression-counts');
check(banner.textContent.includes('несколько раундов')&&banner.textContent.includes('независимых проверок'),'assignment:completion-rule-explicit');
check(first.dataset.currentTask==='true'&&first.querySelector('h3').textContent.startsWith('Текущая задача'),'assignment:task-card-highlighted');
check(!banner.querySelector('img,script')&&banner.textContent.includes('<img src=x'),'assignment:untrusted-title-is-literal-text');
check(first.querySelector('h3').nextElementSibling.textContent.includes('перепроверка'),'assignment:closed-planning-group-is-not-current-pass');
first.querySelector('details').open=true;first.querySelector('button').click();const video=first.querySelector('video');
await refreshAcceptance();
check(q('acceptanceTaskRows').firstElementChild===first&&first.querySelector('video')===video&&first.querySelector('details').open,'assignment:poll-preserves-card-details-player');
first.querySelector('details').open=false;await refreshAcceptance();
check(!first.querySelector('details').open,'assignment:does-not-reopen-manually-closed-details');
fixtureAcceptance.assignment.current_task={id:'TASK-2',title:'Next function',unresolved_requirement_ids:[]};await refreshAcceptance();
check(first.dataset.currentTask==='false'&&q('acceptanceTaskRows').children[1].dataset.currentTask==='true'&&banner.textContent.includes('TASK-2'),'assignment:switch-updates-highlight');
check(first.querySelector('video')===video,'assignment:switch-keeps-existing-player');
fixtureAcceptance.assignment.status='all_verified_requires_review';fixtureAcceptance.assignment.current_task=null;fixtureAcceptance.assignment.pending_task_count=0;await refreshAcceptance();
check(banner.textContent.includes('требуется итоговая приёмка')&&!q('acceptanceTaskRows').querySelector('[data-current-task="true"]'),'assignment:all-verified-requires-review');
check(first.querySelector('h3').nextElementSibling.textContent.includes('перепроверка'),'assignment:historical-completion-never-promotes-stale');
delete fixtureAcceptance.assignment;await refreshAcceptance();
check(banner.hidden&&q('acceptanceTaskRows').children.length===2,'assignment:missing-legacy-fallback');
q('acceptanceClose').click();q('acceptance-selftest').textContent=JSON.stringify({status:'PASS',checks});
}catch(error){q('acceptance-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},150);
</script>'''
lines = baseline.splitlines()
for node in reversed(module.body):
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'checks' for t in node.targets):
        lines[node.lineno - 1:node.end_lineno] = ['checks = ' + repr(checks)]
source = '\n'.join(lines).replace("root / 'audit' / 'acceptance-ui'", "root / 'audit' / 'task-assignment-ui'")
exec(compile(source, str(root / 'audit/check_acceptance_ui.py'), 'exec'),
     {'__file__': str(root / 'audit/check_acceptance_ui.py'), '__name__': '__main__'})
