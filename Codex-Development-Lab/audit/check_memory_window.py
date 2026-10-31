"""Offline Edge checks of the actual memory viewer; no model/job mutations."""
import html
import json
from pathlib import Path
import re
import subprocess
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
import threading

root=Path(__file__).resolve().parents[1];out=root/'audit/memory-window';out.mkdir(exist_ok=True)
mock=r'''<script>
let active='aaaaaaaaaaaaaa';let offline=false;let pendingResolve=null;let delayRound=false;const calls=[];
const memory={state:'available',source:'round-memory.json',modified_at:'2026-10-22T13:00:00Z',data:{last_processed_round:27,errors:[{label:'verification_session',status:'OPEN',repeat_count:8,first_round:20,last_round:27,detail:'Permission denied'},{label:'build',status:'RESOLVED',repeat_count:1,first_round:1,last_round:1,resolved_round:2,resolution:'Independent PASS'}],recent_agent_notes:[{source_round:27,claims:{summary:'<img src=x onerror="window.xss=true">',next_steps:['REQ-123'],decisions:['bounded approach'],errors_and_solutions:[{error:'wall',solution:'backoff',evidence:'claim'}]}}]}};
const overview={current:memory,rounds:[{round:27,verification_status:'FAIL',memory_available:false},{round:1,verification_status:'PASS',memory_available:true}],retention_note:'Up to 30 errors and 8 notes'};
const detail=n=>({round:n,memory:n===1?{...memory,source:'round-01/round-memory.json',data:{...memory.data,snapshot_phase:'after_verification',snapshot_at:'2026-10-22T13:00:00Z'}}:{state:'missing',source:'round-27/round-memory.json'},current_memory_for_round:{...memory,provenance:'Текущая память; это не исторический снимок выбранного раунда.'},verification:{state:'available',source:'verification_complete.json',data:{status:'PASS',checks:[{label:'gradle_build',status:'PASS'}]}},acceptance:{state:'available',source:'acceptance.json',data:{summary:{total:240,test_passed:0,not_tested:240}}},agent:{state:'available',source:'codex_result.json',data:{status:'PASS',selected_model:'claude'}},handoff:{state:'missing'},summary:{state:'available',source:'codex.jsonl',text:'public final'},retention_note:'Unverified agent claims'});
window.fetch=async(url,options={})=>{calls.push({url,method:options.method||'GET'});if(offline)throw Error('offline');if(delayRound&&url.endsWith('/rounds/27'))return new Promise(resolve=>pendingResolve=resolve);const match=url.match(/rounds\/(\d+)$/);return {ok:true,json:async()=>match?detail(Number(match[1])):overview}};
</script>'''
checks=r'''<output id="memory-selftest"></output><script>
const pause=()=>new Promise(resolve=>setTimeout(resolve,40));
setTimeout(async()=>{const checks=[];const test=(name,condition)=>{checks.push({name,status:condition?'PASS':'FAIL'});if(!condition)throw Error(name)};
try{
document.getElementById('openMemory').click();await pause();
const dialog=document.getElementById('memoryDialog'),content=document.getElementById('memoryContent'),select=document.getElementById('memoryRound');
test('opens-accessible-dialog',dialog.open&&dialog.getAttribute('aria-labelledby')==='memoryTitle');
test('current-filled-counts',content.textContent.includes('Открытых ошибок: 1')&&content.textContent.includes('Исправленных: 1'));
test('notes-are-text-not-html',!content.querySelector('img')&&!window.xss&&content.textContent.includes('<img'));
test('proposed-solutions-visible',content.textContent.includes('backoff')&&content.textContent.includes('REQ-123'));
select.value='27';select.dispatchEvent(new Event('change'));await pause();
test('legacy-memory-absence-explicit',content.textContent.includes('Исторический снимок памяти отсутствует')&&content.textContent.includes('не исторический снимок'));
test('independent-success-and-acceptance-visible',content.textContent.includes('gradle_build')&&content.textContent.includes('подтверждено: 0'));
select.value='1';select.dispatchEvent(new Event('change'));await pause();
test('actual-snapshot-phase-visible',content.textContent.includes('Снимок после проверки'));
const refresh=[...dialog.querySelectorAll('button')].find(x=>x.textContent==='Обновить');refresh.click();await pause();
test('refresh-preserves-selected-round',select.value==='1');
test('refresh-preserves-media-dom',document.getElementById('fixtureVideo')===window.savedVideo);
const before=content.textContent;offline=true;refresh.click();await pause();
test('offline-keeps-last-data',content.textContent===before&&document.getElementById('memoryNotice').textContent.includes('устарели'));offline=false;
delayRound=true;select.value='27';select.dispatchEvent(new Event('change'));await pause();
active='bbbbbbbbbbbbbb';window.memorySwitchJob(active);await pause();
pendingResolve({ok:true,json:async()=>detail(27)});await pause();delayRound=false;
test('late-previous-job-round-discarded',select.value==='current'&&!content.textContent.includes('Исторический снимок памяти отсутствует'));
dialog.close();const count=calls.length;await pause();test('no-job-mutations',calls.every(x=>x.method==='GET'));
document.getElementById('memory-selftest').textContent=JSON.stringify({status:'PASS',checks});
}catch(error){document.getElementById('memory-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},100);
</script>'''
page='<!doctype html><html><head><meta charset="utf-8"><style>body{background:#111927;color:#e9eef9;font:16px Arial}.panel{padding:20px}.row{display:flex;gap:12px}button,select{padding:12px;background:#22324a;color:white;border:1px solid #405174;border-radius:8px}label{display:flex;flex-direction:column;gap:8px}'+(root/'app/memory.css').read_text(encoding='utf-8')+'</style>'+mock+'</head><body><div id="files"><video id="fixtureVideo"></video></div><section id="agentHistory"></section><script>window.savedVideo=document.getElementById("fixtureVideo")</script><script>'+(root/'app/memory.js').read_text(encoding='utf-8')+'</script>'+checks+'</body></html>'
(out/'fixture.html').write_text(page,encoding='utf-8')
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(out),**kwargs)
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    result=subprocess.run(['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-extensions','--user-data-dir='+str(out/'edge-profile'),'--virtual-time-budget=3000','--window-size=1440,1100','--dump-dom',f'http://127.0.0.1:{server.server_port}/fixture.html'],capture_output=True,timeout=45)
finally:server.shutdown();server.server_close()
rendered=result.stdout.decode('utf-8',errors='replace');(out/'dom.html').write_text(rendered,encoding='utf-8')
match=re.search(r'<output id="memory-selftest">(.*?)</output>',rendered,re.S)
if not match:raise RuntimeError('Browser returned no selftest result')
report=json.loads(html.unescape(match[1]));(out/'browser-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
if report['status']!='PASS':raise RuntimeError('Memory window checks failed')
