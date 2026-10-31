const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(process.argv[2]||'app/dashboard.js','utf8');
const start=source.indexOf('  const phase=String(job.phase');
assert.ok(start>=0,'Workflow phase rendering must exist');
const code=source.slice(start,source.indexOf('  loadSummary();',start));
function render(job){
 const nodes=['plan','code','test','result'].map(stage=>({dataset:{stage},attrs:{},classes:new Set(),
  setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]},
  classList:{toggle(name,value){if(value)this.owner.classes.add(name);else this.owner.classes.delete(name)}}}));
 for(const node of nodes)node.classList.owner=node;
 vm.runInNewContext(code,{job,get:id=>{assert.equal(id,'workflow');return {querySelectorAll:()=>nodes}}});
 return nodes;
}
for(const [phase,stage] of Object.entries({decomposing:'plan',planning:'plan',reflecting:'plan',pipeline_waiting:'plan',
 codex_development:'code',reviewing:'test',verifying:'test',acceptance_verified:'result',max_rounds_reached:'result'})){
 const nodes=render({phase,status:'RUNNING'});
 assert.equal(nodes.filter(n=>n.classes.has('current')).length,1);
 assert.equal(nodes.find(n=>n.attrs['aria-current']==='step').dataset.stage,stage);
}
for(const [phase,stage] of Object.entries({planner_error:'plan',coder_error:'code',reviewer_error:'test',verification_error:'test'})){
 const nodes=render({phase,status:'USER_PAUSED'});
 const selected=nodes.find(n=>n.classes.has('current'));
 assert.equal(selected.dataset.stage,stage);assert.ok(selected.classes.has('workflow-paused'));
 assert.equal(selected.attrs.title,'Остановлено на этом этапе');
}
assert.equal(render({phase:'unknown',status:'FINISHED'}).find(n=>n.classes.has('current')).dataset.stage,'result');
assert.equal(render({phase:'unknown',status:'QUEUED'}).filter(n=>n.classes.has('current')).length,0);
console.log('Workflow UI: PASS (new phases, paused errors, aria-current, result)');
