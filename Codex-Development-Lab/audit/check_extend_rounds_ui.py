"""Independent Edge checks. All API traffic is intercepted with synthetic state."""
import html
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
folder = root / 'audit' / 'extend-rounds-inspection'
folder.mkdir(exist_ok=True)
page = (root / 'app/index.html').read_text(encoding='utf-8')
mock = r'''<script>
const fixtureJob={id:'aaaaaaaaaaaaaa',title:'Isolated UI fixture',status:'FINISHED',phase:'max_rounds_reached',round:5,max_rounds:5};
let postCount=0,releasePost=null,postedAmount=null;
window.fetch=async(path,options)=>{
let result=[];
if(options?.method==='POST'){
 if(path!=='/api/jobs/'+fixtureJob.id+'/extend-rounds')throw Error('Unexpected fixture mutation');
 postCount++;postedAmount=options.body.get('additional_rounds');
 await new Promise(resolve=>releasePost=resolve);
 fixtureJob.status='QUEUED';fixtureJob.phase='queued_extension';fixtureJob.max_rounds+=Number(postedAmount);
 result=fixtureJob;
} else if(path==='/api/jobs')result=[fixtureJob];
else if(path==='/api/jobs/'+fixtureJob.id)result=fixtureJob;
else if(path.endsWith('/verification'))result={status:'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',gameplay_scenarios:[]};
else if(path.endsWith('/agent-history'))result={attempts:[],truncated:false};
return {ok:true,status:200,text:async()=>JSON.stringify(result)};
};
</script>'''
checks = r'''<output id="extend-selftest"></output><script>
setTimeout(async()=>{const passed=[];const check=(value,label)=>{if(!value)throw Error(label);passed.push(label)};
try {
 await refresh();
 check(!q('extendRounds').hidden,'finished:extension-visible');
 check(q('additionalRounds').value==='5'&&q('additionalRounds').max==='15','finished:default-five-max-fifteen');
 check(q('extendNote').textContent.includes('6'),'finished:next-round-six');
 q('additionalRounds').value='4';await refresh();
 check(q('additionalRounds').value==='4','poll:keeps-user-input');
 q('additionalRounds').value='5';
 const first=q('extendRounds').onsubmit({preventDefault(){}});
 check(q('extendButton').disabled&&q('additionalRounds').disabled,'submit:disabled-while-pending');
 const second=q('extendRounds').onsubmit({preventDefault(){}});await second;
 check(postCount===1,'submit:duplicate-prevented');
 check(postedAmount==='5','submit:form-value-before-disabling');
 releasePost();await first;
 check(q('extendRounds').hidden,'queued:extension-hidden');
 check(fixtureJob.round===5&&fixtureJob.max_rounds===10,'queued:five-of-ten');
 check(!q('extendButton').disabled&&!q('additionalRounds').disabled,'submit:controls-reenabled');
 renderRoundExtension({...fixtureJob,status:'FINISHED',phase:'max_rounds_reached',round:20,max_rounds:20});
 check(q('extendRounds').hidden&&!q('extendLimit').hidden,'twenty:cap-explanation');
 for(const status of ['RUNNING','FAILED','USER_PAUSED','STOPPED']){
  renderRoundExtension({...fixtureJob,status,phase:'max_rounds_reached'});
  check(q('extendRounds').hidden,'ineligible:hidden-'+status);
 }
 renderRoundExtension({...fixtureJob,status:'FINISHED',phase:'accepted'});
 check(q('extendRounds').hidden,'other-finished-phase:hidden');
 q('extend-selftest').textContent=JSON.stringify({status:'PASS',checks:passed});
}catch(error){q('extend-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks:passed});}
},150);
</script>'''
page = page.replace('<script>', mock + '<script>', 1).replace('</body>', checks + '</body>')
fixture = folder / 'fixture.html'
fixture.write_text(page, encoding='utf-8')
edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
result = subprocess.run([edge, '--headless=new', '--disable-gpu', '--no-first-run',
    '--disable-background-networking', '--disable-extensions',
    '--user-data-dir=' + str(folder / 'edge-profile'), '--virtual-time-budget=2000',
    '--window-size=1440,1100', '--dump-dom', fixture.as_uri()], capture_output=True, timeout=40)
rendered = result.stdout.decode('utf-8', errors='replace')
(folder / 'browser-dom.html').write_text(rendered, encoding='utf-8')
(folder / 'browser.log').write_bytes(result.stderr)
match = re.search(r'<output id="extend-selftest">(.*?)</output>', rendered, re.S)
if not match:
    raise RuntimeError('Browser returned no test result')
report = json.loads(html.unescape(match.group(1)))
(folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
if report['status'] != 'PASS':
    raise RuntimeError('Browser checks failed')
