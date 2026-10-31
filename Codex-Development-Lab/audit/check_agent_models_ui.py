"""Offline Edge checks for per-role model selection and request isolation."""
import html
import json
import re
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / 'audit' / 'agent-models-ui'
folder.mkdir(exist_ok=True)
script = (root / 'app/agent-models.js').read_text(encoding='utf-8')
mock = r'''
let active='aaaaaaaaaaaaaa';const writes=[];let delayed=null,delayNext=false;
const data={status:'USER_PAUSED',editable:true,roles:['planner','coder'],models:{planner:{},coder:{provider:'codex',model:'existing-coder',reasoning_effort:'medium'}},catalogue:{
 codex:['gpt-6.1-sol','gpt-6-astra','gpt-6-sol','gpt-6-luna','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna'].map(id=>({id,efforts:['default','low','medium','high','xhigh','max',...(!id.endsWith('-luna')?['ultra']:[])]})),
 dslab:['claude-sonnet-5-5','claude-opus-5-5'].map(id=>({id,efforts:['default','low','medium','high','xhigh','max']})),
 litellm:[{id:'Qwen/Qwen3.8-27B',efforts:['default','none','low','medium','high']},{id:'Qwen/Qwen3-Next-80B-A3B-Instruct-FP8',efforts:['default','none']}]}};
window.fetch=async(path,options)=>{
 if(options?.method){writes.push({path,body:JSON.parse(options.body)});data.models=JSON.parse(options.body).models}
 if(delayNext){delayNext=false;return await new Promise(resolve=>delayed=resolve)}
 return {ok:true,json:async()=>structuredClone(data)};
};
'''
checks = r'''
setTimeout(async()=>{
 const checks=[],wait=()=>new Promise(r=>setTimeout(r,40));
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label)};
 try{
  const panel=document.getElementById('agentModelsPanel'),player=document.getElementById('existingVideo');
  check(!document.getElementById('configure-reviewer')&&panel.textContent.includes('программно'),'only-real-stage-roles-no-fake-verifier');
  document.getElementById('configure-planner').click();const dialog=document.getElementById('modelDialog-planner'),fields=dialog.querySelectorAll('select'),model=fields[1],effort=fields[2];
  check(dialog.open&&fields.length===3&&!dialog.querySelector('input,datalist'),'real-model-dropdown');
  fields[0].value='codex';fields[0].dispatchEvent(new Event('change'));
  check(model.options.length===8&&model.value===''&&[...model.options].some(x=>x.value==='gpt-6.1-sol'),'codex-seven-models-and-cli-default');
  model.value='gpt-6.1-sol';model.dispatchEvent(new Event('change'));effort.value='medium';
  check(!effort.selectedOptions[0].hidden&&effort.value==='medium','codex-effort-uses-catalogue');
  effort.value='ultra';check(effort.value==='ultra'&&!effort.selectedOptions[0].hidden,'codex-ultra-selectable');
  model.value='gpt-6-luna';model.dispatchEvent(new Event('change'));
  check([...effort.options].find(x=>x.value==='ultra').hidden&&effort.value==='default','luna-ultra-unavailable');
  fields[0].value='dslab';fields[0].dispatchEvent(new Event('change'));
  check(model.value==='claude-sonnet-5-5'&&model.options.length===2&&[...effort.options].filter(x=>!x.hidden).length===6,'dslab-auto-selection-and-six-efforts');
  fields[0].value='litellm';fields[0].dispatchEvent(new Event('change'));
  check(model.value==='Qwen/Qwen3.8-27B'&&model.options.length===2,'catalogue-matches-provider-and-first-model-selected');
  check(effort.value==='default'&&[...effort.options].filter(x=>!x.hidden).map(x=>x.value).join(',')==='default','litellm-default-only-even-if-metadata-lists-more');
  dialog.querySelector('form').dispatchEvent(new Event('submit',{cancelable:true}));await wait();
  check(writes.length===1&&writes[0].body.models.planner.model==='Qwen/Qwen3.8-27B'&&writes[0].body.models.coder.model==='existing-coder','save-one-role-preserves-other-model');
  fields[0].value='';dialog.querySelector('form').dispatchEvent(new Event('submit',{cancelable:true}));await wait();
  check(Object.keys(writes[1].body.models.planner).length===0,'inheritance-saves-empty-override');
  dialog.close();document.getElementById('configure-coder').click();const coder=document.getElementById('modelDialog-coder').querySelectorAll('select')[1];
  check(coder.value==='existing-coder'&&coder.selectedOptions[0].textContent.includes('вне каталога'),'historical-model-preserved');
  document.getElementById('modelDialog-coder').close();delayNext=true;active='bbbbbbbbbbbbbb';window.agentModelsSwitchJob(active);await wait();
  active='cccccccccccccc';window.agentModelsSwitchJob(active);await wait();
  delayed({ok:true,json:async()=>({...data,models:{coder:{model:'STALE-JOB'}}})});await wait();
  check(!panel.textContent.includes('STALE-JOB'),'stale-project-response-rejected');
  data.editable=false;data.status='RUNNING';active='dddddddddddddd';window.agentModelsSwitchJob(active);await wait();
  check(document.getElementById('configure-planner').disabled&&document.getElementById('configure-coder').disabled,'active-job-settings-disabled');
  check(document.getElementById('existingVideo')===player,'media-node-preserved');
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
    raise RuntimeError('Browser returned no agent-models UI result')
report = json.loads(html.unescape(match[1]))
(folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
assert report['status'] == 'PASS'
