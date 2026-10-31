"""Offline real Edge checks for the activity card; live lab is never contacted."""
import html
import json
from pathlib import Path
import re
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import threading

root = Path(__file__).resolve().parents[1]
folder = root / 'audit' / 'activity-ui'
folder.mkdir(exist_ok=True)
page = (root / 'app/index.html').read_text(encoding='utf-8')
mock = r'''<script>
const fixtureJob={id:'aaaaaaaaaaaaaa',title:'Activity fixture',status:'RUNNING',phase:'codex_development',round:18,max_rounds:30,updated_at:new Date().toISOString()};
const fixtureOther={...fixtureJob,id:'bbbbbbbbbbbbbb',title:'Other fixture'};
const fixtureActivity={job_id:fixtureJob.id,status:'RUNNING',phase:'codex_development',stage:'reading_spec',title:'Изучает ТЗ',detail:'Команда чтения требований.',current_round:19,max_rounds:30,started_at:new Date(Date.now()-125000).toISOString(),observed_at:new Date().toISOString(),source:'agent_cli',stale:false};
const fixtureRequests=[];let delayActivity=null;let failActivity=false;
window.fetch=async function(path,options){fixtureRequests.push({path,method:options?.method||'GET'});if(options?.method&&options.method!=='GET')throw Error('Fixture forbids mutations');let result=[];
if(path==='/api/jobs')result=[fixtureJob,fixtureOther];
else if(path==='/api/jobs/'+fixtureJob.id)result=fixtureJob;
else if(path==='/api/jobs/'+fixtureOther.id)result=fixtureOther;
else if(path.endsWith('/activity')){if(failActivity)throw Error('Fixture timeout');if(delayActivity)return await delayActivity;result=path.includes(fixtureOther.id)?{...fixtureActivity,job_id:fixtureOther.id,title:'Другой проект'}:fixtureActivity;}
else if(path.endsWith('/verification'))result={status:'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED'};
else if(path.endsWith('/agent-history'))result={attempts:[],truncated:false};
return {ok:true,status:200,text:async()=>JSON.stringify(result)};};
</script>'''
checks = r'''<output id="activity-selftest"></output><script>
setTimeout(async()=>{const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label)};
try{
await refresh();await refreshCurrentActivity(fixtureJob);
check(q('activityTitle').textContent==='Изучает ТЗ','activity:shows-observed-action');
check(q('activityDetail').textContent.includes('чтения'),'activity:explanation');
check(q('activityMeta').textContent.includes('19'),'round:current-development-number');
const meta=q('activityMeta').textContent;
fixtureJob.updated_at=new Date(Date.now()+10000).toISOString();await refreshCurrentActivity(fixtureJob);
check(q('activityMeta').textContent===meta,'heartbeat:does-not-reset-phase-duration');
renderCurrentActivity({...fixtureActivity,title:'<img src=x onerror=alert(1)>',detail:'<script>alert(1)<\/script>'});
check(!q('activityCard').querySelector('img,script'),'untrusted:no-html-execution');
check(q('activityTitle').textContent.includes('<img'),'untrusted:literal-text');
renderCurrentActivity(fixtureActivity);failActivity=true;await refreshCurrentActivity(fixtureJob);failActivity=false;
check(q('activityTitle').textContent==='Изучает ТЗ','timeout:keeps-last-known-action');
check(q('activityFreshness').textContent.length>0,'timeout:warns-not-fresh');
let release;delayActivity=new Promise(resolve=>{release=resolve});const pending=refreshCurrentActivity(fixtureJob);
active=fixtureOther.id;activitySwitchJob(active);release({ok:true,status:200,text:async()=>JSON.stringify(fixtureActivity)});await pending;delayActivity=null;
check(q('activityTitle').textContent!=='Изучает ТЗ','job-switch:rejects-delayed-old-result');
await refreshCurrentActivity(fixtureOther);check(q('activityTitle').textContent==='Другой проект','job-switch:correct-new-project');
check(fixtureRequests.every(r=>r.path.startsWith('/api/jobs')&&r.method==='GET'),'fixture:read-only-local-endpoints');
q('activity-selftest').textContent=JSON.stringify({status:'PASS',checks});
}catch(error){q('activity-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},150);
</script>'''
page = page.replace('<script>', mock + '<script>', 1).replace('</body>', checks + '</body>')
(folder / 'fixture.html').write_text(page, encoding='utf-8')

class FixtureHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(folder), **kwargs)

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    result = subprocess.run([
        'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
        '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
        '--disable-extensions', '--user-data-dir=' + str(folder / 'edge-profile'),
        '--virtual-time-budget=3000', '--window-size=1440,1100', '--dump-dom',
        f'http://127.0.0.1:{server.server_port}/fixture.html',
    ], capture_output=True, timeout=45)
finally:
    server.shutdown()
    server.server_close()
rendered = result.stdout.decode('utf-8', errors='replace')
(folder / 'dom.html').write_text(rendered, encoding='utf-8')
(folder / 'browser.log').write_bytes(result.stderr)
match = re.search(r'<output id="activity-selftest">(.*?)</output>', rendered, re.S)
if not match:
    raise RuntimeError('Browser fixture did not return results')
report = json.loads(html.unescape(match.group(1)))
(folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
if report['status'] != 'PASS':
    raise RuntimeError('Activity UI checks failed')
