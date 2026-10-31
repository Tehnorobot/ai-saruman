const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.value='';this.open=false;this.scrollTop=0;this.text=''}
 set textContent(value){this.text=String(value);this.children=[]}
 get textContent(){return [this.text,...this.children.map(x=>x.textContent)].join(' ')}
 set innerHTML(value){throw Error('Untrusted memory must not be HTML')}
 append(...items){this.children.push(...items);for(const item of items)item.parent=this}
 replaceChildren(...items){this.children=[];this.append(...items)}
 remove(){this.parent.children=this.parent.children.filter(x=>x!==this)}
 setAttribute(){} addEventListener(){} focus(){} before(){} after(){}
 showModal(){this.open=true} close(){this.open=false}
 querySelectorAll(selector){return this.children.flatMap(x=>[...(selector==='details[open]'&&x.tag==='details'&&x.open?[x]:[]),...x.querySelectorAll(selector)])}
 querySelector(selector){return all(this).find(x=>selector==='details'?x.tag==='details':selector.startsWith('.')&&String(x.className||'').split(' ').includes(selector.slice(1)))||null}
}
const body=new Element('body');
const all=node=>[node,...node.children.flatMap(all)];
const document={body,createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment'),getElementById:id=>all(body).find(x=>x.id===id)||null};
const path='errors/'+'a'.repeat(64)+'.json',requests=[];
const fetch=async url=>{
 requests.push(url);
 let data;
 if(url.endsWith('/round-task'))data={task:{state:'missing'}};
 else if(url.includes('/archive/record?'))data={state:'available',data:{detail:'<img src=x onerror=alert(1)>',status:'OPEN'}};
 else if(url.includes('/archive?'))data={kind:url.includes('kind=notes')?'notes':'errors',page_number:1,
  index:{data:{counts:{errors:150,notes:24,open_errors:3},error_pages:[{},{}],note_pages:[{}]}},
  page:{state:'available',data:{items:[{path,label:'Old failure',status:'OPEN',first_round:1,last_round:2}]}}};
 else data={current:{state:'missing'},rounds:[],retention_note:'Full archive retained'};
 return {ok:true,json:async()=>data};
};
const window={renderFailureDetails(){}};
const context={document,window,fetch,active:'a'.repeat(14),setInterval(){},console,Date,JSON,Set,encodeURIComponent};
vm.runInNewContext(fs.readFileSync(process.argv[2]||'app/memory.js','utf8'),context);
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate)};
(async()=>{
 document.getElementById('openMemory').onclick();await flush();
 const select=document.getElementById('memoryRound');
 assert.ok(select.children.some(x=>x.value==='archive-errors'));
 assert.ok(select.children.some(x=>x.value==='archive-notes'));
 select.value='archive-errors';select.onchange();await flush();
 assert.match(document.getElementById('memoryContent').textContent,/150/);
 const button=all(body).find(x=>x.tag==='button'&&x.textContent.includes('Прочитать запись целиком'));
 await button.onclick();
 assert.ok(document.getElementById('memoryContent').textContent.includes('<img src=x onerror=alert(1)>'));
 assert.ok(requests.some(x=>x.includes('/archive/record?')));
 select.value='archive-notes';select.onchange();await flush();
 assert.ok(requests.some(x=>x.includes('kind=notes')));
 console.log('Memory archive UI: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
