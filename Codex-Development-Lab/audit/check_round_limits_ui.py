"""Independent headless Edge checks; intercepted synthetic API, never live Lab jobs."""
import html
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
folder = root / 'audit' / 'round-limits-inspection'
folder.mkdir(exist_ok=True)
page = (root / 'app/index.html').read_text(encoding='utf-8')
mock = r'''<script>
const fixtureJob={id:'aaaaaaaaaaaaaa',title:'Isolated round limits fixture',status:'FINISHED',phase:'max_rounds_reached',round:25,max_rounds:25};
const posts=[];
window.fetch=async(path,options)=>{
let result=[];
if(options?.method==='POST'){
 posts.push({path,limit:options.body?.get('max_rounds'),additional:options.body?.get('additional_rounds')});
 if(path.endsWith('/round-limit')){fixtureJob.max_rounds=Number(options.body.get('max_rounds'));fixtureJob.status='USER_PAUSED';fixtureJob.phase='round_limit_updated';result=fixtureJob;}
 else if(path==='/api/projects')result={project_id:'fixture-project',job_id:fixtureJob.id};
 else if(path.endsWith('/extend-rounds')){fixtureJob.max_rounds+=Number(options.body.get('additional_rounds'));fixtureJob.status='QUEUED';result=fixtureJob;}
 else throw Error('Unexpected fixture POST '+path);
} else if(path==='/api/jobs')result=[fixtureJob];
else if(path==='/api/jobs/'+fixtureJob.id)result=fixtureJob;
else if(path.endsWith('/verification'))result={status:'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',gameplay_scenarios:[]};
else if(path.endsWith('/agent-history'))result={attempts:[],truncated:false};
return {ok:true,status:200,text:async()=>JSON.stringify(result)};
};
</script>'''
checks = r'''<output id="limits-selftest"></output><script>
setTimeout(async()=>{const passed=[];const check=(value,label)=>{if(!value)throw Error(label);passed.push(label)};
try {
 await refresh();
 check(!q('rounds').hasAttribute('max'),'new-project:no-old-cap');
 check(!q('additionalRounds').hasAttribute('max'),'extension:no-old-cap');
 check(!q('extendRounds').hidden,'round-25:extension-visible');
 q('additionalRounds').value='100';await refresh();
 check(q('additionalRounds').value==='100','poll:extension-user-value-retained');
 q('roundLimitValue').value='200';await refresh();
 check(q('roundLimitValue').value==='200','poll:limit-user-value-retained');
 q('roundLimitUnlimited').checked=true;q('roundLimitUnlimited').onchange();
 check(q('roundLimitValue').disabled,'unlimited:numeric-input-disabled');
 await q('roundLimitSettings').onsubmit({preventDefault(){}});
 check(posts.length===1&&posts[0].limit==='0','unlimited:zero-posted');
 check(fixtureJob.status==='USER_PAUSED','unlimited:does-not-start-model');
 check(q('status').textContent.includes('25/∞'),'unlimited:infinity-in-status');
 check(!q('resume').disabled,'unlimited:explicit-resume-enabled');
 check(q('extendRounds').hidden,'unlimited:finite-extension-hidden');
 q('roundLimitUnlimited').checked=false;q('roundLimitUnlimited').onchange();
 q('roundLimitValue').value='200';
 await q('roundLimitSettings').onsubmit({preventDefault(){}});
 check(posts[1].limit==='200','finite:custom-limit-posted');
 fixtureJob.status='RUNNING';await refresh();
 check(q('saveRoundLimit').disabled&&q('roundLimitUnlimited').disabled&&q('roundLimitValue').disabled,'running:pause-required');
 fixtureJob.status='FINISHED';fixtureJob.phase='max_rounds_reached';fixtureJob.round=200;fixtureJob.max_rounds=200;await refresh();
 q('additionalRounds').value='100';await q('extendRounds').onsubmit({preventDefault(){}});
 check(posts[2].additional==='100'&&fixtureJob.max_rounds===300,'extension:custom-addition-beyond-twenty');
 q('newUnlimited').checked=true;q('newUnlimited').onchange();
 check(q('rounds').disabled,'new-project:unlimited-input-disabled');
 await q('form').onsubmit({preventDefault(){}});
 check(posts[3].path==='/api/projects'&&posts[3].limit==='0','new-project:unlimited-zero-posted');
 q('limits-selftest').textContent=JSON.stringify({status:'PASS',checks:passed});
}catch(error){q('limits-selftest').textContent=JSON.stringify({status:'FAIL',error:String(error),checks:passed});}
},150);
</script>'''
page = page.replace('<script>', mock + '<script>', 1).replace('</body>', checks + '</body>')
fixture = folder / 'fixture.html'
fixture.write_text(page, encoding='utf-8')
edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
result = subprocess.run([edge, '--headless=new', '--disable-gpu', '--no-first-run',
    '--disable-background-networking', '--disable-extensions',
    '--user-data-dir=' + str(folder / 'edge-profile'), '--virtual-time-budget=4000',
    '--window-size=1440,1100', '--dump-dom', fixture.as_uri()], capture_output=True, timeout=40)
rendered = result.stdout.decode('utf-8', errors='replace')
(folder / 'browser-dom.html').write_text(rendered, encoding='utf-8')
(folder / 'browser.log').write_bytes(result.stderr)
match = re.search(r'<output id="limits-selftest">(.*?)</output>', rendered, re.S)
if not match:
    raise RuntimeError('Browser returned no test result')
report = json.loads(html.unescape(match.group(1)))
(folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
if report['status'] != 'PASS':
    raise RuntimeError('Browser checks failed')
