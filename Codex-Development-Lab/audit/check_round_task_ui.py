"""Offline browser checks: current task, historical assignment, safe rendering."""
import html,json,re,subprocess,threading
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

root=Path(__file__).resolve().parents[1]
folder=root/'audit'/'round-task-ui';folder.mkdir(exist_ok=True)
script=(root/'app/memory.js').read_text(encoding='utf-8')
mock=r'''
let active='aaaaaaaaaaaaaa';const calls=[];
const task={task_assignment:{current_task:{title:'<img src=x onerror=alert(1)>',requirement_ids:['REQ-one']}},
 round_action_round:6,round_action:{kind:'repair_scenario',target:'client-melee',diagnostic:'actual failure <img src=x>'}};
const current={state:'available',source:'round-memory.json',data:{...task,errors:[],recent_agent_notes:[]}};
window.fetch=async(path,options)=>{
 calls.push(path);if(options?.method)throw Error('Read only fixture');let data;
 if(path.endsWith('/round-task'))data={status:'USER_PAUSED',task:current};
 else if(path.endsWith('/memory'))data={current,rounds:[{round:1,verification_status:'FAIL',task_title:'Original task',action_target:'original-target'}]};
 else if(path.endsWith('/rounds/1'))data={round:1,memory:{state:'missing'},round_task:{state:'available',source:'round-01/round-memory-start.json',
  data:{assignment:{current_task:{title:'Original task'}},round_action:{kind:'repair_scenario',target:'original-target'}},provenance:'Historical start'},
  verification:{state:'missing'},acceptance:{state:'missing'},summary:{state:'missing'}};
 else throw Error('Unexpected endpoint '+path);
 return {ok:true,json:async()=>data};
};
'''
checks=r'''
setTimeout(async()=>{
 const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label)};
 try{
  const panel=document.getElementById('roundTaskPanel');
  check(panel.textContent.includes('6')&&panel.textContent.includes('client-melee'),'current-task-visible-before-opening-dialog');
  check(panel.textContent.includes('actual failure')&&panel.textContent.includes('<img'),'exact-error-is-visible-as-text');
  check(!panel.querySelector('img,script'),'task-content-cannot-inject-html');
  check(panel.textContent.includes('Продолжить'),'paused-assignment-is-not-claimed-as-running');
  const player=document.getElementById('existingVideo');
  document.getElementById('openMemory').click();await new Promise(r=>setTimeout(r,60));
  const select=document.getElementById('memoryRound');
  check(select.options[1].textContent.includes('original-target'),'round-list-shows-assignment');
  select.value='1';select.dispatchEvent(new Event('change'));await new Promise(r=>setTimeout(r,60));
  const content=document.getElementById('memoryContent').textContent;
  check(content.includes('Original task')&&content.includes('original-target')&&!content.includes('client-melee'),'historical-round-uses-its-own-assignment');
  check(document.getElementById('existingVideo')===player,'task-refresh-preserves-existing-media');
  check(calls.every(p=>p.startsWith('/api/jobs/')),'read-only-local-endpoints');
  document.getElementById('result').textContent=JSON.stringify({status:'PASS',checks});
 }catch(error){document.getElementById('result').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},600);
'''
page='<!doctype html><html><meta charset="utf-8"><body><div id="activityCard"></div><section id="agentHistory"></section><video id="existingVideo"></video><output id="result"></output><script>'+mock+'</script><script>'+script+'</script><script>'+checks+'</script></body></html>'
(folder/'fixture.html').write_text(page,encoding='utf-8')
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(folder),**kwargs)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 result=subprocess.run(['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-extensions',
  '--user-data-dir='+str(folder/'edge-profile'),'--virtual-time-budget=3000','--dump-dom',
  'http://127.0.0.1:'+str(server.server_port)+'/fixture.html'],capture_output=True,timeout=45)
finally:server.shutdown();server.server_close()
dom=result.stdout.decode('utf-8',errors='replace')
(folder/'dom.html').write_text(dom,encoding='utf-8');(folder/'browser.log').write_bytes(result.stderr)
match=re.search(r'<output id="result">(.*?)</output>',dom,re.S)
if not match:raise RuntimeError('Browser returned no task UI result')
report=json.loads(html.unescape(match[1]))
(folder/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report));assert report['status']=='PASS'
