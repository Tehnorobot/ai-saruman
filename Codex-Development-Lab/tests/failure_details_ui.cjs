const assert=require('node:assert/strict');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.open=false;this.text=''}
 set textContent(text){this.text=String(text)}
 get textContent(){return [this.text,...this.children.map(c=>c.textContent)].join(' ')}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=children}
 querySelectorAll(){return this.children.flatMap(n=>[...(n.tag==='details'&&n.open?[n]:[]),...n.querySelectorAll()])}
}
const document={createElement:tag=>new Element(tag)},window={};
function checkFailureRendering(){
 const parent=new Element('div'),payload='<img src=x onerror=alert(1)>';
 const facts={counts:{passed:1,failed:1,not_executed:1},checks:[
  {classname:'Combat',name:'select',status:'TEST_PASSED',reason:'Confirmed'},
  {classname:'Combat',name:'hit',status:'FAIL',reason:payload},
  {classname:'Combat',name:'stop',status:'NOT_EXECUTED',reason:'Причина не установлена'}],scope_note:'Не подтверждает весь пункт ТЗ'};
 window.renderFailureDetails(parent,facts);
 assert.match(parent.textContent,/Пройдено: 1 · Ошибки: 1 · Без результата: 1/);
 assert.match(parent.textContent,/Нет результата в отчёте/);
 assert.ok(parent.textContent.includes(payload));
 assert.ok(parent.children.filter(n=>n.tag==='details').some(n=>n.dataset.checkStatus==='FAIL'&&n.open));
 const children=parent.children;window.renderFailureDetails(parent,facts);
 assert.equal(parent.children,children,'An unchanged poll must preserve open details');
 window.renderFailureDetails(parent,null);assert.equal(parent.children.length,0);
 console.log('Failure details UI: PASS');
}
