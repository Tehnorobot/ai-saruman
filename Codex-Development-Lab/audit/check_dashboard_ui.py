"""Real Edge, synthetic jobs only. Check navigation, isolation and responsive layout."""
import html,json,re,subprocess,threading,uuid
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

root=Path(__file__).resolve().parents[1]
folder=root/'audit/dashboard-ui';folder.mkdir(exist_ok=True)
catalogue_code="""from common.provider_settings import model_catalogue"""
import sys
sys.path.insert(0,str(root))
from common.provider_settings import model_catalogue

mock=r'''<script>
const errors=[],requests=[];window.addEventListener('error',e=>errors.push(e.message));window.addEventListener('unhandledrejection',e=>errors.push(String(e.reason)));
const jobA={id:'aaaaaaaaaaaaaa',title:'Auto Defense · универсальная защита персонажа Minecraft',status:'USER_PAUSED',phase:'planner_error',round:19,max_rounds:30,provider:'codex',model:'gpt-6.1-sol',reasoning_effort:'medium',role_models:{},dslab_token_budget:1000000,dslab_max_output_tokens:8192,dslab_max_calls:100,last_error:'Планировщик не завершил ответ. Подробная диагностика доступна в истории.'};
const jobB={...jobA,id:'bbbbbbbbbbbbbb',title:'Второй проект · проверка переключения',status:'RUNNING',phase:'verifying',last_error:null};
const acceptanceMock={status:'IN_PROGRESS',spec_sha256:'f'.repeat(64),report_round:19,summary:{total:4,test_passed:1,failed:1,stale:1,not_tested:1},task_summary:{total:2,test_passed:0,in_progress:1,failed:1},requirements:[
{id:'REQ-001',title:'Выбор ближайшей угрозы',status:'TEST_PASSED',section:'Защита',expected:'Выбрана ближайшая атакующая цель.',source:{start_line:1,end_line:3,text:'Защита персонажа'},scenarios:[]},
{id:'REQ-002',title:'Поиск пути',status:'FAIL',section:'Движение',expected:'Персонаж обходит препятствия.',scenarios:[]},
{id:'REQ-003',title:'Отключение защиты',status:'STALE',section:'Управление',scenarios:[]},
{id:'REQ-004',title:'Управление командами',status:'NOT_TESTED',section:'API',scenarios:[]}],tasks:[]};
let holdSummary=null;
window.fetch=async(path,options)=>{
 requests.push({path,method:options?.method||'GET'});if(options?.method&&options.method!=='GET')throw Error('Fixture forbids mutations');
 let data=[];const jid=path.includes(jobB.id)?jobB.id:jobA.id;
 if(path==='/api/providers')data={catalogue:CATALOGUE,providers:['codex','dslab','litellm']};
 else if(path==='/api/jobs')data=[jobA,jobB];
 else if(path==='/api/jobs/'+jid)data=jid===jobA.id?jobA:jobB;
 else if(path.endsWith('/acceptance-summary')){if(holdSummary)return await holdSummary;data=jid===jobA.id?acceptanceMock:{summary:{total:8,test_passed:0,not_tested:8}}}
 else if(path.endsWith('/acceptance'))data=acceptanceMock;
 else if(path.endsWith('/agent-models'))data={status:jid===jobA.id?'USER_PAUSED':'RUNNING',editable:jid===jobA.id,roles:['planner','coder'],models:{},resolved:{},catalogue:CATALOGUE};
 else if(path.endsWith('/verification'))data={status:'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',checks:[{label:'Gradle',status:'PASS'}]};
 else if(path.endsWith('/activity'))data={job_id:jid,status:jid===jobA.id?'USER_PAUSED':'RUNNING',stage:'paused',title:jid===jobA.id?'На паузе':'Проверяет мод',detail:'Результат подтверждает независимая проверка.',current_round:19,max_rounds:30};
 else if(path.endsWith('/round-task'))data={status:'USER_PAUSED',task:{data:{round_action_round:20,round_action:{kind:'repair_scenario',target:'REQ-002',source:'Диагностика поиска пути. '.repeat(80)},assignment:{current_task:{title:'Движение и препятствия',requirement_ids:['REQ-002']}}}}};
 else if(path.endsWith('/planning'))data={planner:{},manual_reviews:[]};
 else if(path.endsWith('/agent-history'))data={attempts:[],truncated:false};
 else if(path.endsWith('/usage'))data={available:true,active_provider:'litellm',totals:{input_tokens:3000,output_tokens:1000,requests:2},roles:[{role:'planner',input_tokens:1000,output_tokens:500,requests:1},{role:'coder',input_tokens:2000,output_tokens:500,requests:1}],requests:[]};
 else if(path.includes('/media?'))data={files:[{path:'round-19/demo.mp4',url:'/api/jobs/'+jid+'/files/round-19/demo.mp4',size:4000,type:'.mp4'},{path:'round-19/mod.jar',url:'/api/jobs/'+jid+'/files/round-19/mod.jar',size:2000,type:'.jar'}],total:2,rounds:[19],has_more:false};
 else if(path.endsWith('/memory'))data={rounds:[],current:{},errors:[]};
 return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
};
</script>'''.replace('CATALOGUE',json.dumps(model_catalogue()))

checks=r'''<output id="dashboard-test"></output><script>
setTimeout(async()=>{
const passed=[],wait=()=>new Promise(r=>setTimeout(r,50)),check=(ok,name)=>{if(!ok)throw Error(name);passed.push(name)};
try{
 await refresh();await wait();
 check(errors.length===0,'scripts-no-runtime-errors');
 check(document.querySelectorAll('[id]').length===new Set([...document.querySelectorAll('[id]')].map(n=>n.id)).size,'unique-element-ids');
 check(q('readinessTitle').textContent.includes('1 из 4'),'readiness-from-verifier-only');
 check(q('projectBadge').textContent==='На паузе'&&!q('jobAlert').hidden,'status-and-error-visible');
 const project=document.querySelector('[data-job="'+jobA.id+'"]');project.focus();await refresh();
 check(project===document.querySelector('[data-job="'+jobA.id+'"]')&&document.activeElement===project,'poll-preserves-project-button-and-focus');
 check(project.getAttribute('aria-pressed')==='true'&&getComputedStyle(project).backgroundColor!=='rgba(0, 0, 0, 0)','selected-project-highlight');
 check(!q('jobs').textContent.includes('Загрузка'),'loading-placeholder-removed');
 for(const name of ['overview','acceptance','media','agents','usage','settings']){
  document.querySelector('[data-view="'+name+'"]').click();await wait();
  check([...document.querySelectorAll('.lab-view')].filter(n=>!n.hidden).map(n=>n.dataset.page).join(',')===name,'single-visible-page-'+name);
  check(location.hash==='#'+name&&document.querySelector('[data-view="'+name+'"]').getAttribute('aria-current')==='page','navigation-state-'+name);
 }
 check(q('agentModelsPanel').closest('#view-settings')&&q('plannerPanel').closest('#view-agents')&&q('usagePanel').closest('#view-usage'),'panels-grouped-by-purpose');
 await new Promise(resolve=>{addEventListener('popstate',()=>setTimeout(resolve,50),{once:true});history.back()});
 check(!q('view-usage').hidden,'browser-back-restores-previous-section');
 await new Promise(resolve=>{addEventListener('popstate',()=>setTimeout(resolve,50),{once:true});history.forward()});
 check(!q('view-settings').hidden,'browser-forward-restores-section');
 q('baseModelSettings').open=true;q('updateModel').value='draft-model';await refresh();check(q('updateModel').value==='draft-model','poll-preserves-settings-draft');
 q('configure-planner').click();await wait();check(q('modelDialog-planner').open,'role-dialog-opens');
 q('modelDialog-planner').querySelector('header button').click();await wait();check(document.activeElement===q('configure-planner'),'role-dialog-restores-opener-focus:'+document.activeElement.outerHTML.slice(0,180));
 q('newProjectOpen').click();check(q('newProjectDialog').open,'new-project-separate-dialog');q('newProjectClose').click();await wait();check(document.activeElement===q('newProjectOpen'),'create-dialog-restores-focus');
 document.querySelector('[data-view="media"]').click();const video=q('files').querySelector('video');
 check(!!video,'media-player-mounted');await refresh();check(q('files').querySelector('video')===video,'poll-keeps-media-player');
 let paused=0;video.pause=()=>paused++;document.querySelector('[data-view="overview"]').click();check(paused>0,'navigation-pauses-hidden-media');
 const diagnostic=q('roundTaskPanel').querySelector('details');check(diagnostic&&!diagnostic.open,'long-action-diagnostics-collapsed');diagnostic.open=true;await window.memorySwitchJob(active);await wait();check(diagnostic.open,'expanded-diagnostics-preserved');
 document.querySelector('[data-job="'+jobB.id+'"]').click();await wait();await refresh();await wait();
 check(q('title').textContent===jobB.title&&q('projectBadge').textContent==='В работе','project-switch-correct-header');
 check(q('saveSettings').disabled&&q('configure-planner').disabled,'running-project-disables-model-edits');
 check(new URL(location.href).searchParams.get('project')===jobB.id,'project-retained-in-url');
 jobB.status='FINISHED';await refresh();check(q('projectBadge').textContent==='Лимит раундов достигнут','round-limit-never-means-mod-ready');
 document.querySelector('[data-job="'+jobA.id+'"]').click();await wait();await refresh();await wait();
 let release;holdSummary=new Promise(resolve=>release=resolve);
 document.querySelector('[data-job="'+jobB.id+'"]').click();await wait();holdSummary=null;
 document.querySelector('[data-job="'+jobA.id+'"]').click();await wait();await refresh();await wait();
 release(new Response(JSON.stringify({summary:{total:999,test_passed:999}})));await wait();
 check(q('readinessTitle').textContent.includes('1 из 4'),'late-summary-cannot-replace-current-project');
 for(const name of ['overview','acceptance','media','agents','usage','settings']){document.querySelector('[data-view="'+name+'"]').click();await wait();check(document.documentElement.scrollWidth<=window.innerWidth,'no-horizontal-overflow-'+name)}
 if(matchMedia('(prefers-reduced-motion: reduce)').matches)check(getComputedStyle(q('view-settings')).animationName==='none','reduced-motion-respected');
 check(errors.length===0,'all-pages-no-runtime-errors');check(requests.every(r=>r.method==='GET'),'navigation-never-mutates-jobs');
 const target=new URL(location.href).searchParams.get('page')||'overview';document.querySelector('[data-view="'+target+'"]').click();await wait();
 q('dashboard-test').textContent=JSON.stringify({status:'PASS',checks:passed,width:innerWidth});
}catch(error){q('dashboard-test').textContent=JSON.stringify({status:'FAIL',error:String(error),checks:passed,errors})}
if(parent!==window)parent.postMessage({dashboard:q('dashboard-test').textContent},location.origin);
},600);
</script>'''
page=(root/'app/index.html').read_text(encoding='utf-8').replace('<script>',mock+'<script>',1).replace('</body>',checks+'</body>')
(folder/'fixture.html').write_text(page,encoding='utf-8')

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        name=self.path.split('?')[0].lstrip('/')
        if name=='viewport.html':
            data=b'''<!doctype html><style>body{margin:0;background:#0c111b}iframe{display:block;width:390px;height:1100px;border:0;margin:0}output{display:none}</style><iframe src="/fixture.html?page=overview"></iframe><output id="dashboard-test"></output><script>addEventListener('message',e=>{if(e.origin===location.origin&&e.data.dashboard)document.getElementById('dashboard-test').textContent=e.data.dashboard})</script>'''
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(data);return
        path=folder/'fixture.html' if name=='fixture.html' else root/'app'/name
        if name not in ['fixture.html','provider.js','memory.js','planner.js','agent-models.js','dashboard.js','planner.css','memory.css','dashboard.css']:
            self.send_error(404);return
        data=path.read_bytes();self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'application/javascript')
        self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
reports=[]
run_id=uuid.uuid4().hex[:10]
try:
    for width,target in [(1440,'overview'),(1440,'settings'),(1440,'acceptance'),(768,'overview'),(390,'overview')]:
        key=f'{width}-{target}'
        route='viewport.html' if width==390 else f'fixture.html?page={target}'
        result=subprocess.run(['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-extensions','--force-prefers-reduced-motion','--user-data-dir='+str(folder/('edge-'+key+'-'+run_id)),'--virtual-time-budget=7000',f'--window-size={width},1100','--hide-scrollbars','--screenshot='+str(folder/(key+'.png')),'--dump-dom',f'http://127.0.0.1:{server.server_port}/{route}'],capture_output=True,timeout=50)
        dom=result.stdout.decode('utf-8',errors='replace');(folder/(key+'.html')).write_text(dom,encoding='utf-8')
        match=re.search(r'<output id="dashboard-test">(.*?)</output>',dom,re.S)
        report=json.loads(html.unescape(match[1])) if match else {'status':'FAIL','error':'No browser result'}
        if not match:report.update(returncode=result.returncode,stderr=result.stderr.decode('utf-8',errors='replace')[-2000:])
        report['case']=key;reports.append(report);print(json.dumps(report))
finally:server.shutdown();server.server_close()
(folder/'results.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
assert all(r['status']=='PASS' for r in reports)
