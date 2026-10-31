"""Offline browser regression: preserve historical progress without claiming fresh PASS.

Uses the existing acceptance fixture's isolated mocks and browser runner. No lab
requests, job mutations or model calls are permitted by that fixture.
"""
import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
baseline = (root / 'audit/check_acceptance_ui.py').read_text(encoding='utf-8')
module = ast.parse(baseline)
mock = next(ast.literal_eval(node.value) for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'mock'
                    for target in node.targets))
checks = r'''<output id="acceptance-selftest"></output><script>
setTimeout(async()=>{const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label)};
try{
await refresh();q('acceptanceOpen').click();await new Promise(r=>setTimeout(r,30));
fixtureAcceptance.requirements[1].status='STALE';
fixtureAcceptance.summary={total:3,test_passed:1,stale:1,not_tested:1,failed:0,without_video:1};
fixtureAcceptance.history_summary={test_passed:2,tested:2,latest_round:37};
fixtureAcceptance.report_round=37;
await refreshAcceptance();
const count=()=>[...q('acceptanceCounts').children].map(x=>x.textContent);
check(count().includes('Подтверждено для проверенной версии: 1'),'counts:current-pass-separated');
check(count().includes('Нужна перепроверка: 1'),'counts:stale-visible');
check(count().includes('Ранее проходили тесты: 2'),'counts:historical-pass-preserved');
check(count().includes('Ещё не проверено: 1'),'counts:never-tested-excludes-stale');
check(q('acceptanceCoverage').textContent.includes('раунд 37'),'snapshot:round-visible');
check(q('acceptanceCoverage').textContent.includes('требуется перепроверка'),'snapshot:stale-explained');
q('acceptanceRows').querySelector('button').click();const video=q('acceptanceRows').querySelector('video');
fixtureJob.status='RUNNING';fixtureJob.phase='verifying';fixtureAcceptance.verification_in_progress=true;
await refreshAcceptance();
check(count().includes('Подтверждено для проверенной версии: 1'),'running:does-not-zero-last-result');
check(q('acceptanceCoverage').textContent.includes('не подтверждают текущие изменения'),'running:snapshot-limitation-explicit');
check(q('acceptanceRows').querySelector('video')===video,'poll:player-node-preserved');
q('acceptanceFilter').value='STALE';filterAcceptanceRows();
check([...q('acceptanceRows').children].filter(x=>!x.hidden).length===1,'filter:stale-distinct');
q('acceptanceFilter').value='NOT_TESTED';filterAcceptanceRows();
check([...q('acceptanceRows').children].filter(x=>!x.hidden).length===1,'filter:never-tested-distinct');
q('acceptanceFilter').value='TEST_PASSED';filterAcceptanceRows();
check([...q('acceptanceRows').children].filter(x=>!x.hidden).length===1,'filter:history-not-promoted-to-pass');
check(!q('acceptanceRows').querySelector('img,script'),'untrusted:literal-text-only');
check(q('acceptanceRows').querySelectorAll('a').length===1,'evidence:unsafe-links-rejected');
delete fixtureAcceptance.history_summary;delete fixtureAcceptance.summary.stale;
await refreshAcceptance();
check(count().includes('Нужна перепроверка: 1'),'legacy:stale-count-from-items');
check(count().includes('Ранее проходили тесты: нет данных'),'legacy:does-not-invent-history');
check(fixtureRequests.every(p=>p.startsWith('/api/jobs')),'fixture:read-only-local-endpoints');
q('acceptanceClose').click();q('acceptance-selftest').textContent=JSON.stringify({status:'PASS',checks});
}catch(error){q('acceptance-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},150);
</script>'''

# Reuse the original runner, replacing only its mock/check assignments and output
# folder. Compile it with the original filename so its root calculation is stable.
changes = {}
for node in module.body:
    if not isinstance(node, ast.Assign):
        continue
    names = [target.id for target in node.targets if isinstance(target, ast.Name)]
    if 'checks' in names:
        changes[node.lineno] = (node.end_lineno, 'checks = ' + repr(checks))
    elif 'mock' in names:
        changes[node.lineno] = (node.end_lineno, 'mock = ' + repr(mock))
lines = baseline.splitlines()
for start, (end, replacement) in sorted(changes.items(), reverse=True):
    lines[start - 1:end] = [replacement]
source = '\n'.join(lines).replace("root / 'audit' / 'acceptance-ui'",
                                 "root / 'audit' / 'acceptance-progress-ui'")
# The constrained Windows execution host cannot start Edge's GPU subprocess
# sandbox. This browser loads only this script's synthetic localhost fixture.
source = source.replace("'--headless=new', '--disable-gpu'",
                        "'--headless=new', '--no-sandbox', '--disable-gpu'")
exec(compile(source, str(root / 'audit/check_acceptance_ui.py'), 'exec'),
     {'__file__': str(root / 'audit/check_acceptance_ui.py'), '__name__': '__main__'})
