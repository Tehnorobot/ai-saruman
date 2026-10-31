/* Provider settings and usage polling are independent from media rendering. */
const claudeModels=['claude-sonnet-5-5','claude-opus-5-5'];
const providerFallbackCatalogue={
 dslab:claudeModels.map(id=>({id,efforts:['default','low','medium','high','xhigh','max']})),
 litellm:['deepseek-ai/DeepSeek-V4-Flash-0731','Qwen/Qwen3-Next-80B-A3B-Instruct-FP8','google/gemma-4-26B-A4B-it','zai-org/GLM-5.3-Flash','Qwen/Qwen3.8-27B'].map(id=>({id,efforts:['default']})),
 minimax:[{id:'MiniMax-M3.1-Flash',efforts:['default','low','medium','high','xhigh','max']}]
};
let providerCatalogue={};
function providerFields(form, prefix, modelId, effortId){
 const row=el('div','row'),label=el('label','field muted','Провайдер');
 const provider=el('select');provider.id=prefix+'Provider';provider.name='provider';
 for(const [value,title] of [['codex','Codex / ChatGPT'],['dslab','DSLab / Claude API'],['litellm','LiteLLM API'],['minimax','MiniMax / Explore']]){const option=el('option','',title);option.value=value;provider.append(option)}
 label.append(provider);row.append(label);
 const modelsLabel=el('label','field muted'),modelsCaption=el('span','','Модель API'),models=el('select');models.id=prefix+'ApiModel';
 modelsLabel.append(modelsCaption,models);row.append(modelsLabel);
 const upload=form.querySelector('input[type="file"]');if(upload)upload.after(row);else form.prepend(row);
 const details=el('details'),summary=el('summary','','Лимиты API на раунд');details.append(summary);
 const limits=el('div','row');
 for(const [name,title,value,min] of [
  ['dslab_token_budget','Общий лимит токенов',1000000,1000],
  ['dslab_max_output_tokens','Максимум ответа',8192,1024],
  ['dslab_max_calls','Максимум запросов',100,1]]){
  const field=el('label','field muted',title),input=el('input');input.type='number';input.name=name;input.value=value;input.min=min;input.step=1;input.style.width='100%';field.append(input);limits.append(field);
 }
 details.append(limits,el('p','foot','В новом пайплайне это общий бюджет API на раунд: планирование, код, ревью, коррекции и повторы. Токены включают вход, выход и кеш. Перед запросом шлюз резервирует объём следующего входа и ответа; остановка возможна раньше номинального лимита. При временных сбоях выполняется повтор с сохранением истории и общего бюджета.'));
 details.append(el('p','foot','Верхние границы этих трёх настроек со стороны приложения сняты. Заданные бюджеты продолжают действовать. Размер ответа должен поддерживаться выбранной моделью и провайдером.'));
 const minimaxNote=el('p','foot','MiniMax Explore: Subscription Key задаётся в MINIMAX_API_KEY в .env. Reasoning по умолчанию — max; для меньшего расхода выбери low или medium.');details.append(minimaxNote);
 details.append(el('p','foot','Максимум ответа действует и для планировщика, и для кодера и включает thinking. Он ограничен доступным резервом стадии. Ошибка max_tokens означает обрыв одного ответа; это не исчерпание общего бюджета.'));
 form.append(details);
 const model=q(modelId),effort=q(effortId),maxOption=el('option','','Max');maxOption.value='max';effort.append(maxOption);
 const codexModelLabel=model.closest('label');row.append(codexModelLabel);
 let lastProvider=provider.value;
 function update(reset=true){
  const isApi=provider.value!=='codex',entries=providerCatalogue[provider.value]||providerFallbackCatalogue[provider.value]||[];
  if(reset&&provider.value!==lastProvider)model.value='';
  lastProvider=provider.value;
  modelsLabel.hidden=!isApi;details.hidden=prefix==='new'&&!isApi;model.readOnly=isApi;minimaxNote.hidden=provider.value!=='minimax';
  // Keep the hidden canonical input enabled: FormData still needs its model ID.
  codexModelLabel.hidden=isApi;
  if(isApi){
   modelsCaption.textContent=provider.value==='minimax'?'Модель MiniMax':provider.value==='dslab'?'Модель Claude':'Модель LiteLLM';
   const valid=entries.some(entry=>entry.id===model.value);if(!valid)model.value=entries[0]?.id||'';
   models.replaceChildren(...entries.map(entry=>{const option=el('option','',entry.id);option.value=entry.id;return option}));models.value=model.value;
  }
  const selected=entries.find(entry=>entry.id===model.value);
  const supported=selected?.efforts||(isApi?['default']:['default','none','minimal','low','medium','high','xhigh']);
  for(const option of effort.options){option.hidden=!supported.includes(option.value);option.disabled=option.hidden;if(option.value==='default')option.textContent=provider.value==='minimax'?'По умолчанию MiniMax — max':'По умолчанию модели'}
  if(effort.selectedOptions[0]?.hidden)effort.value='default';
 }
 provider.onchange=()=>update();models.onchange=()=>{model.value=models.value;update(false)};model.oninput=()=>update(false);update(false);
 return {provider,model,effort,update,form};
}
const newProviderFields=providerFields(q('form'),'new','model','reasoning');
const savedProviderFields=providerFields(q('updateSettings'),'saved','updateModel','updateReasoning');
fetch('/api/providers').then(r=>r.json()).then(report=>{
 providerCatalogue=report.catalogue||{};
 const list=document.getElementById('codexModels');
 if(list&&providerCatalogue.codex)list.replaceChildren(...providerCatalogue.codex.map(item=>{const option=el('option');option.value=item.id;return option}));
 for(const fields of [newProviderFields,savedProviderFields]){
  for(const value of ['max','ultra'])if(![...fields.effort.options].some(o=>o.value===value)){const option=el('option','',value);option.value=value;fields.effort.append(option)}
  fields.update(false);
 }
}).catch(()=>{});
window.loadProviderSettings=function(job){
 const fields=savedProviderFields;fields.provider.value=job.provider||'codex';fields.model.value=job.model||'';fields.effort.value=job.reasoning_effort||'default';
 for(const name of ['dslab_token_budget','dslab_max_output_tokens','dslab_max_calls'])fields.form.elements[name].value=job[name]??({'dslab_token_budget':1000000,'dslab_max_output_tokens':8192,'dslab_max_calls':100}[name]);
 fields.update(false);
};
// The first dashboard request can finish before this external script loads.
settingsFor=null;
if(active){const jid=active;q('saveSettings').disabled=true;api('/api/jobs/'+jid).then(job=>{
 if(active===jid){window.loadProviderSettings(job);settingsFor=jid;q('saveSettings').disabled=!['QUEUED','USER_PAUSED','QUOTA_PAUSED'].includes(job.status)}
}).catch(()=>{})}

const usagePanel=el('section','panel');usagePanel.id='usagePanel';usagePanel.append(el('h2','','Расход токенов'));
const usageSummary=el('div','muted','Выбери проект.'),openUsage=el('button','','История запросов');openUsage.type='button';
usagePanel.append(usageSummary,openUsage);q('agentHistory').before(usagePanel);
const usageDialog=el('dialog','acceptance-dialog');usageDialog.id='usageDialog';usageDialog.setAttribute('aria-labelledby','usageHeading');
const usageHeader=el('header'),usageHeading=el('h2','','История запросов');usageHeading.id='usageHeading';
const closeUsage=el('button','','Закрыть');closeUsage.type='button';usageHeader.append(usageHeading,closeUsage);
const usageDetails=el('div');usageDetails.id='usageDetails';
const requestStatus=el('p','muted','Выбери проект.');requestStatus.setAttribute('role','status');
const requestTable=el('table'),requestHead=el('thead'),requestHeading=el('tr'),requestBody=el('tbody');
requestTable.setAttribute('aria-label','Запросы всех провайдеров');
for(const title of ['Время','Провайдер','Роль','Модель','Статус','Вход','Выход','Кеш: запись / чтение','ID запроса'])requestHeading.append(el('th','',title));
requestHead.append(requestHeading);requestTable.append(requestHead,requestBody);
const loadMoreRequests=el('button','','Загрузить ещё');loadMoreRequests.type='button';loadMoreRequests.hidden=true;
usageDetails.append(requestStatus,requestTable,loadMoreRequests);usageDialog.append(usageHeader,usageDetails);
document.body.append(usageDialog);openUsage.onclick=()=>{openUsage.focus();usageDialog.showModal();refreshUsage()};closeUsage.onclick=()=>{usageDialog.close();openUsage.focus()};
let usageJob=null,usageState=null;
const usageNumber=value=>value===null||value===undefined?'—':Number(value).toLocaleString('ru-RU');
function renderUsage(report,jid){
 window.dispatchEvent(new CustomEvent('lab-usage',{detail:{job_id:jid,report}}));
 const totals=report.totals||{},input=totals.input_tokens_total??(Number(totals.input_tokens||0)+Number(totals.cache_read_input_tokens||0)+Number(totals.cache_creation_input_tokens||0));
 usageSummary.textContent='Вход: '+usageNumber(input)+' · Выход: '+usageNumber(totals.output_tokens)+' · Из кеша: '+usageNumber(totals.cache_read_input_tokens);
}
function requestTime(value){
 if(value===null||value===undefined||value==='')return '—';
 const date=new Date(typeof value==='number'?value*1000:value);
 return Number.isFinite(date.getTime())?date.toLocaleString('ru-RU'):String(value);
}
function requestDiagnostic(value){
 if(!value)return '';
 if(typeof value==='string'){try{return JSON.stringify(JSON.parse(value),null,2)}catch{return value}}
 return JSON.stringify(value,null,2);
}
function renderRequests(state){
 // Keep DOM rows to preserve open diagnostics, focus and text selection during polling.
 let position=0;
 for(const request of state.rows.values()){
  const id=String(request.request_id),cached=state.nodes.get(id);
  const item=cached||{row:el('tr'),cells:[]};
  if(!cached){for(let i=0;i<9;i++){const cell=el('td');item.cells.push(cell);item.row.append(cell)}state.nodes.set(id,item)}
  const input=request.input_tokens_total??(request.input_tokens===null||request.input_tokens===undefined?null:Number(request.input_tokens)+Number(request.cache_read_input_tokens||0)+Number(request.cache_creation_input_tokens||0));
  const values=[requestTime(request.started_at),request.provider||'—',request.role||'—',request.model||'—',request.status||'—',usageNumber(input),usageNumber(request.output_tokens),usageNumber(request.cache_creation_input_tokens)+' / '+usageNumber(request.cache_read_input_tokens),id];
  values.forEach((value,index)=>{const cell=item.cells[index];if(cell._value!==value){cell.textContent=value;cell._value=value}});
  const diagnostic=requestDiagnostic(request.diagnostic_json);
  if(item.diagnostic!==diagnostic){
   if(item.details)item.details.remove();item.details=null;
   if(diagnostic){const details=el('details');details.append(el('summary','','Ошибка'),el('pre','',diagnostic));item.cells[4].append(details);item.details=details}
   item.diagnostic=diagnostic;
  }else if(item.details&&!item.details.parentNode)item.cells[4].append(item.details);
  if(requestBody.children[position]!==item.row)requestBody.insertBefore(item.row,requestBody.children[position]||null);
  position++;
 }
 loadMoreRequests.hidden=!state.cursor;loadMoreRequests.disabled=state.moreBusy;
}
function mergeRequests(state,report,older){
 const page=new Map((report.requests||[]).filter(row=>row.request_id!==undefined&&row.request_id!==null).map(row=>[String(row.request_id),row]));
 if(older){
  for(const [id,row] of page)if(!state.rows.has(id))state.rows.set(id,row);
  state.cursor=report.next_cursor||null;
  if(state.cursorRefresh){state.cursor=state.cursorRefresh.cursor;state.loadedOlder=false;state.cursorRefresh=null}
 }else{
  // A changed first page can contain a gap after a burst of calls, or after
  // historical indexing. Restart pagination while retaining all visible rows.
  const head=page.keys().next().value;
  const restart=(state.firstHead!==undefined&&head!==state.firstHead)||(state.codexLoading&&!report.codex_loading);
  state.firstHead=head;state.codexLoading=!!report.codex_loading;
  state.rows=new Map([...page,...[...state.rows].filter(([id])=>!page.has(id))]);
  if(restart){
   state.loadedOlder=false;
   if(state.moreBusy)state.cursorRefresh={cursor:report.next_cursor||null};
   else state.cursor=report.next_cursor||null;
  }else if(!state.loadedOlder&&!state.moreBusy)state.cursor=report.next_cursor||null;
 }
 // Loaded gaps must be inserted into their stream order, not appended behind
 // older rows. Native sequence survives clock changes; CLI has no API sequence.
 const entries=[...state.rows];
 const native=entries.filter(([,r])=>r.sequence!==undefined).sort((a,b)=>Number(b[1].sequence)-Number(a[1].sequence));
 const cli=entries.filter(([,r])=>r.provider==='codex'&&r.sequence===undefined).sort((a,b)=>{
  const stamp=r=>typeof r.started_at==='number'?r.started_at*1000:Date.parse(r.started_at)||0;
  return stamp(b[1])-stamp(a[1])||String(b[1].task_id).localeCompare(String(a[1].task_id))||Number(b[1].turn_offset||0)-Number(a[1].turn_offset||0);
 });
 let ni=0,ci=0;
 state.rows=new Map(entries.map(entry=>entry[1].sequence!==undefined?native[ni++]:entry[1].provider==='codex'?cli[ci++]:entry));
 renderRequests(state);
 requestStatus.textContent=report.available===false?'История временно недоступна. Последние данные сохранены.':state.codexLoading?'Индексируется история…':state.rows.size?'Показано запросов: '+state.rows.size:'Запросов пока нет.';
}
async function usageFetch(url,state){
 const controller=new AbortController();state.controllers.add(controller);
 const timeout=setTimeout(()=>controller.abort(),8000);
 try{const response=await fetch(url,{signal:controller.signal});if(!response.ok)throw new Error('HTTP '+response.status);return await response.json()}
 finally{clearTimeout(timeout);state.controllers.delete(controller)}
}
async function refreshRequests(state,older=false){
 const key=older?'moreBusy':'busy';if(state[key]||(older&&!state.cursor))return;
 state[key]=true;if(older)state.loadedOlder=true;loadMoreRequests.disabled=state.moreBusy;
 const cursor=older?'&cursor='+encodeURIComponent(state.cursor):'';
 try{
  const report=await usageFetch('/api/jobs/'+encodeURIComponent(state.jid)+'/requests?limit=50'+cursor,state);
  if(active!==state.jid||usageState!==state)return;
  if(report.available===false){if(report.codex_loading)state.codexLoading=true;requestStatus.textContent=report.codex_loading?'Индексируется история…':'История временно недоступна. Последние данные сохранены.';return}
  mergeRequests(state,report,older);
 }catch(error){if(active===state.jid&&usageState===state)requestStatus.textContent='Не удалось обновить историю. Последние данные сохранены.'}
 finally{
  state[key]=false;
  if(older&&state.cursorRefresh){state.cursor=state.cursorRefresh.cursor;state.loadedOlder=false;state.cursorRefresh=null}
  if(usageState===state){loadMoreRequests.hidden=!state.cursor;loadMoreRequests.disabled=state.moreBusy}
 }
}
async function refreshUsageSummary(state){
 if(state.summaryBusy)return;state.summaryBusy=true;
 try{const report=await usageFetch('/api/jobs/'+encodeURIComponent(state.jid)+'/usage',state);if(active!==state.jid||usageState!==state)return;if(report.available)renderUsage(report,state.jid);else if(!state.hasSummary)usageSummary.textContent='Статистика пока недоступна.';if(report.available)state.hasSummary=true}
 catch(error){if(active===state.jid&&usageState===state&&!state.hasSummary)usageSummary.textContent='Не удалось обновить расход.'}
 finally{state.summaryBusy=false}
}
function refreshUsage(){
 const jid=active;
 if(jid!==usageJob||!usageState){
  if(usageState)for(const controller of usageState.controllers)controller.abort();
  usageJob=jid;usageState={jid,rows:new Map(),nodes:new Map(),controllers:new Set(),cursor:null,loadedOlder:false,busy:false,moreBusy:false,summaryBusy:false,hasSummary:false};
  usageSummary.textContent=jid?'Загрузка расхода…':'Выбери проект.';requestStatus.textContent=jid?'Загрузка запросов…':'Выбери проект.';requestBody.replaceChildren();loadMoreRequests.hidden=true;
 }
 if(!jid)return;
 // Slow summaries and older-page fetches do not block live request updates.
 void refreshRequests(usageState);void refreshUsageSummary(usageState);
}
loadMoreRequests.onclick=()=>{refreshUsage();if(usageState.jid)void refreshRequests(usageState,true)};
setInterval(refreshUsage,2000);refreshUsage();
