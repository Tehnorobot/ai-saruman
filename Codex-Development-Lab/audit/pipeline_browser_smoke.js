// Runs only inside a temporary documentation preview. Does not contact the app.
(function(){
 const assertions=[];
 const check=(value,label)=>{if(!value)throw Error(label);assertions.push(label);};
 const click=n=>{if(!n)throw Error('missing control');n.click();};
 try{
  check(document.querySelectorAll('#flow button').length===11,'11 blocks rendered');
  for(const b of document.querySelectorAll('#flow button')){click(b);check(document.querySelector('#block-detail h3').textContent===b.querySelector('strong').textContent,'block '+b.dataset.id);}
  check(document.querySelectorAll('#contract-select option').length===24,'24 contracts rendered');
  for(const option of document.querySelectorAll('#contract-select option')){
   const select=document.querySelector('#contract-select');select.value=option.value;select.dispatchEvent(new Event('change'));
   click(document.querySelector('[data-view="example"]'));
   const parsed=JSON.parse(document.querySelector('#contract-view pre').textContent);
   check(parsed.contract===option.value,'example '+option.value);
   click(document.querySelector('[data-view="fields"]'));
   check(document.querySelector('#contract-view table tbody tr').textContent.includes('contract'),'fields '+option.value);
   click(document.querySelector('[data-view="schema"]'));
   check(JSON.parse(document.querySelector('#contract-view pre').textContent).properties.contract.const===option.value,'schema '+option.value);
  }
  for(const which of ['type','unknown','cycle','semantic']){click(document.querySelector('[data-case="'+which+'"]'));check(document.querySelector('#validator-result strong').textContent.length>5,'validator '+which);}
  for(let i=0;i<5;i++)click(document.querySelector('#demo-step'));
  check(document.querySelector('#demo-step').disabled,'walkthrough completed');
  check([...document.querySelectorAll('.timeline-row')].map(n=>n.dataset.status).join(',')==='PASS,FAIL,NOT_EXECUTED','checkpoint states');
  click(document.querySelector('#demo-reset'));check(!document.querySelector('#demo-step').disabled,'walkthrough reset');
  check(document.documentElement.scrollWidth<=window.innerWidth,'no page overflow at '+window.innerWidth);
  click(document.querySelector('#flow button[data-id="controller"]'));
  document.querySelector('#contract-select').value='PlannerDecision';document.querySelector('#contract-select').dispatchEvent(new Event('change'));click(document.querySelector('[data-view="example"]'));
  window.scrollTo(0,0);
  const result=document.createElement('pre');result.id='smoke-result';result.hidden=true;result.textContent=JSON.stringify({ok:true,assertions:assertions.length,width:window.innerWidth});document.body.append(result);
 }catch(error){const result=document.createElement('pre');result.id='smoke-result';result.textContent=JSON.stringify({ok:false,error:String(error),assertions:assertions.length});document.body.append(result);}
})();
