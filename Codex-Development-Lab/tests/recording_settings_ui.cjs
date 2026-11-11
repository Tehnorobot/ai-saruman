const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(process.argv[2]||'app/recording-settings.js','utf8');
class Element {
 constructor(){this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.listeners={};this.attrs={}}
 addEventListener(type,callback){this.listeners[type]=callback}
 setAttribute(key,value){this.attrs[key]=value}
 removeAttribute(key){delete this.attrs[key]}
 focus(){this.focused=true}
 emit(type,event={}){return this.listeners[type]?.(event)}
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function start(respond){
 const ids=['recordingSettingsForm','recordingMaxSeconds','recordingSave','recordingRetry','recordingSettingsStatus','recordingCurrentLimit'];
 const nodes=Object.fromEntries(ids.map(id=>[id,new Element()])),listeners={},requests=[];
 const state={respond};
 const context={document:{getElementById:id=>nodes[id]},window:{addEventListener:(type,callback)=>listeners[type]=callback},
  fetch:async(path,options={})=>{requests.push({path,...options});return state.respond(path,options)}};
 vm.runInNewContext(source,context);
 return {nodes,listeners,requests,state,input(text){nodes.recordingMaxSeconds.value=String(text);nodes.recordingMaxSeconds.emit('input')},
  submit(){return nodes.recordingSettingsForm.emit('submit',{preventDefault(){}})}};
}
const ok=seconds=>({ok:true,status:200,json:async()=>({max_seconds:seconds,scope:'global',applies_to:'next_test_run'})});
async function run(){
 // Global load does not require an active project or interfere with its controls.
 const app=start(()=>ok(300));
 assert.equal(app.nodes.recordingMaxSeconds.disabled,true);
 await settle();
 assert.equal(app.nodes.recordingMaxSeconds.value,'300');
 assert.equal(app.nodes.recordingSave.disabled,true);
 assert.match(app.nodes.recordingCurrentLimit.textContent,/300 с/);
 app.input(7200);
 assert.equal(app.nodes.recordingSave.disabled,false);
 assert.match(app.nodes.recordingSettingsStatus.textContent,/Изменено/);
 app.state.respond=(_,options)=>ok(JSON.parse(options.body).max_seconds);
 await app.submit();
 const put=app.requests.find(request=>request.method==='PUT');
 assert.equal(put.path,'/api/recording-settings');
 assert.deepEqual(JSON.parse(put.body),{max_seconds:7200});
 assert.match(app.nodes.recordingSettingsStatus.textContent,/следующих запусков проверки/);
 assert.equal(app.nodes.recordingSave.disabled,true);
 // An open-page refresh or another project's polling cannot wipe dirty edits.
 app.input(9000);
 const before=app.requests.length;
 await app.listeners['lab-recording-settings-open']();
 assert.equal(app.requests.length,before);
 assert.equal(app.nodes.recordingMaxSeconds.value,'9000');
 assert.equal(app.listeners['lab-job'],undefined);
 for(const invalid of ['','0','-1','1.5','bad','Infinity','9007199254740992']){
  app.input(invalid);assert.equal(app.nodes.recordingSave.disabled,true);
  await app.submit();assert.equal(app.requests.length,before);
  assert.equal(app.nodes.recordingMaxSeconds.attrs['aria-invalid'],'true');
 }
 // Saving errors preserve the edit and server-confirmed displayed value.
 app.input(10800);
 app.state.respond=()=>({ok:false,status:503,json:async()=>({detail:'Storage unavailable'})});
 await app.submit();
 assert.equal(app.nodes.recordingMaxSeconds.value,'10800');
 assert.equal(app.nodes.recordingSave.disabled,false);
 assert.match(app.nodes.recordingSettingsStatus.textContent,/Storage unavailable/);
 assert.match(app.nodes.recordingCurrentLimit.textContent,/7.?200 с/);
 // Busy submission has one request, including a repeated Enter/click.
 let resolveSave;
 app.state.respond=()=>new Promise(resolve=>{resolveSave=resolve});
 const saving=app.submit();
 assert.equal(app.nodes.recordingMaxSeconds.disabled,true);
 const count=app.requests.length;await app.submit();assert.equal(app.requests.length,count);
 resolveSave(ok(10800));await saving;
 assert.equal(app.nodes.recordingMaxSeconds.disabled,false);
 // Recover after an initial load failure without presenting a fake saved default.
 const failed=start(()=>({ok:false,status:503,json:async()=>({detail:'Unavailable'})}));
 await settle();
 assert.equal(failed.nodes.recordingMaxSeconds.disabled,true);
 assert.equal(failed.nodes.recordingRetry.hidden,false);
 assert.equal(failed.nodes.recordingCurrentLimit.textContent,'');
 failed.state.respond=()=>ok(86400);
 await failed.nodes.recordingRetry.emit('click');
 assert.equal(failed.nodes.recordingMaxSeconds.value,'86400');
 assert.equal(failed.nodes.recordingMaxSeconds.disabled,false);
 assert.equal(failed.nodes.recordingRetry.hidden,true);
 // Malformed successful responses do not claim the edit was saved.
 failed.input(90000);
 failed.state.respond=()=>({ok:true,status:200,json:async()=>({max_seconds:0})});
 await failed.submit();
 assert.equal(failed.nodes.recordingSave.disabled,false);
 assert.equal(failed.nodes.recordingMaxSeconds.value,'90000');
 assert.match(failed.nodes.recordingSettingsStatus.textContent,/неверный предел/);
 console.log('Recording settings UI: PASS (global load/save, long duration, dirty edits, validation, retry, duplicate submit, server errors)');
}
run().catch(error=>{console.error(error);process.exitCode=1});
