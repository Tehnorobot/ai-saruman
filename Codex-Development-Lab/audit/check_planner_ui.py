"""Offline browser regression checks for planner, manual instructions and stale requests."""
import html
import json
import re
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / 'audit' / 'planner-ui'
folder.mkdir(exist_ok=True)
script = (root / 'app/planner.js').read_text(encoding='utf-8')
mock = r'''
let active='aaaaaaaaaaaaaa';const calls=[];let delayed=null,mode='normal';
const state={planner:{state:'available',source:'planner-result.json',data:{round:12,mode:'deep_diagnosis',status:'READY',
 summary:'Investigate repeated failure',action:{kind:'repair_scenario',target:'client-combat',reason:'same failure repeated',diagnostic:'<img src=x onerror=alert(1)>',requirement_ids:['REQ-one'],expected_progress:{kind:'repair',observable_result:'Independent scenario proves that input is released after stopping'}},
 additional_actions:[{kind:'implement_and_map_requirement',target:'REQ-two',reason:'Share controller and fixture',depends_on:['client-combat'],requirement_ids:['REQ-two']}],bundle_reason:'Related combat lifecycle',verification_limit:2,
 hypotheses:['Wrong game thread'],next_steps:['Reproduce','Fix then run regression']}},
 manual_reviews:[{id:'REQ-manual',title:'Visual check',source:{text:'Original spec'},review_status:'USER_TEST_REQUIRED',
 manual_review:{reason:'Subjective appearance',automation_blocker:'Needs user judgement',category:'visual',steps:['Install JAR','Press F8'],expected_result:'Visible HUD'}}]};
window.fetch=async(path,options)=>{
 calls.push(path);if(options?.method)throw Error('Mutation forbidden');
 if(mode==='delay'){mode='normal';return await new Promise(resolve=>delayed=resolve)}
 if(mode==='error')throw Error('offline fixture');
 return {ok:true,json:async()=>mode==='missing'?{planner:{state:'missing'},manual_reviews:[]}:state};
};
'''
checks = r'''
setTimeout(async()=>{
 const checks=[],wait=()=>new Promise(r=>setTimeout(r,40));
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label)};
 try{
  const panel=document.getElementById('plannerPanel'),content=document.getElementById('plannerContent'),player=document.getElementById('existingVideo');
  check(panel.textContent.includes('client-combat')&&panel.textContent.includes('same failure repeated'),'selected-action-and-reason');
  check(panel.querySelector('.planner-expected-progress').textContent.includes('Independent scenario proves')&&panel.textContent.includes('Ожидаемый результат'),'expected-result-distinct-from-execution');
  check(panel.querySelector('.planner-bundle ol').children.length===2&&panel.textContent.includes('REQ-two')&&panel.textContent.includes('Related combat lifecycle'),'whole-package-and-purpose-visible');
  check(panel.textContent.includes('После: client-combat')&&panel.textContent.includes('до 2'),'package-dependency-and-verification-limit');
  check(panel.textContent.includes('Углублённая диагностика')&&panel.textContent.includes('Wrong game thread'),'diagnostic-mode-and-hypothesis');
  check(panel.textContent.includes('<img')&&!panel.querySelector('img,script'),'untrusted-data-rendered-as-text');
  check(panel.querySelector('details ol').children.length===2&&panel.textContent.includes('Visible HUD')&&panel.textContent.includes('Needs user judgement'),'manual-steps-expected-result-and-blocker');
  check(panel.textContent.includes('USER_TEST_REQUIRED')&&!panel.textContent.includes('TEST_PASSED'),'manual-check-is-not-automatic-pass');
  state.planner.data.scheduling_directive={kind:'implement_functional_goal',required_target:'REQ-goal',source_condition:'Actual gameplay behavior',reason:'Bootstrap already verified'};
  state.planner.data.action.verification_objective='Actual attack followed by a safe stop';
  document.getElementById('refreshPlanner').click();await wait();
  check(panel.textContent.includes('Приоритет — первый рабочий сценарий')&&panel.textContent.includes('Actual gameplay behavior')&&panel.textContent.includes('Actual attack followed by a safe stop'),'functional-goal-and-observable-result-visible');
  const detail=panel.querySelector('details');detail.open=true;state.planner.data.round=13;
  document.getElementById('refreshPlanner').click();await wait();
  check(panel.querySelector('details').open,'manual-expansion-survives-poll');
  mode='delay';document.getElementById('refreshPlanner').click();await wait();
  active='bbbbbbbbbbbbbb';window.plannerSwitchJob(active);await wait();
  active='aaaaaaaaaaaaaa';window.plannerSwitchJob(active);await wait();
  delayed({ok:true,json:async()=>({planner:{state:'available',data:{summary:'STALE-REQUEST'}}})});await wait();
  check(!content.textContent.includes('STALE-REQUEST')&&content.textContent.includes('client-combat'),'old-response-rejected-after-a-b-a-switch');
  mode='error';document.getElementById('refreshPlanner').click();await wait();
  check(document.getElementById('plannerNotice').textContent.includes('устаревшими')&&content.textContent.includes('client-combat'),'error-keeps-result-with-stale-warning');
  mode='missing';document.getElementById('refreshPlanner').click();await wait();
  check(content.textContent.includes('не подтверждено')&&content.textContent.includes('не означает'),'missing-plan-does-not-imply-completion');
  check(document.getElementById('existingVideo')===player,'media-node-preserved');
  check(calls.every(path=>/^\/api\/jobs\/[a-z]+\/planning$/.test(path)),'local-read-only-api-only');
  document.getElementById('result').textContent=JSON.stringify({status:'PASS',checks});
 }catch(error){document.getElementById('result').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},300);
'''
page = '<!doctype html><html><meta charset="utf-8"><body><section id="agentHistory"></section><video id="existingVideo"></video><output id="result"></output><script>' + mock + '</script><script>' + script + '</script><script>' + checks + '</script></body></html>'
(folder / 'fixture.html').write_text(page, encoding='utf-8')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(folder), **kwargs)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    result = subprocess.run([
        'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
        '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
        '--disable-extensions', '--user-data-dir=' + str(folder / 'edge-profile'),
        '--virtual-time-budget=3000', '--dump-dom',
        'http://127.0.0.1:' + str(server.server_port) + '/fixture.html',
    ], capture_output=True, timeout=45)
finally:
    server.shutdown()
    server.server_close()
dom = result.stdout.decode('utf-8', errors='replace')
(folder / 'dom.html').write_text(dom, encoding='utf-8')
(folder / 'browser.log').write_bytes(result.stderr)
match = re.search(r'<output id="result">(.*?)</output>', dom, re.S)
if not match:
    raise RuntimeError('Browser returned no planner UI result')
report = json.loads(html.unescape(match[1]))
(folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
assert report['status'] == 'PASS'
