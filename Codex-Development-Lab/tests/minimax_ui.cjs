/* Run with Node; read the two actual UI sources from stdin as JSON. */
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
class Element {
 constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.style={};this._value='';this.hidden=false}
 append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node)}}
 prepend(node){this.children.unshift(node);node.parent=this}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);this._value=''}
 get options(){return this.children}
 get value(){return this.tag==='select'?(this._value||this.children[0]?.value||''):this._value}
 set value(value){this._value=String(value)}
 get selectedOptions(){return this.children.filter(n=>n.value===this.value)}
 closest(){return this.parent}
 querySelector(){return null}
 setAttribute(){}
 after(){}
 before(){}
 close(){this.open=false}
 showModal(){this.open=true}
 focus(){}
}
const sources=process.argv[2]?{provider:fs.readFileSync(process.argv[2]+'/app/provider.js','utf8'),roles:fs.readFileSync(process.argv[2]+'/app/agent-models.js','utf8')}:JSON.parse(fs.readFileSync(0,'utf8'));
const elements={};
for(const id of ['model','reasoning','updateModel','updateReasoning']){
 const node=elements[id]=new Element(id.includes('easoning')?'select':'input');
 const label=new Element('label');label.append(node);
 if(node.tag==='select')for(const value of ['default','none','minimal','low','medium','high','xhigh']){const option=new Element('option');option.value=value;node.append(option)}
}
const context={console,el:(tag,cls,text)=>new Element(tag,text),q:id=>elements[id],window:{},document:{createElement:tag=>new Element(tag),body:new Element('body'),getElementById:id=>elements[id]||null},setInterval(){},active:null};
vm.createContext(context);
vm.runInContext(sources.provider.slice(0,sources.provider.indexOf('const newProviderFields='))+`;globalThis.fields=providerFields(new Element('form'),'new','model','reasoning');`,Object.assign(context,{Element}));
const fields=context.fields;
function walk(node){return [node,...node.children.flatMap(walk)]}
const budgetInputs=walk(fields.form).filter(node=>['dslab_token_budget','dslab_max_output_tokens','dslab_max_calls'].includes(node.name));
assert.equal(budgetInputs.length,3);
for(const input of budgetInputs)assert.equal(input.max,undefined,'API budget has no arbitrary HTML maximum');
assert.deepEqual(fields.provider.options.map(x=>x.value),['codex','dslab','litellm','minimax']);
fields.provider.value='minimax';fields.provider.onchange();
assert.equal(fields.model.value,'MiniMax-M3.1-Flash');assert.equal(fields.model.readOnly,true);
assert.deepEqual(fields.effort.options.filter(x=>!x.disabled).map(x=>x.value),['default','low','medium','high','xhigh','max']);
fields.effort.value='high';fields.update(false);assert.equal(fields.effort.value,'high');
fields.provider.value='litellm';fields.provider.onchange();assert.equal(fields.model.value,'deepseek-ai/DeepSeek-V4-Flash-0731');
assert.deepEqual(fields.effort.options.filter(x=>!x.disabled).map(x=>x.value),['default']);assert.equal(fields.effort.value,'default');
fields.provider.value='codex';fields.provider.onchange();assert.equal(fields.model.value,'');assert.equal(fields.model.readOnly,false);
fields.provider.value='dslab';fields.provider.onchange();assert.equal(fields.model.value,'claude-sonnet-5-5');
// Expose closure state in the test copy only; production code has no test API.
vm.runInContext(sources.roles.replace('window.agentModelsSwitchJob=switchJob;','window.agentModelsSwitchJob=switchJob; window.testRoleControls=controls; window.testCapabilities=capabilities; window.testPopulate=populateModels; window.testSetState=(value,jid)=>{state=value;job=jid;update()}; window.testFill=fill; window.testSaveRole=saveRole;'),context);
for(const role of ['planner','coder','reviewer']){
 const c=context.window.testRoleControls[role];assert.ok(c.provider.options.some(x=>x.value==='minimax'));
 c.provider.value='minimax';context.window.testPopulate(role,'',false);
 assert.equal(c.model.value,'MiniMax-M3.1-Flash');
 assert.deepEqual(c.effort.options.filter(x=>!x.disabled).map(x=>x.value),['default','low','medium','high','xhigh','max']);
 assert.equal(c.providerNote.hidden,false);
 c.provider.value='litellm';context.window.testPopulate(role,'',false);assert.equal(c.providerNote.hidden,true);
 assert.deepEqual(c.effort.options.filter(x=>!x.disabled).map(x=>x.value),['default']);
}
// An inherited reviewer is visible, and resetting an override deletes its key.
// Sending reviewer:{} would accidentally choose Codex defaults on the server.
(async()=>{
 const coder={provider:'minimax',model:'MiniMax-M3.1-Flash',reasoning_effort:'low'};
 context.active='job-1';
 const inherited={status:'USER_PAUSED',editable:true,roles:['planner','coder','reviewer'],models:{coder},resolved:{reviewer:{...coder,inherited:true,inherited_from:'coder'}}};
 context.window.testSetState(inherited,'job-1');
 const reviewer=context.window.testRoleControls.reviewer;
 assert.match(reviewer.button.textContent,/MiniMax-M3.1-Flash.*из настроек кодера/);
 assert.equal(reviewer.provider.options[0].textContent,'Наследовать модель кодера');
 const explicit={...inherited,models:{coder,reviewer:{provider:'codex',model:'gpt-6-sol',reasoning_effort:'medium'}}};
 context.window.testSetState(explicit,'job-1');context.window.testFill('reviewer');reviewer.provider.value='';
 let sent=null;
 context.fetch=async(path,options)=>{sent=JSON.parse(options.body);return {ok:true,json:async()=>inherited}};
 await context.window.testSaveRole('reviewer');
 assert.ok(sent);assert.equal(Object.hasOwn(sent.models,'reviewer'),false);
 assert.deepEqual(sent.models.coder,coder);
 console.log('MiniMax provider/reviewer UI: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
