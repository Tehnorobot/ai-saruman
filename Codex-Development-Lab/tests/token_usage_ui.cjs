/* Run: node tests/token_usage_ui.cjs. Uses the actual usage UI source. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element{
 constructor(tag,cls='',text=''){this.tag=tag;this.className=cls;this.children=[];this.style={};this.attributes={};this._text=String(text);this.parentNode=null;this.open=false}
 set textContent(value){this._text=String(value);for(const child of this.children)child.parentNode=null;this.children=[]}
 get textContent(){return [this._text,...this.children.map(c=>c.textContent)].join(' ')}
 append(...nodes){for(const node of nodes){node.remove();node.parentNode=this;this.children.push(node)}}
 insertBefore(node,before){node.remove();node.parentNode=this;const index=before?this.children.indexOf(before):-1;if(index<0)this.children.push(node);else this.children.splice(index,0,node)}
 remove(){if(this.parentNode){this.parentNode.children.splice(this.parentNode.children.indexOf(this),1);this.parentNode=null}}
 replaceChildren(...nodes){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...nodes)}
 before(){}setAttribute(key,value){this.attributes[key]=value}focus(){}showModal(){this.open=true}close(){this.open=false}
}
const pending=[],timers=new Map(),intervals=[];let timerId=0;
const context=vm.createContext({
 el:(...args)=>new Element(...args),q:()=>new Element('div'),active:null,
 document:{body:new Element('body')},window:{dispatchEvent(){}},CustomEvent:class{},
 AbortController,Map,Set,Date,Number,JSON,encodeURIComponent,
 setInterval(fn,ms){intervals.push({fn,ms})},
 setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)},
 fetch(url,options){return new Promise((resolve,reject)=>{const item={url,options,resolve:report=>resolve({ok:true,json:async()=>report}),reject};pending.push(item);options.signal.addEventListener('abort',()=>reject(new Error('aborted')))})}
});
const source=fs.readFileSync(path.join(__dirname,'../app/provider.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('const usagePanel=')),context);
const run=code=>vm.runInContext(code,context);
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
const request=(id,extra={})=>({request_id:String(id),task_id:'same-task',provider:['codex','dslab','litellm','minimax'][id%4],role:'coder',model:'model',status:'SUCCEEDED',started_at:1793000000+id,input_tokens_total:100,output_tokens:20,cache_read_input_tokens:80,cache_creation_input_tokens:0,...extra});
const next=fragment=>{const index=pending.findIndex(item=>item.url.includes(fragment));assert.notEqual(index,-1,fragment);return pending.splice(index,1)[0]};
async function checkUsageRendering(){
 run("globalThis.orderState={rows:new Map(),nodes:new Map(),cursor:null,loadedOlder:false,moreBusy:false}");
 run("mergeRequests(orderState,{available:true,requests:[{request_id:'s3',sequence:3,provider:'minimax',started_at:1},{request_id:'s1',sequence:1,provider:'minimax',started_at:9999999999}],next_cursor:'gap'},false)");
 run("mergeRequests(orderState,{available:true,requests:[{request_id:'s2',sequence:2,provider:'minimax',started_at:0}],next_cursor:null},true)");
 assert.equal(run("[...orderState.rows.keys()].join(',')"),'s3,s2,s1');
 assert.equal(intervals[0].ms,2000);
 run("active='job';refreshUsage()");
 const initial=next('/requests?limit=50'),summary=next('/usage');
 initial.resolve({available:true,requests:Array.from({length:50},(_,i)=>request(150-i)),next_cursor:'older/a+b'});await flush();
 assert.equal(run('requestBody.children.length'),50);
 assert.equal(run('loadMoreRequests.hidden'),false);
 // The history stays live even while /usage is waiting.
 run('refreshUsage()');next('/requests?limit=50').resolve({available:true,requests:[request(151),request(150)],next_cursor:'new-first-page'});await flush();
 assert.equal(run('requestBody.children.length'),51);
 run('loadMoreRequests.onclick()');
 next('/requests?limit=50').resolve({available:true,requests:[request(151),request(150)],next_cursor:'new-first-page'});
 const older=next('&cursor=new-first-page');
 older.resolve({available:true,requests:Array.from({length:50},(_,i)=>request(100-i)),next_cursor:'last'});await flush();
 assert.equal(run('requestBody.children.length'),101);
 run('loadMoreRequests.onclick()');
 next('/requests?limit=50').resolve({available:true,requests:[request(151)],next_cursor:'must-not-reset'});
 next('&cursor=last').resolve({available:true,requests:Array.from({length:50},(_,i)=>request(50-i)),next_cursor:null});await flush();
 assert.equal(run('requestBody.children.length'),151);
 assert.equal(run('loadMoreRequests.hidden'),true);
 assert.equal(run('usageState.cursor'),null);
 // Same task, different calls and provider identities stay distinct.
 assert.match(run('requestBody.textContent'),/codex/);assert.match(run('requestBody.textContent'),/dslab/);assert.match(run('requestBody.textContent'),/litellm/);assert.match(run('requestBody.textContent'),/minimax/);
 summary.resolve({available:true,totals:{input_tokens_total:100,output_tokens:20,cache_read_input_tokens:80}});await flush();
 assert.match(run('usageSummary.textContent'),/100/);
 const diagnostic={error_type:'overloaded',message:'<script>untrusted</script>'};
 run('refreshUsage()');next('/requests?limit=50').resolve({available:true,requests:[request(151,{diagnostic_json:diagnostic,status:'FAILED'})],next_cursor:null});next('/usage').resolve({available:false});await flush();
 run("globalThis.selectedRow=usageState.nodes.get('151').row;globalThis.selectedDetails=usageState.nodes.get('151').details;selectedDetails.open=true");
 run('refreshUsage()');next('/requests?limit=50').resolve({available:true,requests:[request(151,{diagnostic_json:diagnostic,status:'FAILED'})],next_cursor:null});next('/usage').resolve({available:false});await flush();
 assert.equal(run("usageState.nodes.get('151').row===selectedRow"),true);
 assert.equal(run("usageState.nodes.get('151').details===selectedDetails&&selectedDetails.open"),true);
 assert.match(run('selectedDetails.textContent'),/<script>untrusted<\/script>/);
 assert.equal(run('usageDialog.children.length'),2);
 assert.equal(run("usageDetails.children.filter(child=>child.tag==='table').length"),1);
 assert.equal(run("requestHeading.children.map(c=>c.textContent).join('|')").includes('ID запроса'),true);
 assert.doesNotMatch(run('usageDetails.textContent'),/Codex по дням|Планировщик: вход|Среднее за активный/);
 // Failed refresh retains the whole loaded history.
 run('refreshUsage()');next('/requests?limit=50').reject(new Error('offline'));next('/usage').resolve({available:false});await flush();
 assert.equal(run('requestBody.children.length'),151);
 assert.match(run('requestStatus.textContent'),/Не удалось/);
 // A burst larger than one page restarts the cursor so intermediate calls
 // remain reachable; retained older rows and their DOM nodes stay intact.
 context.burst=Array.from({length:50},(_,i)=>request(250-i));
 run("mergeRequests(usageState,{available:true,requests:burst,next_cursor:'burst-gap'},false)");
 assert.equal(run('usageState.rows.size'),201);assert.equal(run('usageState.cursor'),'burst-gap');assert.equal(run('usageState.loadedOlder'),false);
 context.gap=Array.from({length:50},(_,i)=>request(200-i));
 run("usageState.loadedOlder=true;mergeRequests(usageState,{available:true,requests:gap,next_cursor:'old-overlap'},true)");
 assert.equal(run('usageState.rows.size'),250);
 // An unchanged newest page does not move an established older cursor.
 run("mergeRequests(usageState,{available:true,requests:burst,next_cursor:'burst-gap'},false)");
 assert.equal(run('usageState.cursor'),'old-overlap');
 // Cold indexing completion restarts pagination even with the same head.
 run("mergeRequests(usageState,{available:true,codex_loading:true,requests:burst,next_cursor:'cold'},false)");
 assert.match(run('requestStatus.textContent'),/Индексируется история/);
 run("mergeRequests(usageState,{available:true,codex_loading:false,requests:burst,next_cursor:'indexed'},false)");
 assert.equal(run('usageState.cursor'),'indexed');assert.equal(run('usageState.loadedOlder'),false);
 // A first-page restart wins over an older-page response already in flight.
 context.newest=[request(251)];
 run("usageState.moreBusy=true;usageState.loadedOlder=true;mergeRequests(usageState,{available:true,requests:newest,next_cursor:'restart'},false);mergeRequests(usageState,{available:true,requests:[],next_cursor:'stale-cursor'},true);usageState.moreBusy=false");
 assert.equal(run('usageState.cursor'),'restart');assert.equal(run('usageState.loadedOlder'),false);
 // Reset aborts pending work; late results cannot paint the previous project.
 run('refreshUsage()');const stale=next('/requests?limit=50'),staleSummary=next('/usage');
 run("active='other';refreshUsage()");assert.equal(stale.options.signal.aborted,true);assert.equal(staleSummary.options.signal.aborted,true);
 assert.equal(run('requestBody.children.length'),0);assert.equal(run('usageState.cursor'),null);
 stale.resolve({available:true,requests:[request('stale')],next_cursor:'bad'});await flush();assert.equal(run('requestBody.children.length'),0);
 // Eight second timeout releases busy flags, allowing the next poll to retry.
 assert.ok([...timers.values()].every(timer=>timer.ms===8000));
 for(const timer of [...timers.values()])timer.fn();await flush();
 assert.equal(run('usageState.busy'),false);assert.equal(run('usageState.summaryBusy'),false);
 pending.length=0;run('refreshUsage()');
 next('/requests?limit=50').resolve({available:true,requests:[],next_cursor:'a/b+c'});next('/usage').resolve({available:false});await flush();
 run('loadMoreRequests.onclick()');next('/requests?limit=50').resolve({available:true,requests:[],next_cursor:'ignored'});
 next('&cursor=a%2Fb%2Bc').reject(new Error('offline'));next('/usage').resolve({available:false});await flush();
 assert.equal(run('usageState.cursor'),'a/b+c');assert.equal(run('loadMoreRequests.disabled'),false);
}
// Keep Node alive until every asynchronous assertion reaches completion.
// A pending Promise alone does not keep the event loop alive.
const watchdog=setTimeout(()=>{console.error('Token usage UI: timed out before completion');process.exitCode=1},5000);
checkUsageRendering().then(()=>{
 clearTimeout(watchdog);
 console.log('Token usage UI: PASS (all asynchronous checks completed)');
},error=>{
 clearTimeout(watchdog);
 console.error(error);
 process.exitCode=1;
});
