import html,json,re,subprocess,threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
root=Path(__file__).resolve().parents[1]
folder=root/'audit/dslab/ui'
folder.mkdir(parents=True,exist_ok=True)
page=(root/'app/index.html').read_text(encoding='utf-8')
mock = "<script>\nconst fakeJob={id:'aaaaaaaaaaaaaa',title:'Claude fixture',provider:'dslab',model:'claude-opus-5-5',reasoning_effort:'max',status:'USER_PAUSED',phase:'paused',round:19,max_rounds:30,dslab_token_budget:123456,dslab_max_output_tokens:8192,dslab_max_calls:17};\nlet failUsage=false,usageOutput=10,slowUsage=null;const requests=[];\nconst fixtureReport=()=>({available:true,active_provider:'dslab',totals:{requests:1,input_tokens:100,output_tokens:usageOutput,cache_creation_input_tokens:20,cache_read_input_tokens:30,estimated_rub:0.1234,billing_unknown_requests:0},requests:[{task_id:'<img src=x onerror=alert(1)>',model:fakeJob.model,status:'streaming',input_tokens:100,output_tokens:usageOutput,estimated_rub:.1234}]});\nwindow.fetch=async(path,options)=>{requests.push({path,method:options?.method||'GET'});let data=[];\nif(path.endsWith('/usage')){if(failUsage)throw Error('offline');if(slowUsage)return await slowUsage;data=fixtureReport();}\nelse if(path==='/api/jobs')data=[fakeJob];else if(path==='/api/jobs/'+fakeJob.id)data=fakeJob;\nelse if(path.endsWith('/agent-history'))data={attempts:[]};else if(path.endsWith('/verification'))data={status:'INCOMPLETE'};else if(path.endsWith('/activity'))data={title:'Paused',stage:'paused',current_round:19};\nreturn {ok:true,status:200,text:async()=>JSON.stringify(data)}};\n</script>"
checks = '<output id="provider-selftest"></output><script>\nsetTimeout(async()=>{const checks=[];const check=(v,name)=>{if(!v)throw Error(name);checks.push(name)};\ntry{\nawait refresh();await refreshUsage();\ncheck(q(\'savedProvider\').value===\'dslab\',\'saved-provider-restored\');\ncheck(q(\'updateModel\').value===\'claude-opus-5-5\',\'saved-model-restored\');\ncheck(q(\'updateReasoning\').value===\'max\',\'claude-max-effort\');\ncheck(q(\'updateSettings\').elements.dslab_token_budget.value===\'123456\',\'budget-restored\');\nq(\'newProvider\').value=\'dslab\';q(\'newProvider\').onchange();q(\'newClaude\').value=\'claude-opus-5-5\';q(\'newClaude\').onchange();\nconst data=new FormData(q(\'form\'));check(data.get(\'provider\')===\'dslab\'&&data.get(\'model\')===\'claude-opus-5-5\',\'new-project-form-native-payload\');\ncheck(q(\'usagePanel\').textContent.includes(\'160\'),\'four-token-categories-total\');\ncheck(!q(\'usageDialog\').querySelector(\'img\'),\'request-names-rendered-as-text\');\nconst video=document.createElement(\'video\');q(\'files\').append(video);usageOutput=99;await refreshUsage();\ncheck(q(\'files\').contains(video),\'usage-poll-keeps-media-dom\');check(q(\'usagePanel\').textContent.includes(\'249\'),\'streaming-output-updated\');\nconst before=q(\'usagePanel\').textContent;failUsage=true;for(let i=0;i<15;i++)await refreshUsage();\ncheck(q(\'usagePanel\').textContent.includes(\'249\'),\'offline-keeps-last-totals\');\ncheck(q(\'usagePanel\').querySelectorAll(\'.media-warning\').length===1,\'offline-warning-bounded\');\nfailUsage=false;let release;slowUsage=new Promise(r=>{release=r});const pending=refreshUsage();active=\'bbbbbbbbbbbbbb\';release({ok:true,status:200,text:async()=>JSON.stringify(fixtureReport())});await pending;slowUsage=null;\ncheck(usageJob!==\'bbbbbbbbbbbbbb\'||!q(\'usagePanel\').textContent.includes(\'249\'),\'stale-result-not-assigned-new-job\');\ncheck(requests.every(r=>r.method===\'GET\'),\'fixture-no-mutations-or-paid-calls\');\nq(\'provider-selftest\').textContent=JSON.stringify({status:\'PASS\',checks});\n}catch(error){q(\'provider-selftest\').textContent=JSON.stringify({status:\'FAIL\',error:String(error),checks})}\n},300);\n</script>'
page=page.replace('<script>',mock+'<script>',1).replace('<script src="/provider.js"></script>','<script>'+ (root/'app/provider.js').read_text(encoding='utf-8')+'</script>').replace('</body>',checks+'</body>')
(folder/'fixture.html').write_text(page,encoding='utf-8')
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
match = re.search(r'<output id="provider-selftest">(.*?)</output>', rendered, re.S)
if not match:
    raise RuntimeError('Browser fixture did not return results')
report = json.loads(html.unescape(match.group(1)))
(folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
if report['status'] != 'PASS':
    raise RuntimeError('Provider UI checks failed')
