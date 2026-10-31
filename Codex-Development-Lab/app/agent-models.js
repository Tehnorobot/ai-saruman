/* Per-role model settings. No credential is requested or displayed here. */
(() => {
 'use strict';
 const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node};
 const roles={planner:{title:'Планировщик',help:'Обязательны вызов инструментов и точный ответ JSON. Желательны сильное рассуждение и анализ причин ошибок. Планировщик читает проект и назначает следующий пакет задач.'},coder:{title:'Кодер',help:'Обязателен вызов инструментов для чтения, изменения и проверки кода. Нужна уверенная работа с языком проекта, сборкой и тестами. Кодер реализует назначение и отвечает на замечания ревьюера.'},reviewer:{title:'Ревьюер',help:'Обязательны чтение файлов через инструменты и точный JSON. Нужны сильный анализ логики, переходов состояний и тестов. Ревьюер работает только на чтение. Изображения нужны лишь при фактической передаче визуальных материалов.'}};
 const editable=new Set(['QUEUED','USER_PAUSED','QUOTA_PAUSED']);
 const fallbackCatalogue={litellm:[
  {id:'deepseek-ai/DeepSeek-V4-Flash-0731',efforts:['default']},
  {id:'Qwen/Qwen3-Next-80B-A3B-Instruct-FP8',efforts:['default']},
  {id:'google/gemma-4-26B-A4B-it',efforts:['default']},
  {id:'zai-org/GLM-5.3-Flash',efforts:['default']},
  {id:'Qwen/Qwen3.8-27B',efforts:['default']}
 ],minimax:[{id:'MiniMax-M3.1-Flash',efforts:['default','low','medium','high','xhigh','max']}]};
 const panel=make('section','panel');panel.id='agentModelsPanel';panel.append(make('h2','','Модели по ролям'),make('p','muted','Настройки действуют для следующих запусков. Для каждой роли можно выбрать своего провайдера, модель и reasoning. Ключи API задаются в .env.'),make('p','foot','Ревьюер — отдельная роль с доступом только на чтение. Без явного выбора он наследует модель кодера; это указано на кнопке роли. Ошибка доступа не вызывает скрытой смены модели. Независимый исполнитель запускает тесты программно. Скриншоты сохраняются, но не передаются моделям для визуального анализа.'));
 const note=make('p','muted');note.id='agentModelsNotice';note.setAttribute('role','status');panel.append(note);
 const row=make('div','row');panel.append(row);
 const statusAnchor=document.getElementById('status'),historyAnchor=document.getElementById('agentHistory');
 if(statusAnchor)statusAnchor.after(panel);else if(historyAnchor)historyAnchor.before(panel);else document.body.prepend(panel);
 let job=null,generation=0,state=null,pending=null,saving=false,loadedAt=0;
 const controls={};
 for(const [role,info] of Object.entries(roles)){
  const button=make('button','',info.title);button.type='button';button.disabled=true;button.id='configure-'+role;row.append(button);
  const dialog=make('dialog','acceptance-dialog');dialog.id='modelDialog-'+role;
  const title=make('h2','','Модель · '+info.title);title.id='modelTitle-'+role;dialog.setAttribute('aria-labelledby',title.id);
  const close=make('button','','Закрыть');close.type='button';close.onclick=()=>{dialog.close();button.focus()};
  const header=make('header','row');header.append(title,close);
  const form=make('form'),fields=make('div','row'),provider=make('select'),model=make('select'),effort=make('select');
  model.id='roleModels-'+role;
  for(const [value,label] of [['',role==='reviewer'?'Наследовать модель кодера':'Наследовать настройки задания'],['codex','Codex / ChatGPT'],['dslab','DSLab API'],['litellm','LiteLLM API'],['minimax','MiniMax / Explore']]){const option=make('option','',label);option.value=value;provider.append(option)}
  for(const value of ['default','none','minimal','low','medium','high','xhigh','max','ultra']){const option=make('option','',value==='default'?'По умолчанию модели':value);option.value=value;effort.append(option)}
  for(const [label,input] of [['Провайдер',provider],['Модель',model],['Reasoning',effort]]){const field=make('label','field muted',label);field.append(input);fields.append(field)}
  const save=make('button','primary','Сохранить');save.type='submit';
  const status=make('p','muted');status.setAttribute('role','status');
  const providerNote=make('p','foot','MiniMax Explore: используй Subscription Key в MINIMAX_API_KEY в .env. По умолчанию reasoning = max; low и medium уменьшают глубину рассуждения.');providerNote.hidden=true;
  form.append(fields,providerNote,save,status);dialog.append(header,make('p','',info.help),form);document.body.append(dialog);
  controls[role]={button,dialog,form,provider,model,effort,save,status,providerNote};
  provider.onchange=()=>{populateModels(role,'',false);update()};model.onchange=()=>capabilities(role);
  button.onclick=()=>{button.focus();fill(role);dialog.showModal()};
  form.onsubmit=async event=>{event.preventDefault();await saveRole(role)};
 }
 function allowed(){return Boolean(state&&(state.editable??editable.has(state.status))&&!saving)}
 function entriesFor(role){
  const c=controls[role],catalogue=state?.catalogue||state?.catalog||fallbackCatalogue;
  return Array.isArray(catalogue)?catalogue:catalogue[c.provider.value]||fallbackCatalogue[c.provider.value]||[];
 }
 function populateModels(role,desired,preserveUnknown){
  const c=controls[role],entries=entriesFor(role);c.model.replaceChildren();
  if(!c.provider.value||c.provider.value==='codex'){const option=make('option','',c.provider.value?'По умолчанию Codex':role==='reviewer'?'Наследовать модель кодера':'Наследовать модель задания');option.value='';c.model.append(option)}
  for(const item of entries){const name=typeof item==='string'?item:item.model||item.id;if(!name)continue;const option=make('option','',name);option.value=name;c.model.append(option)}
  if(desired&&![...c.model.options].some(option=>option.value===desired)&&preserveUnknown){const option=make('option','',desired+' · сохранённая модель вне каталога');option.value=desired;c.model.append(option)}
  if([...c.model.options].some(option=>option.value===desired))c.model.value=desired;
  capabilities(role);
 }
 function capabilities(role){
  const c=controls[role],entries=entriesFor(role);
  const selected=entries.find(item=>typeof item==='object'&&(item.model||item.id)===c.model.value);
  const supported=c.provider.value==='litellm'?['default']:selected?.efforts||['default'];
  c.providerNote.hidden=c.provider.value!=='minimax';
  for(const option of c.effort.options){option.hidden=!supported.includes(option.value);option.disabled=option.hidden;if(option.value==='default')option.textContent=c.provider.value==='minimax'?'По умолчанию MiniMax — max':'По умолчанию модели'}
  if(!supported.includes(c.effort.value))c.effort.value='default';
 }
 function fill(role){
  const c=controls[role],value=state?.models?.[role]||{};
  c.provider.value=value.provider||'';c.effort.value=value.reasoning_effort||'default';c.status.textContent='';
  populateModels(role,value.model||'',true);
 }
 function update(){
  for(const [role,c] of Object.entries(controls)){
   const available=state&&(state.roles||['planner','coder','reviewer']).includes(role);
   c.button.hidden=Boolean(state&&!available);c.button.disabled=!available||!allowed();
   for(const field of [c.provider,c.model,c.effort,c.save])field.disabled=!available||!allowed();
   if(c.dialog.open&&!allowed())c.status.textContent=saving?'Сохранение…':'Для изменения сначала приостанови задание.';
   const explicit=state?.models?.[role],value=explicit||state?.resolved?.[role];
   const inherited=state&&!explicit?(role==='reviewer'?' · из настроек кодера':' · из настроек задания'):'';
   c.button.textContent=roles[role].title+(value?.model?' · '+value.model:'')+inherited;
  }
 }
 async function jsonFetch(path,options){
  const response=await fetch(path,options);const value=await response.json();
  if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'HTTP '+response.status);return value;
 }
 async function load(){
  if(!job||pending||saving)return;const token={job,generation};pending=token;
  try{
   const result=await jsonFetch('/api/jobs/'+encodeURIComponent(job)+'/agent-models');
   if(token.job!==job||token.generation!==generation)return;
   state=result;loadedAt=Date.now();note.textContent=allowed()?'Выбери роль для изменения модели.':'Изменение доступно на паузе или до запуска задания.';update();
  }catch(error){if(token.job===job&&token.generation===generation){state=null;update();note.textContent='Не удалось загрузить модели: '+error.message}}
  finally{if(pending===token)pending=null}
 }
 async function saveRole(role){
  if(typeof active!=='undefined'&&active!==job){switchJob(active);return}
  if(!allowed())return;const token={job,generation},c=controls[role];saving=true;update();
  const models={...state.models};
  if(c.provider.value)models[role]={provider:c.provider.value,model:c.model.value.trim(),reasoning_effort:c.effort.value};
  else delete models[role];
  try{
   const result=await jsonFetch('/api/jobs/'+encodeURIComponent(token.job)+'/agent-models',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({models})});
   if(token.job!==job||token.generation!==generation)return;
   state=result;c.status.textContent='Сохранено для следующих запусков.';note.textContent=roles[role].title+': настройки сохранены.';
  }catch(error){if(token.job===job&&token.generation===generation)c.status.textContent='Ошибка: '+error.message}
  finally{if(token.job===job&&token.generation===generation){saving=false;update()}}
 }
 function switchJob(jid){
  if(jid===job)return;job=jid;generation++;state=null;pending=null;saving=false;loadedAt=0;
  for(const c of Object.values(controls)){c.dialog.close();c.button.hidden=false}update();note.textContent=jid?'Загрузка моделей…':'Выбери проект.';load();
 }
 window.agentModelsSwitchJob=switchJob;
 setInterval(()=>{switchJob(typeof active!=='undefined'?active:null);if(Date.now()-loadedAt>5000)load()},500);
 switchJob(typeof active!=='undefined'?active:null);
})();
