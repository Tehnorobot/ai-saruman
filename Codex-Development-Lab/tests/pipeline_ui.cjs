const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.value='';this.open=false;this.scrollTop=0;this.text='';this.listeners=new Map();this.attrs={};this.disabled=false;this.hidden=false;}
 set textContent(value){this.text=String(value);this.children=[];}
 get textContent(){return [this.text,...this.children.map(x=>x.textContent)].join(' ');}
 set innerHTML(value){throw Error('Pipeline data must never become HTML');}
 append(...items){this.children.push(...items);for(const item of items)item.parent=this;}
 replaceChildren(...items){this.children=[];this.append(...items);}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
 setAttribute(key,value){this.attrs[key]=value;}
 addEventListener(key,handler){this.listeners.set(key,handler);}
 focus(){document.activeElement=this;}
 showModal(){this.open=true;}
 close(){this.open=false;this.listeners.get('close')?.();}
}
const body=new Element('body'),open=new Element('button');open.id='pipelineOpen';body.append(open);
const all=node=>[node,...node.children.flatMap(all)];
const document={body,activeElement:open,createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment'),getElementById:id=>all(body).find(x=>x.id===id)||null};
const get=id=>document.getElementById(id),tab=name=>all(body).find(x=>x.dataset.pipelineTab===name);
const requests=[],intervals=[],events=new Map();
const JOB='a'.repeat(14),OTHER='b'.repeat(14),OLD='c'.repeat(14);
let deferred=null;
let draftView=false,softHandoffView=false,verificationView=false,verificationRevision=0,quotaView=false,memorySyncView=false,resultView=false;
const overview=job=>({enabled:job!==OLD,version:'v1',scope:{round:3},status:{stage:'review',active_task_ids:['T1'],ready:false,readiness_reasons:['C1 is not verified'],coverage:[{condition_id:'C1',status:'failed'}]},catalog:{conditions:[{id:'C1',expected:'A sapling is placed'}]},graph:{tasks:[{id:'T1',title:'Plant <script>unsafe()</script>',outcome:'Sapling in world',requirement_ids:['R1'],condition_ids:['C1'],depends_on:[]},{id:'T2',title:'Grow',outcome:'Tree in world',requirement_ids:['R2'],condition_ids:[],depends_on:['T1']}]},decision:{scope:{round:3},strategy:'repair',rationale:'New useful experiment',actions:[{id:'A1',kind:'repair',task_ids:['T1'],condition_ids:['C1'],goal:'Make planting happen',expected_observation:'sapling=true',depends_on_action_ids:[]}]},review:{disposition:'changes_requested',findings:[{id:'F1',kind:'requirement_violation',severity:'high',requirement_ids:['R1'],observation:'No planting call',consequence:'C1 fails',suggested_check:'Observe the world block',code_refs:[{artifact:{path:'src/Plant.java'},start_line:5,end_line:6,excerpt:'<img src=x onerror=alert(1)>'}]}],unknowns:['Runtime not checked']},reflection:{rationale:'Change experiment',hypotheses:[{id:'H1',statement:'Action missing',experiment:'Record action event',prediction:'No action event'}]},memory_summary:{record_count:2}});
const fetch=async url=>{
 requests.push(url);
 if(deferred&&url===`/api/jobs/${deferred.job}/pipeline`){const pending=deferred;deferred=null;return new Promise(resolve=>pending.resolve=value=>resolve({ok:true,json:async()=>value}));}
 let data;
 if(url.includes('/memory/'))data={record:{record_id:'M1'},payload:{summary:'Full <img src=x onerror=alert(1)>',plan:'Unabridged '.repeat(2000)}};
 else if(url.includes('/memory'))data=url.includes('cursor=')?{records:[{record_id:'M0',summary:'Earlier round',full_record_ref:{path:'irrelevant'}}],next_cursor:null,omitted_records:0}:{records:[{record_id:'M1',summary:'Current round',full_record_ref:{path:'irrelevant'}}],next_cursor:'page-two',omitted_records:1};
 else {data=overview(url.includes(OTHER)?OTHER:url.includes(OLD)?OLD:JOB);
  data.agent_reports={planner:{phase:'plan',notes_md:'## Full Markdown\n\n<script>report_is_data()</script>\n\n'+'Keep every detail. '.repeat(2000)}};
  if(quotaView)data.operation={role:'coder',contract:'AgentReport',attempt:5,status:'retrying',retry_category:'rate_limit',retry_delay_seconds:900};
  if(memorySyncView){data.job_status='RUNNING';data.service_operation={active:true,stage:'memory_sync',completed:13,total:20};}
  if(resultView)data.operation={role:'coder',contract:'AgentReport',attempt:5,status:'processing_result',executor_status:'FAIL'};
  if(verificationView){data.status.coverage=[{condition_id:'C1',status:'untested'}];
   data.operation={role:'coder',contract:'AgentReport',attempt:++verificationRevision,status:'running'};
   data.verification_display={conditions:{C1:{state:'needs_retest'}},tasks:{T1:{state:'needs_retest',has_history:true,
    last_common_success:{round:73},latest:{round:74,outcome:'incomplete',reason:'execution_incomplete'}}}};}
  if(draftView){data.graph=null;data.decision=null;data.catalog.requirements=[{id:'R1',statement:'Saved semantic requirement'}];
   data.decomposition={semantic_review:'pending'};data.operation={role:'reviewer',contract:'ReviewResult',attempt:2,status:'running'};}}
 if(softHandoffView){data.preparation={elapsed_seconds:3700,soft_limit_seconds:3600,handoff_due:true};
  data.handoff={rationale:'Proceed with ready work',working_decisions:['Use the actual API'],tasks:[{task_id:'T1',status:'experiment',reason:'Measure actual behavior',experiment:'Record world changes',return_when:'After observation'}]};
  data.decomposition={semantic_review:'deferred_to_work'};}
 return {ok:true,json:async()=>data};
};
const window={addEventListener:(name,callback)=>events.set(name,callback)};
const context={document,window,fetch,active:JOB,setInterval:fn=>intervals.push(fn),console,Date,JSON,Map,Set,encodeURIComponent};
vm.runInNewContext(fs.readFileSync(process.argv[2]||'app/pipeline.js','utf8'),context);
const flush=async()=>{for(let i=0;i<7;i++)await new Promise(setImmediate);};

(async()=>{
 assert.equal(requests.length,0,'A closed window must not fetch pipeline or memory');
 assert.equal(open.disabled,false);
 await window.LabPipeline.open(JOB);await flush();
 assert.equal(get('pipelineDialog').open,true);
 assert.ok(get('pipelineStage').textContent.includes('код и сценарии'));
 assert.ok(get('pipelineContent').textContent.includes('Make planting happen'));
 assert.ok(get('pipelineContent').textContent.includes('<script>report_is_data()</script>'));
 assert.ok(get('pipelineContent').textContent.includes('## Full Markdown\n\n'));
 assert.ok(get('pipelineContent').textContent.length>30000,'Complete agent Markdown is retained in the UI');
 assert.ok(!requests.some(url=>url.includes('/memory')),'No eager archive fetch');
 tab('tasks').onclick();
 assert.ok(get('pipelineContent').textContent.includes('Plant <script>unsafe()</script>'));
 assert.ok(get('pipelineContent').textContent.includes('Зависит от: T1'));
 assert.ok(get('pipelineContent').textContent.includes('Не пройдено'));
 verificationView=true;await window.LabPipeline.refresh();await flush();
 assert.ok(get('pipelineContent').textContent.includes('Нужна перепроверка'));
 let history=all(get('pipelineContent')).find(x=>x.tag==='details'&&x.className==='pipeline-verification-history');
 assert.ok(history);assert.equal(history.open,false,'Verification history must be collapsed by default');
 assert.ok(history.textContent.includes('Все условия проходили: раунд 73'));
 assert.ok(history.textContent.includes('раунд 74 — проверка не завершена'));
 history.open=true;history.listeners.get('toggle')();
 await window.LabPipeline.refresh();await flush();
 history=all(get('pipelineContent')).find(x=>x.tag==='details'&&x.className==='pipeline-verification-history');
 assert.equal(history.open,true,'Polling must preserve the opened history');
 verificationView=false;await window.LabPipeline.refresh();await flush();
 quotaView=true;await window.LabPipeline.refresh();await flush();
 assert.ok(get('pipelineStage').textContent.includes('ожидаю доступную квоту провайдера'));
 assert.ok(get('pipelineStage').textContent.includes('повторные попытки включены'));
 assert.ok(get('pipelineStage').textContent.includes('900 с'));
 quotaView=false;await window.LabPipeline.refresh();await flush();
 memorySyncView=true;await window.LabPipeline.refresh();await flush();
 assert.ok(get('pipelineStage').textContent.includes('Синхронизирую память · 13 / 20'));
 memorySyncView=false;resultView=true;await window.LabPipeline.refresh();await flush();
 assert.ok(get('pipelineStage').textContent.includes('запрос завершён, сохраняю результат'));
 resultView=false;await window.LabPipeline.refresh();await flush();
 tab('review').onclick();
 assert.ok(get('pipelineContent').textContent.includes('<img src=x onerror=alert(1)>'));
 assert.ok(get('pipelineContent').textContent.includes('Гипотеза')||get('pipelineContent').textContent.includes('гипотеза'));
 tab('memory').onclick();await flush();
 assert.equal(requests.filter(url=>url.endsWith('/memory')).length,1);
 assert.equal(get('pipelineMemoryNext').disabled,false);
 const full=all(body).find(x=>x.tag==='button'&&x.textContent==='Открыть полный JSON');
 await full.onclick();await flush();
 assert.ok(get('pipelineMemoryRecords').textContent.includes('Full <img src=x onerror=alert(1)>'));
 assert.ok(get('pipelineMemoryRecords').textContent.length>20000,'Full original is not cut by UI');
 assert.equal(requests.filter(url=>url.includes('/memory/M1')).length,1);
 get('pipelineMemoryNext').onclick();await flush();
 assert.ok(requests.some(url=>url.includes('cursor=page-two')));
 assert.ok(get('pipelineMemoryRecords').textContent.includes('Earlier round'));
 assert.equal(get('pipelineMemoryNext').disabled,true);
 get('pipelineMemoryPrev').onclick();await flush();
 assert.ok(get('pipelineMemoryRecords').textContent.includes('Current round'));
 get('pipelineMemoryTask').value='T1';get('pipelineMemorySearch').value='a & b';get('pipelineMemoryFind').onclick();await flush();
 assert.ok(requests.some(url=>url.includes('task_id=T1')&&url.includes('query=a%20%26%20b')));
 const count=requests.length;intervals[1]();await flush();assert.equal(requests.length,count,'Polling must not replace an opened memory record');
 get('pipelineDialog').close();
 const closedCount=requests.length;intervals[1]();await flush();assert.equal(requests.length,closedCount);

 tab('overview').onclick();
 const pending={job:JOB};deferred=pending;
 const first=window.LabPipeline.open(JOB);await flush();
 context.active=OTHER;await window.LabPipeline.open(OTHER);await flush();
 pending.resolve({...overview(JOB),decision:{rationale:'STALE PROJECT'}});await first;await flush();
 assert.ok(get('pipelineIdentity').textContent.includes(OTHER));
 assert.ok(!get('pipelineContent').textContent.includes('STALE PROJECT'),'Old project response must not overwrite selection');
 draftView=true;await window.LabPipeline.refresh();await flush();
 tab('tasks').onclick();
 assert.ok(get('pipelineContent').textContent.includes('Saved semantic requirement'));
 assert.ok(get('pipelineContent').textContent.includes('ожидает ревью'));
 assert.ok(get('pipelineStage').textContent.includes('reviewer / ReviewResult'));
 assert.ok(get('pipelineStage').textContent.includes('ещё не одобрен'));
 draftView=false;
 softHandoffView=true;tab('overview').onclick();await window.LabPipeline.refresh();await flush();
 assert.ok(get('pipelineContent').textContent.includes('61 мин активной подготовки'));
 assert.ok(get('pipelineContent').textContent.includes('переход к реализации доступных задач'));
 assert.ok(get('pipelineContent').textContent.includes('Measure actual behavior'));
 assert.ok(get('pipelineStage').textContent.includes('итоговое ревью впереди'));
 softHandoffView=false;
 context.active=OLD;await window.LabPipeline.open(OLD);await flush();
 assert.ok(get('pipelineStage').textContent.includes('не активирован'));
 assert.equal(get('pipelineContent').hidden,false);
 assert.equal(get('pipelineMemory').hidden,true);
 console.log('Pipeline UI: PASS (lazy paging, full JSON, XSS, stale responses, inactive jobs)');
})().catch(error=>{console.error(error);process.exitCode=1;});
