"""Offline Edge fixture for acceptance UI. Never contacts the running lab."""
import html
import json
from pathlib import Path
import re
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import threading

root = Path(__file__).resolve().parents[1]
folder = root / 'audit' / 'acceptance-ui'
folder.mkdir(exist_ok=True)
page = (root / 'app/index.html').read_text(encoding='utf-8')
mock = r'''<script>
const fixtureJob={id:'aaaaaaaaaaaaaa',title:'Fixture only',status:'USER_PAUSED',phase:'paused',round:5,max_rounds:15};
const fixtureOther={...fixtureJob,id:'bbbbbbbbbbbbbb',title:'Other fixture'};
const fakeVideo='/api/jobs/'+fixtureJob.id+'/files/round-05/attack.mp4';
const fixtureAcceptance={spec_sha256:'0123456789abcdef',status:'IN_PROGRESS',coverage_note:'Fixture: incomplete coverage.',plan_notes:['Review source coverage.'],summary:{total:3,test_passed:1,failed:0,not_tested:2,without_video:1},requirements:[
{id:'REQ-001',title:'<img src=x onerror=alert(1)>',section:'Combat',source:{start_line:8,end_line:10,text:'Attack nearby hostile mobs. <script>alert(1)<\/script>'},expected:'Enemy health decreases.',kind:'gameplay',status:'TEST_PASSED',detail:'Executable assertions passed.',video_required:true,scenarios:[{id:'attack',status:'PASS',detail:'Health assertion.',assertion_evidence:['health < startHealth'],log_url:'/api/jobs/'+fixtureJob.id+'/files/round-05/test.log',video_url:fakeVideo}]},
{id:'REQ-002',title:'Saruman integration',section:'API',source:{start_line:20,end_line:21,text:'External integration must work.'},expected:'API calls succeed.',kind:'api',status:'NOT_TESTED',video_required:false,scenarios:[{id:'api',status:'NOT_TESTED',log_url:'javascript:alert(1)',video_url:'https://attacker.invalid/video.mp4'}]},
{id:'REQ-003',title:'Retreat',section:'Combat',source:{start_line:30,end_line:31,text:'Retreat at low health.'},kind:'gameplay',status:'NOT_TESTED',video_required:true,scenarios:[]}
]};
let delayAcceptance=null;const fixtureRequests=[];
window.fetch=async function(path,options){fixtureRequests.push(path);if(options?.method==='POST')throw Error('Fixture forbids mutations');let result=[];
if(path==='/api/jobs')result=[fixtureJob,fixtureOther];
else if(path==='/api/jobs/'+fixtureJob.id)result=fixtureJob;
else if(path==='/api/jobs/'+fixtureOther.id)result=fixtureOther;
else if(path.endsWith('/acceptance')){if(delayAcceptance)return await delayAcceptance;result=path.includes(fixtureOther.id)?{...fixtureAcceptance,requirements:[],summary:{total:0}}:fixtureAcceptance;}
else if(path.endsWith('/verification'))result={status:'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED'};
else if(path.endsWith('/agent-history'))result={attempts:[],truncated:false};
return {ok:true,status:200,text:async()=>JSON.stringify(result)};};
</script>'''
checks = r'''<output id="acceptance-selftest"></output><script>
setTimeout(async()=>{const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label)};
try{
await refresh();check(!fixtureRequests.some(p=>p.endsWith('/acceptance')),'closed:no-extra-acceptance-request');
q('acceptanceOpen').click();await refreshAcceptance();await new Promise(r=>setTimeout(r,20));
check(q('acceptanceDialog').open,'dialog:opens');
check(q('acceptanceRows').children.length===3,'requirements:all-source-items');
check(q('acceptanceRows').textContent.includes('строки 8–10'),'source:line-citation');
check(q('acceptanceRows').textContent.includes('<img src=x'),'untrusted:literal-text');
check(!q('acceptanceRows').querySelector('img,script'),'untrusted:no-html-execution');
check(q('acceptanceCaution').textContent.includes('не доказывает полноту'),'status:no-false-readiness');
check(!q('acceptanceRows').querySelector('video'),'videos:lazy');
check(q('acceptanceRows').querySelectorAll('a').length===1,'evidence:reject-external-and-javascript');
q('acceptanceRows').querySelector('button').click();const video=q('acceptanceRows').querySelector('video');
check(video&&video.getAttribute('src').endsWith('/attack.mp4'),'video:explicit-open');
fixtureAcceptance.requirements[0].detail='New status';fixtureAcceptance.requirements[2].status='FAIL';await refreshAcceptance();
check(q('acceptanceRows').querySelector('video')===video,'poll:preserves-player');
check(q('acceptanceRows').textContent.includes('New status'),'poll:updates-status-text');
q('acceptanceFilter').value='NO_VIDEO';q('acceptanceFilter').dispatchEvent(new Event('change'));
check([...q('acceptanceRows').children].filter(x=>!x.hidden).length===1,'filter:missing-video');
q('acceptanceFilter').value='ALL';q('acceptanceSearch').value='Saruman';q('acceptanceSearch').dispatchEvent(new Event('input'));
check([...q('acceptanceRows').children].filter(x=>!x.hidden).length===1,'search:requirement-text');
q('acceptanceSearch').value='';q('acceptanceSearch').dispatchEvent(new Event('input'));
let paused=0;video.pause=()=>paused++;q('acceptanceClose').click();await new Promise(r=>setTimeout(r,20));
check(paused>0&&!q('acceptanceDialog').open,'close:pauses-video');check(document.activeElement===q('acceptanceOpen'),'close:restores-focus');
q('acceptanceOpen').click();await new Promise(r=>setTimeout(r,20));
let release;delayAcceptance=new Promise(r=>{release=r});const request=refreshAcceptance();
active=fixtureOther.id;acceptanceSwitchJob(active);release({ok:true,status:200,text:async()=>JSON.stringify(fixtureAcceptance)});await request;delayAcceptance=null;
check(!q('acceptanceRows').children.length,'job-switch:discards-delayed-response');
await refreshAcceptance();check(q('acceptanceIdentity').textContent.includes(fixtureOther.id),'job-switch:correct-identity');
check(fixtureRequests.every(p=>p.startsWith('/api/jobs')),'fixture:read-only-local-endpoints');
q('acceptanceClose').click();q('acceptance-selftest').textContent=JSON.stringify({status:'PASS',checks});
}catch(error){q('acceptance-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks})}
},150);
</script>'''
page = page.replace('<script>', mock + '<script>', 1).replace('</body>', checks + '</body>')
fixture = folder / 'fixture.html'
fixture.write_text(page, encoding='utf-8')
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
match = re.search(r'<output id="acceptance-selftest">(.*?)</output>', rendered, re.S)
if not match:
    raise RuntimeError('Browser fixture did not return results')
report = json.loads(html.unescape(match.group(1)))
(folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
if report['status'] != 'PASS':
    raise RuntimeError('Acceptance UI checks failed')
