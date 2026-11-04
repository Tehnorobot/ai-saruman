/* Read-only semantic pipeline window. Full records are fetched on demand. */
(() => {
 'use strict';
 const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node;};
 const asArray=value=>Array.isArray(value)?value:[];
 const button=document.getElementById('pipelineOpen');
 const dialog=make('dialog','pipeline-dialog');dialog.id='pipelineDialog';dialog.setAttribute('aria-labelledby','pipelineTitle');
 const header=make('header','pipeline-header'),title=make('h2','','Пайплайн и решения');title.id='pipelineTitle';
 const close=make('button','','Закрыть');close.type='button';header.append(title,close);
 const identity=make('p','muted');identity.id='pipelineIdentity';
 const stage=make('p','pipeline-stage');stage.id='pipelineStage';stage.setAttribute('role','status');
 const notice=make('p','pipeline-notice');notice.id='pipelineNotice';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
 const controls=make('div','pipeline-tabs');controls.setAttribute('aria-label','Разделы пайплайна');
 const tabs=new Map();
 for(const [id,label] of [['overview','Пакет работы'],['tasks','Задачи и условия'],['review','Ревью и рефлексия'],['memory','Полная память']]){
  const control=make('button','',label);control.type='button';control.dataset.pipelineTab=id;control.setAttribute('aria-pressed',id==='overview'?'true':'false');tabs.set(id,control);controls.append(control);
 }
 const refresh=make('button','','Обновить');refresh.type='button';refresh.id='pipelineRefresh';controls.append(refresh);
 const content=make('div');content.id='pipelineContent';
 const memory=make('section','pipeline-memory');memory.id='pipelineMemory';memory.hidden=true;
 const memoryControls=make('div','pipeline-memory-controls');
 const taskLabel=make('label','field muted','Задача'),taskSelect=make('select');taskSelect.id='pipelineMemoryTask';taskSelect.setAttribute('aria-label','Задача в памяти');taskLabel.append(taskSelect);
 const searchLabel=make('label','field muted','Поиск'),search=make('input');search.type='text';search.id='pipelineMemorySearch';search.placeholder='Ошибка, гипотеза или решение';searchLabel.append(search);
 const find=make('button','','Найти');find.type='button';find.id='pipelineMemoryFind';
 const prev=make('button','','← Назад'),next=make('button','','Далее →');prev.type=next.type='button';prev.id='pipelineMemoryPrev';next.id='pipelineMemoryNext';prev.disabled=next.disabled=true;
 memoryControls.append(taskLabel,searchLabel,find);
 const pagination=make('div','row'),pageLabel=make('span','muted');pageLabel.id='pipelineMemoryPage';pagination.append(prev,pageLabel,next);
 const records=make('div');records.id='pipelineMemoryRecords';memory.append(memoryControls,pagination,records);
 dialog.append(header,identity,stage,controls,notice,content,memory);document.body.append(dialog);
 let job=null,generation=0,tab='overview',overview=null,overviewPending=null,pagePending=null,fingerprint='';
 let cursor=null,nextCursor=null,cursorHistory=[],memoryLoaded=false,filterTask='',filterQuery='',opener=null;
 const recordRequests=new Set();
 const openVerificationHistory=new Set();
 const stages={ingest:'Приём ТЗ',decompose:'Разбираю ТЗ и зависимости',planning:'Выбираю пакет работы',coding:'Реализую код',review:'Проверяю код и сценарии',verification:'Независимая сборка и тесты',reflection:'Пересматриваю подход',waiting:'Ожидаю внешние условия',paused:'На паузе',done:'Проверка завершена'};
 const statuses={verified:'Подтверждено',failed:'Не пройдено',untested:'Ещё не проверено',incomplete:'Проверка не завершена',partial:'Частично подтверждено',manual_pending:'Нужна ручная проверка',needs_retest:'Нужна перепроверка',pending:'Ожидает',in_progress:'В работе',blocked:'Есть внешняя зависимость'};
 const actions={implement:'Реализация',repair:'Исправление',diagnose:'Исследование',verify:'Проверка',manual:'Ручная проверка'};
 const verdicts={changes_requested:'Нужны изменения',ready_for_verification:'Можно передавать на независимую проверку',needs_experiment:'Нужен эксперимент'};
 const empty=(parent,text)=>parent.append(make('p','muted',text));
 function card(parent,heading,text){const block=make('section','pipeline-card');block.append(make('h3','',heading));if(text!==undefined)block.append(make('p','',text));parent.append(block);return block;}
 function jsonDetails(parent,label,value){if(value===null||value===undefined)return;const detail=make('details','pipeline-json');detail.append(make('summary','',label),make('pre','',JSON.stringify(value,null,2)));parent.append(detail);}
 function list(parent,items){const values=asArray(items);if(!values.length)return;const ul=make('ul');for(const value of values)ul.append(make('li','',typeof value==='string'?value:JSON.stringify(value)));parent.append(ul);}
 function resultClass(value){return ['verified','verified_pass','PASS'].includes(value)?'pipeline-ok':['failed','verified_fail','FAIL','ERROR'].includes(value)?'pipeline-error':['needs_retest','incomplete','partial'].includes(value)?'pipeline-warning':'muted';}
 async function request(path){const response=await fetch(path,{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);return response.json();}
 function base(jid=job){return '/api/jobs/'+encodeURIComponent(jid)+'/pipeline';}
 function renderPackage(parent){
  if(overview?.preparation){const prep=overview.preparation;
   card(parent,'Подготовка',Math.floor((prep.elapsed_seconds||0)/60)+' мин активной подготовки · мягкая граница '+Math.round(prep.soft_limit_seconds/60)+' мин'+
    (prep.handoff_due?' · переход к реализации доступных задач':' · уточнение ТЗ и плана'));}
  if(overview?.handoff){const handoff=card(parent,'Рабочие решения после подготовки',overview.handoff.rationale);
   list(handoff,overview.handoff.working_decisions);
   for(const task of asArray(overview.handoff.tasks))handoff.append(make('p','',task.task_id+' · '+({ready:'Доступна',experiment:'Эксперимент',needs_user:'Нужно уточнение пользователя',external:'Внешняя зависимость'}[task.status]||task.status)+' · '+task.reason),make('p','foot',task.experiment||task.return_when));
   handoff.append(make('p','foot','Передача в работу не подтверждает готовность. Итоговое ревью и независимые тесты обязательны.'));
   jsonDetails(handoff,'Полное решение о переходе',overview.handoff);}
  if(overview?.catalog){const catalog=overview.catalog;const progress=overview.decomposition;
   card(parent,'Сохранённые результаты','Требований: '+asArray(catalog.requirements).length+' · условий: '+asArray(catalog.conditions).length+
    ' · задач: '+(overview.graph?asArray(overview.graph.tasks).length:'граф ещё не принят')+
    ' · записей памяти: '+(overview.memory_summary?.record_count??'—')+
    (progress?.semantic_review==='pending'?' · ожидает семантического ревью':''));}
  const decision=overview?.decision;
  if(!decision){empty(parent,'Принятый пакет ещё не сохранён. Это не означает потерю исходников.');return;}
  const block=card(parent,'Назначение раунда '+(decision.scope?.round??overview.scope?.round??'—'),decision.rationale||decision.summary||'Основание выбора не записано.');
  const rows=asArray(decision.actions);
  block.append(make('p','muted','Действий в пакете: '+rows.length+' · стратегия: '+(decision.strategy||'—')));
  for(const action of rows){const item=card(block,(actions[action.kind]||action.kind||'Действие')+' · '+(action.id||'—'),action.goal||'Цель не записана.');
   item.append(make('p','muted','Задачи: '+asArray(action.task_ids).join(', ')+' · условия: '+asArray(action.condition_ids).join(', ')));
   if(action.expected_observation)item.append(make('p','','Проверяемый результат: '+action.expected_observation));
   if(asArray(action.depends_on_action_ids).length)item.append(make('p','foot','После действий: '+action.depends_on_action_ids.join(', ')));
  }
  const deferred=asArray(decision.deferred);if(deferred.length){const section=card(parent,'Отложенные задачи');for(const item of deferred)section.append(make('p','',item.task_id+': '+item.reason),make('p','foot','Вернуться, когда: '+item.return_when));}
  const pending=asArray(decision.manual_checks);if(pending.length){const section=card(parent,'Ручные проверки');for(const check of pending){section.append(make('p','','Условия: '+asArray(check.condition_ids).join(', ')));list(section,check.steps);section.append(make('p','','Ожидается: '+check.expected));}}
  jsonDetails(parent,'Полный JSON принятого решения',decision);
 }
 function renderTasks(parent){
  const graph=overview?.graph,catalog=overview?.catalog;
  if(!graph){
   empty(parent,'Граф задач ещё не принят. Исходное ТЗ сохраняется отдельно.');
   if(catalog){const section=card(parent,'Разбор ТЗ сохранён · ожидает ревью','Требований: '+asArray(catalog.requirements).length+' · условий: '+asArray(catalog.conditions).length);
    for(const req of asArray(catalog.requirements))section.append(make('p','',req.id+' · '+req.statement));
    jsonDetails(section,'Полный промежуточный каталог',catalog);}
   return;
  }
  const conditions=asArray(catalog?.conditions),coverage=new Map(asArray(overview.status?.coverage).map(row=>[row.condition_id,row]));
  const taskStates=new Map(asArray(overview.status?.task_states).map(row=>[row.task_id,row]));
  const active=new Set(asArray(overview.status?.active_task_ids));
  const names=new Map(asArray(graph.tasks).map(row=>[row.id,row.title]));
  const display=overview.verification_display||{};
  for(const task of asArray(graph.tasks)){
   const section=card(parent,task.id+' · '+task.title,task.outcome);
   section.className+=' pipeline-task'+(active.has(task.id)?' pipeline-task-active':'');
   const taskDisplay=display.tasks?.[task.id];
   const state=taskDisplay?.state||taskStates.get(task.id)?.status;
   if(active.has(task.id))section.append(make('p','pipeline-active-label','В текущем пакете'));
   if(state)section.append(make('p','pipeline-task-status '+resultClass(state),statuses[state]||state));
   const dependencies=asArray(task.depends_on);
   section.append(make('p','muted',dependencies.length?'Зависит от: '+dependencies.map(id=>id+' · '+(names.get(id)||'неизвестная задача')).join('; '):'Нет задач-предпосылок'));
   section.append(make('p','foot','Требования ТЗ: '+asArray(task.requirement_ids).join(', ')));
   const ids=new Set(asArray(task.condition_ids)),linked=conditions.filter(row=>ids.has(row.id));
   if(!linked.length)empty(section,'Условия задачи ещё не сопоставлены. Готовность не подтверждена.');
   for(const condition of linked){const status=display.conditions?.[condition.id]?.state||coverage.get(condition.id)?.status||'untested';
    const line=make('p');line.append(make('span','',condition.id+' · '),make('span',resultClass(status),statuses[status]||status),make('span','', ' — '+condition.expected));section.append(line);}
   if(taskDisplay?.has_history){const detail=make('details','pipeline-verification-history');
    detail.append(make('summary','','История проверки'));
    if(taskDisplay.last_common_success)detail.append(make('p','foot','Все условия проходили: раунд '+taskDisplay.last_common_success.round+'.'));
    if(taskDisplay.latest){const last=taskDisplay.latest;
     detail.append(make('p','foot','Последний прогон: раунд '+last.round+' — '+(last.reason==='partial_selection'?'проверялась часть условий':({passed:'проверки прошли',failed:'есть непройденные проверки',incomplete:'проверка не завершена'}[last.outcome]||'нет полного результата'))+'.'));}
    if(state==='needs_retest')detail.append(make('p','foot','Для текущей версии нужно новое подтверждение. Предыдущий успех сохранён в истории.'));
    const key=job+':'+task.id;detail.open=openVerificationHistory.has(key);
    detail.addEventListener('toggle',()=>{if(detail.open)openVerificationHistory.add(key);else openVerificationHistory.delete(key);});
    section.append(detail);}
   if(task.verification_approach)section.append(make('p','foot','Способ проверки: '+task.verification_approach));
  }
  if(asArray(catalog?.open_questions).length){const section=card(parent,'Вопросы и решения');
   const resolutions=new Map(asArray(catalog.question_resolutions).map(row=>[row.question,row]));
   for(const question of catalog.open_questions){const resolution=resolutions.get(question);section.append(make('p','',question));
    if(resolution){section.append(make('p','foot',(resolution.status==='resolved'?'Решено':'Открыто')+' · '+({planner:'Планировщик',coder:'Кодер',user:'Пользователь'}[resolution.owner]||resolution.owner)));
     section.append(make('p','',resolution.answer||resolution.reason));}else section.append(make('p','foot','Ожидает классификации'));}}
  jsonDetails(parent,'Полный граф задач',graph);jsonDetails(parent,'Полный каталог требований и условий',catalog);
 }
 function renderReview(parent){
  const review=overview?.review;
  if(!review)empty(parent,'Ревью ещё не сохранено. Отсутствие замечаний не означает PASS.');
  else{
   const section=card(parent,'Последнее ревью',verdicts[review.disposition]||review.disposition||'Результат не записан.');
   for(const finding of asArray(review.findings)){
    const item=card(section,(finding.id||'—')+' · '+(finding.kind||'замечание'),finding.observation);
    if(finding.consequence)item.append(make('p','','Риск: '+finding.consequence));
    if(finding.suggested_check)item.append(make('p','','Проверка: '+finding.suggested_check));
    item.append(make('p','foot','Требования: '+asArray(finding.requirement_ids).join(', ')+' · серьёзность: '+(finding.severity||'—')));
    for(const source of asArray(finding.code_refs)){item.append(make('p','foot',(source.artifact?.path||source.path||'файл')+':'+(source.start_line??'?')+'–'+(source.end_line??'?')));if(source.excerpt)item.append(make('pre','',source.excerpt));}
   }
   list(section,review.unknowns);jsonDetails(section,'Полный JSON ревью',review);
  }
  const reflection=overview?.reflection;
  if(reflection){const section=card(parent,'Рефлексия',reflection.rationale||reflection.summary||'План пересмотрен.');
   for(const hypothesis of asArray(reflection.hypotheses)){section.append(make('p','',hypothesis.id+' · гипотеза: '+hypothesis.statement));if(hypothesis.experiment)section.append(make('p','','Эксперимент: '+hypothesis.experiment));if(hypothesis.prediction)section.append(make('p','foot','Ожидаемое наблюдение: '+hypothesis.prediction));}
   jsonDetails(section,'Полный JSON рефлексии',reflection);
  }else empty(parent,'Отдельная рефлексия ещё не записана.');
 }
 function render(){
  if(!overview)return;
  const key=JSON.stringify([job,tab,overview]);if(fingerprint===key)return;fingerprint=key;
  identity.textContent='Задание: '+job+' · версия пайплайна: '+(overview.version||'—')+' · раунд '+(overview.scope?.round??overview.status?.scope?.round??'—');
  if(overview.enabled===false){stage.textContent='Для этого задания новый пайплайн ещё не активирован.';content.hidden=false;content.replaceChildren(make('p','muted','История старого задания остаётся в «Памяти по раундам».'));memory.hidden=true;return;}
  const current=overview.status||{};stage.textContent=stages[current.stage]||current.stage||'Этап ещё не записан';
  if(overview.operation){const operation=overview.operation;const label={running:'выполняется',processing_result:'запрос завершён, сохраняю результат',completed:'завершено',retrying:operation.retry_category==='rate_limit'?'ожидаю доступную квоту провайдера':'восстанавливаю соединение',failed:'ошибка',correcting_format:'исправляю формат'}[operation.status]||'последний этап';
   stage.textContent+=' · '+label+': '+operation.role+' / '+operation.contract+' · попытка '+operation.attempt;
   if(operation.status==='retrying')stage.textContent+=' · '+({provider_unavailable:'API временно недоступен',provider_generation_error:'API завершил генерацию с ошибкой',stream_interrupted:'обрыв потока',rate_limit:'повторные попытки включены'}[operation.retry_category]||'восстановление')+' · интервал '+operation.retry_delay_seconds+' с';}
  if(overview.service_operation?.active&&overview.service_operation.stage==='memory_sync'&&overview.job_status==='RUNNING'){
   const service=overview.service_operation;
   stage.textContent='Синхронизирую память'+(Number.isInteger(service.total)?' · '+service.completed+' / '+service.total:'');
  }
  if(overview.decomposition?.semantic_review==='pending')stage.textContent+=' · разбор ТЗ ещё не одобрен';
  if(overview.decomposition?.semantic_review==='deferred_to_work')stage.textContent+=' · подготовка передана в работу, итоговое ревью впереди';
  if(current.ready===true)stage.textContent+=' · готовность подтверждена';
  memory.hidden=tab!=='memory';content.hidden=tab==='memory';
  const savedScroll=dialog.scrollTop,fragment=document.createDocumentFragment();
  if(tab==='overview')renderPackage(fragment);
  else if(tab==='tasks')renderTasks(fragment);
  else if(tab==='review')renderReview(fragment);
  if(tab==='overview'||tab==='review'){
   if(asArray(overview.supplemental_evidence).length){const section=card(fragment,'Дополнительные материалы','Фото и видео не блокируют разработку или приёмку.');
    for(const condition of overview.supplemental_evidence)section.append(make('p','foot',condition.id+' · '+condition.expected));}
   for(const [role,report] of Object.entries(overview.agent_reports||{})){
    if(tab==='review'&&role!=='reviewer')continue;
    const section=card(fragment,'Отчёт: '+({planner:'планировщик',coder:'кодер',reviewer:'ревьюер'}[role]||role),report.phase||'');
    const details=make('details');details.append(make('summary','','Полный отчёт · Markdown'),make('pre','pipeline-full-record',report.notes_md||''));section.append(details);
   }
  }
  if(tab!=='memory'){
   const reasons=asArray(current.readiness_reasons);if(reasons.length){const section=card(fragment,'Что ещё не подтверждено');list(section,reasons);}
   content.replaceChildren(fragment);dialog.scrollTop=savedScroll;
  }
  const selected=taskSelect.value;taskSelect.replaceChildren();const any=make('option','','Все задачи');any.value='';taskSelect.append(any);
  for(const task of asArray(overview.graph?.tasks)){const option=make('option','',task.id+' · '+task.title);option.value=task.id;taskSelect.append(option);}
  taskSelect.value=selected;
 }
 async function loadOverview(){
  if(!dialog.open||!job||overviewPending)return;
  const token={job,generation};overviewPending=token;notice.textContent='Обновляю состояние…';
  try{const value=await request(base(token.job));if(token.job!==job||token.generation!==generation||!dialog.open)return;
   overview=value;render();notice.textContent='Обновлено: '+new Date().toLocaleTimeString()+'. Просмотр не запускает агентов.';
   if(tab==='memory'&&!memoryLoaded&&value.enabled!==false)await loadMemory();
  }catch(error){if(token.job===job&&token.generation===generation)notice.textContent='Не удалось обновить: '+error.message+'. Предыдущие данные оставлены на экране.';}
  finally{if(overviewPending===token)overviewPending=null;}
 }
 async function openRecord(id,holder,control){
  if(recordRequests.has(id))return;const token={job,generation};recordRequests.add(id);control.disabled=true;
  try{const value=await request(base(token.job)+'/memory/'+encodeURIComponent(id));if(token.job!==job||token.generation!==generation||!dialog.open)return;
   holder.append(make('pre','pipeline-full-record',JSON.stringify(value,null,2)));control.remove();
  }catch(error){if(token.job===job&&token.generation===generation){control.disabled=false;control.textContent='Повторить: '+error.message;}}
  finally{recordRequests.delete(id);}
 }
 async function loadMemory(){
  if(!dialog.open||!job||pagePending)return;const token={job,generation,cursor,filterTask,filterQuery};pagePending=token;
  const params=[];if(filterTask)params.push('task_id='+encodeURIComponent(filterTask));if(filterQuery)params.push('query='+encodeURIComponent(filterQuery));if(cursor)params.push('cursor='+encodeURIComponent(cursor));
  prev.disabled=next.disabled=true;notice.textContent='Загружаю страницу памяти…';
  try{const value=await request(base(token.job)+'/memory'+(params.length?'?'+params.join('&'):''));
   if(token.job!==job||token.generation!==generation||token.cursor!==cursor||token.filterTask!==filterTask||token.filterQuery!==filterQuery||!dialog.open)return;
   records.replaceChildren();
   for(const item of asArray(value.records)){const section=card(records,item.summary||'Запись истории');section.append(make('p','foot',item.record_id));
    const control=make('button','','Открыть полный JSON');control.type='button';section.append(control);control.onclick=()=>openRecord(item.record_id,section,control);
   }
   if(!asArray(value.records).length)empty(records,'Подходящих записей пока нет.');
   nextCursor=value.next_cursor||null;memoryLoaded=true;prev.disabled=!cursorHistory.length;next.disabled=!nextCursor;
   pageLabel.textContent='Страница '+(cursorHistory.length+1)+' · далее записей: '+(value.omitted_records??'—');
   notice.textContent='Полные данные хранятся в архиве. Здесь загружена одна страница; JSON открывается по запросу.';
  }catch(error){if(token.job===job&&token.generation===generation){notice.textContent='Память недоступна: '+error.message;prev.disabled=!cursorHistory.length;next.disabled=!nextCursor;}}
  finally{if(pagePending===token)pagePending=null;}
 }
 function resetMemory(){cursor=null;nextCursor=null;cursorHistory=[];memoryLoaded=false;pagePending=null;records.replaceChildren();prev.disabled=next.disabled=true;pageLabel.textContent='';}
 function switchJob(jid){
  if(job===jid)return;job=jid||null;generation++;overview=null;overviewPending=null;pagePending=null;fingerprint='';filterTask='';filterQuery='';taskSelect.value='';search.value='';recordRequests.clear();resetMemory();
  content.replaceChildren();content.hidden=false;memory.hidden=true;stage.textContent=job?'Открой пайплайн для просмотра.':'Выбери проект.';identity.textContent=job?'Задание: '+job:'';if(button)button.disabled=!job;
  if(dialog.open&&job)loadOverview();
 }
 async function open(jid){switchJob(jid||job||(typeof active!=='undefined'?active:null));if(!job)return;opener=document.activeElement||button;if(!dialog.open)dialog.showModal();await loadOverview();}
 for(const [name,control] of tabs){control.onclick=()=>{tab=name;fingerprint='';for(const [id,item] of tabs)item.setAttribute('aria-pressed',id===name?'true':'false');render();if(name==='memory'&&overview?.enabled!==false&&!memoryLoaded)loadMemory();};}
 find.onclick=()=>{generation++;overviewPending=null;filterTask=taskSelect.value||'';filterQuery=search.value||'';resetMemory();loadMemory();};
 search.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();find.onclick();}};
 prev.onclick=()=>{if(!cursorHistory.length||pagePending)return;cursor=cursorHistory.pop();memoryLoaded=false;loadMemory();};
 next.onclick=()=>{if(!nextCursor||pagePending)return;cursorHistory.push(cursor);cursor=nextCursor;memoryLoaded=false;loadMemory();};
 refresh.onclick=()=>{if(tab==='memory'){memoryLoaded=false;loadMemory();}else loadOverview();};
 close.onclick=()=>{dialog.close();if(opener?.focus)opener.focus();};
 dialog.addEventListener('close',()=>{generation++;overviewPending=null;pagePending=null;recordRequests.clear();});
 if(button)button.onclick=()=>open(typeof active!=='undefined'?active:job);
 window.LabPipeline={open,switchJob,refresh:loadOverview};
 window.addEventListener('lab-job',event=>{if(event.detail?.id)switchJob(event.detail.id);});
 setInterval(()=>switchJob(typeof active!=='undefined'?active:null),500);
 setInterval(()=>{if(dialog.open&&tab!=='memory')loadOverview();},5000);
 switchJob(typeof active!=='undefined'?active:null);
})();
