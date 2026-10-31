/* Read-only memory viewer. Its polling never replaces media or agent history. */
(() => {
 'use strict';
 const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node};
 const panel=make('section','panel');panel.id='memoryPanel';
 const taskPanel=make('section','activity-card');taskPanel.id='roundTaskPanel';taskPanel.setAttribute('aria-label','Задача агента в раунде');
 const activity=document.getElementById('activityCard');if(activity)activity.after(taskPanel);
 panel.append(make('h2','','Память и результаты по раундам'),make('p','muted','Ошибки, успешные проверки, решения и заметки. Видно, какие данные уже сохранены, а каких ещё нет.'));
 const open=make('button','primary','Открыть память');open.id='openMemory';open.disabled=true;panel.append(open);
 const anchor=document.getElementById('agentHistory');if(anchor)anchor.before(panel);else document.body.append(panel);
 const dialog=make('dialog','memory-dialog');dialog.id='memoryDialog';dialog.setAttribute('aria-labelledby','memoryTitle');
 const header=make('header','row'),title=make('h2','','Память проекта');title.id='memoryTitle';
 const close=make('button','','Закрыть');close.type='button';close.onclick=()=>{dialog.close();open.focus()};header.append(title,close);
 const controls=make('div','row'),label=make('label','field muted','Состояние / раунд'),select=make('select');select.id='memoryRound';label.htmlFor=select.id;label.append(select);
 const refresh=make('button','','Обновить');refresh.type='button';controls.append(label,refresh);
 const notice=make('p','memory-notice');notice.id='memoryNotice';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
 const content=make('div');content.id='memoryContent';
 dialog.append(header,controls,notice,content);document.body.append(dialog);
 let job=null,selected='current',archivePage=1,generation=0,loading=false,fingerprint='',optionsKey='',overview=null,taskLoading=null,taskVersion=0;
 const actions={repair_acceptance_plan:'Исправить план приёмки',repair_scenario:'Исправить проваленный сценарий',repair_verification_failure:'Устранить ошибку сборки или проверки',implement_and_map_requirement:'Реализовать требование и добавить его проверку',implement_or_verify_scenario:'Реализовать и проверить сценарий',verify_task:'Проверить текущую задачу'};
 const reasons={repair_acceptance_plan:'План не прошёл валидацию. Сначала нужно восстановить запуск проверок.',repair_scenario:'Независимая проверка обнаружила ошибку в этом сценарии.',repair_verification_failure:'Предыдущая сборка или проверка завершилась ошибкой.',implement_and_map_requirement:'У требования ещё нет исполняемой проверки.',implement_or_verify_scenario:'Сценарию нужна независимая проверка.',verify_task:'Нужно подтвердить выполнение текущей задачи.'};
 function taskContents(parent,data,heading,compact=false){
  parent.append(make('h3','',heading));
  if(!data){parent.append(make('p','muted','Назначение не сохранено. Текущая задача не подставляется вместо истории.'));return}
  const action=data.round_action||{},assignment=data.task_assignment||data.assignment||{},group=assignment.current_task;
  if(data.pipeline_version==='v1'){
   parent.append(make('p','activity-title',group?.title||'Адаптивный пакет задач'));
   parent.append(make('p','muted','Действий: '+(data.round_actions||[]).length+' · выбор по всему графу проекта'));
   if(action.source)parent.append(make('p','',action.source));
   const detail=make('details');detail.append(make('summary','','Действия и ожидаемые результаты'));
   for(const text of action.diagnostics||[])detail.append(make('p','',text));
   parent.append(detail,make('p','foot','Полный граф, ревью и записи: «Пайплайн и решения».'));
   return;
  }
  parent.append(make('p','activity-title',actions[action.kind]||group?.title||'Конкретное действие ещё не записано'));
  if(group?.title)parent.append(make('p','muted','Задача по ТЗ: '+group.title));
  if(action.target)parent.append(make('p','','Цель: '+action.target));
  if(reasons[action.kind])parent.append(make('p','muted','Почему: '+reasons[action.kind]));
  let detail=parent;
  if(compact){detail=make('details');detail.append(make('summary','','План действия и диагностика'));parent.append(detail)}
  if(action.source)detail.append(make('p','',action.source));
  if(action.diagnostic)detail.append(make('pre','',action.diagnostic));
  if(action.diagnostics?.length)detail.append(make('pre','',action.diagnostics.join('\n')));
  if(group?.requirement_ids?.length)detail.append(make('p','foot','Пункты ТЗ: '+group.requirement_ids.join(', ')));
 }
 async function loadTask(){
  if(!job||taskLoading===job)return;const jid=job,version=taskVersion;taskLoading=jid;
  try{
   const value=await request('/api/jobs/'+encodeURIComponent(jid)+'/round-task');
   if(jid!==job||version!==taskVersion)return;
   const data=value.task?.data,fragment=document.createDocumentFragment(),n=data?.round_action_round;
   const expanded=taskPanel.querySelector('details')?.open;
   taskContents(fragment,data,n?'Задача для раунда '+n:'Текущее назначение агента',true);
   fragment.append(make('p','foot',value.status==='USER_PAUSED'?'На паузе. Назначение будет передано агенту после «Продолжить».':'Это назначение контроллера. Результат подтверждается независимой проверкой.'));
   fragment.append(make('p','foot','Задачи прошлых раундов: «Открыть память» → выбрать раунд.'));
   taskPanel.replaceChildren(fragment);
   if(expanded&&taskPanel.querySelector('details'))taskPanel.querySelector('details').open=true;
  }catch(error){if(jid===job&&version===taskVersion){let note=taskPanel.querySelector('.task-load-error');if(!note){note=make('p','task-load-error muted');taskPanel.append(note)}note.textContent='Не удалось обновить назначение: '+error.message}}
  finally{if(taskLoading===jid&&version===taskVersion)taskLoading=null}
 }
 const states={available:'Сохранено',missing:'Ещё не сохранено',invalid:'Повреждённые данные',too_large:'Файл превышает допустимый размер',unavailable:'Файл недоступен'};
 const statuses={OPEN:'Открыта',HISTORICAL:'Историческая · исправление не подтверждено',RESOLVED:'Исправлена',PASS:'PASS',FAIL:'FAIL',ERROR:'ERROR',NOT_TESTED:'Не проверено',TEST_PASSED:'Тест пройден'};
 const stamp=value=>{if(!value)return 'нет отметки времени';const date=new Date(value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString()};
 const card=(parent,title,text)=>{const node=make('section','memory-card');node.append(make('h3','',title));if(text!==undefined)node.append(make('p','',text));parent.append(node);return node};
 const list=(parent,values)=>{if(!Array.isArray(values)||!values.length){parent.append(make('p','muted','Нет записей.'));return}const ul=make('ul');for(const text of values)ul.append(make('li','',text));parent.append(ul)};
 function source(parent,value){if(value)parent.append(make('p','foot',(states[value.state]||value.state)+' · '+value.source+' · '+stamp(value.modified_at)))}
 function errors(parent,rows){
  const section=card(parent,'История ошибок');if(!rows?.length){section.append(make('p','muted','В этом источнике нет записей об ошибках. Это не подтверждает готовность мода.'));return}
  for(const [index,error] of rows.entries()){
   const detail=make('details','memory-error');detail.dataset.memoryKey='error-'+index+'-'+error.label;
   detail.append(make('summary',error.status==='RESOLVED'?'memory-pass':'memory-warn',
    (statuses[error.status]||error.status)+' · '+error.label+' · повторов: '+(error.repeat_count??'—')));
   detail.append(make('p','muted','Первый раунд: '+(error.first_round??'—')+' · последний: '+(error.last_round??'—')+(error.resolved_round?' · исправление подтверждено в раунде '+error.resolved_round:'')));
   detail.append(make('pre','',error.detail||'Причина не записана.'));if(error.resolution)detail.append(make('p','memory-pass',error.resolution));section.append(detail);
  }
 }
 function notes(parent,value,title='Заметки и решения агента'){
  const section=card(parent,title);section.append(make('p','memory-warn','Заявления агента не являются независимым подтверждением.'));
  if(!value){section.append(make('p','muted','Заметки ещё не сохранены.'));return}
  if(value.summary)section.append(make('pre','',value.summary));
  section.append(make('h4','','Следующие шаги'));list(section,value.next_steps);
  section.append(make('h4','','Принятые решения'));list(section,value.decisions);
  section.append(make('h4','','Ошибки и предложенные решения'));
  if(!value.errors_and_solutions?.length)section.append(make('p','muted','Нет записей.'));
  for(const item of value.errors_and_solutions||[]){const block=make('div','memory-proposal');block.append(make('p','','Ошибка: '+item.error),make('p','','Решение: '+item.solution),make('p','muted','Свидетельство агента: '+item.evidence));section.append(block)}
  if(value.source)section.append(make('p','foot',value.source));
 }
 function memory(parent,value){
  const data=value?.data;
  const meta=card(parent,'Заполнение памяти');source(meta,value);
  if(!data){meta.append(make('p','muted','Память ещё не создана или её не удалось прочитать.'));return}
  const rows=data.errors||[],fixed=rows.filter(e=>e.status==='RESOLVED').length;
  const counters=make('div','memory-counts');
  for(const text of ['Обработаны отчёты по раунд '+(data.last_processed_round??'—'),'Открытых ошибок: '+rows.filter(e=>e.status==='OPEN').length,'Исторических: '+rows.filter(e=>e.status==='HISTORICAL').length,'Исправленных: '+fixed,'Сохранённых заметок: '+(data.recent_agent_notes?.length||0)])counters.append(make('span','',text));meta.append(counters);
  meta.append(make('p','foot','Обработанный отчёт не означает выполнение всех требований ТЗ.'));
  if(data.snapshot_phase)meta.append(make('p','muted',(data.snapshot_phase==='before_development'?'Снимок перед работой агента':'Снимок после проверки')+' · '+stamp(data.snapshot_at)));
  if(data.spec_sha256)meta.append(make('p','foot','Версия ТЗ: '+data.spec_sha256));
  errors(parent,rows);
  if(!data.recent_agent_notes?.length)notes(parent,null);
  for(const note of data.recent_agent_notes||[])notes(parent,note.claims,'Заметки агента · раунд '+(note.source_round??'—'));
 }
 function acceptanceCard(parent,value,title='Приёмка по ТЗ'){
  const section=card(parent,title);source(section,value);const counts=value?.data?.summary;
  if(counts&&Object.keys(counts).length)section.append(make('p','','Всего: '+(counts.total??'—')+' · подтверждено: '+(counts.test_passed??0)+' · провалено: '+(counts.failed??0)+' · не проверено: '+(counts.not_tested??'—')));else section.append(make('p','muted','Отчёт приёмки отсутствует.'));
  for(const scenario of value?.data?.scenarios||[]){
   section.append(make('p',scenario.status==='TEST_PASSED'?'memory-pass':'muted',scenario.id+' · '+(statuses[scenario.status]||scenario.status)+(scenario.has_video?' · видео сохранено':'')));
   if(scenario.failure_details){const detail=make('div');window.renderFailureDetails(detail,scenario.failure_details);section.append(detail)}
  }
 }
 function archiveContents(parent,data){
  const index=data.index?.data,kind=data.kind,section=card(parent,kind==='errors'?'Все сохранённые ошибки':'Все сохранённые заметки');
  if(!index){section.append(make('p','muted','Архив ещё не создан. Он заполняется контроллером; чтение этого окна не запускает агентов.'));return}
  const counts=index.counts||{};section.append(make('p','','Ошибок: '+(counts.errors??0)+' · открытых: '+(counts.open_errors??0)+' · заметок: '+(counts.notes??0)));
  section.append(make('p','foot','Архив сохраняется целиком. Здесь загружается одна страница; подробности открываются отдельно. Заметки агента — непроверенные утверждения.'));
  const pages=index[kind==='errors'?'error_pages':'note_pages']||[],rows=data.page?.data?.items||data.page?.data?.entries||[];
  const controls=make('div','row'),prev=make('button','','← Назад'),next=make('button','','Далее →');prev.type=next.type='button';
  prev.disabled=archivePage<=1;next.disabled=archivePage>=pages.length;
  prev.onclick=()=>{archivePage--;fingerprint='';load(true)};next.onclick=()=>{archivePage++;fingerprint='';load(true)};
  controls.append(prev,make('span','','Страница '+archivePage+' / '+Math.max(1,pages.length)),next);section.append(controls);
  if(!rows.length)section.append(make('p','muted',data.page?.state==='missing'?'На этой странице нет записей.':'Не удалось прочитать страницу архива.'));
  for(const row of rows){
   const detail=make('details','memory-error'),path=row.path||row.archive_path;
   detail.dataset.memoryKey='archive-'+path;
   detail.append(make('summary','',kind==='errors'?(row.label||row.signature)+' · '+(row.status||'—')+' · раунды '+(row.first_round??'—')+'–'+(row.last_round??'—'):'Заметка · раунд '+(row.source_round??'—')));
   if(path){
    const button=make('button','','Прочитать запись целиком');button.type='button';detail.append(button);
    button.onclick=async()=>{
     const jid=job,choice=selected;button.disabled=true;
     try{const record=await request('/api/jobs/'+encodeURIComponent(jid)+'/memory/archive/record?path='+encodeURIComponent(path));
      if(jid!==job||choice!==selected||!dialog.open)return;
      detail.append(make('pre','',record.state==='available'?JSON.stringify(record.data,null,2):'Запись недоступна: '+record.state));button.remove();
     }catch(error){button.disabled=false;button.textContent='Повторить: '+error.message}
    };
   }
   section.append(detail);
  }
 }
 function render(data){
  const key=JSON.stringify([selected,data]);if(fingerprint===key)return;fingerprint=key;
  const scroll=dialog.scrollTop,expanded=new Set([...content.querySelectorAll('details[open]')].map(x=>x.dataset.memoryKey));
  const fragment=document.createDocumentFragment();
  if(selected.startsWith('archive-')){
   archiveContents(fragment,data);
  }else if(selected==='current'){
   taskContents(fragment,data.current?.data,'Назначение для раунда '+(data.current?.data?.round_action_round??'—'));
   memory(fragment,data.current);if(data.latest_acceptance)acceptanceCard(fragment,data.latest_acceptance,'Последняя независимая приёмка');card(fragment,'Объём истории',data.retention_note);
  }else{
   const saved=data.memory?.state==='available';
   card(fragment,'Раунд '+data.round,saved?'Сохранённый снимок памяти этого раунда.':'Исторический снимок памяти отсутствует. Ниже — реальные отчёты раунда; текущая память показана отдельно.');
   const task=card(fragment,'Назначение в этом раунде');taskContents(task,data.round_task?.data,'Работа агента');source(task,data.round_task);if(data.round_task?.provenance)task.append(make('p','foot',data.round_task.provenance));
   if(saved)memory(fragment,data.memory);
   const check=card(fragment,'Независимая проверка');source(check,data.verification);
   const result=data.verification?.data;
   if(result){check.append(make('p','','Итог: '+result.status));for(const row of result.checks||[]){const block=make('details','memory-error');block.dataset.memoryKey='check-'+row.label;block.append(make('summary',row.status==='PASS'?'memory-pass':'memory-warn',row.label+' · '+row.status));block.append(make('pre','',row.detail||row.tail||'Подробности не записаны.'));check.append(block)}}
   acceptanceCard(fragment,data.acceptance);
   const agent=card(fragment,'Завершение агента');if(data.development?.data?.kind==='verification_only')agent.append(make('p','','Только независимая проверка сохранённого кода. Модель в этом раунде не вызывалась.'));else{source(agent,data.agent);if(data.agent?.data)agent.append(make('p','','Статус: '+data.agent.data.status+' · '+data.agent.data.selected_model+' (это не вердикт приёмки).'));}
   if(data.handoff?.state==='available')notes(fragment,data.handoff.data,'Рабочие заметки этого раунда');else card(fragment,'Рабочие заметки этого раунда',states[data.handoff?.state]||'Ещё не сохранены.');
   if(data.summary?.state==='available')notes(fragment,{summary:data.summary.text,source:data.summary.source},'Публичный итог агента из журнала');
   if(!saved){card(fragment,'Текущая память о прежних событиях',data.current_memory_for_round?.provenance||'Это не снимок прошлого состояния.');memory(fragment,data.current_memory_for_round)}
   card(fragment,'Как читать результаты',data.retention_note);
  }
  content.replaceChildren(fragment);for(const detail of content.querySelectorAll('details'))if(expanded.has(detail.dataset.memoryKey))detail.open=true;dialog.scrollTop=scroll;
 }
 async function request(path){const response=await fetch(path,{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);return response.json()}
 async function load(force=false){
  if(!dialog.open||!job||loading&&!force)return;
  const jid=job,choice=selected,token=++generation;loading=true;notice.textContent='Обновление…';
  try{
   const data=await request('/api/jobs/'+encodeURIComponent(jid)+'/memory');
   if(token!==generation||jid!==job||choice!==selected||!dialog.open)return;
   overview=data;const options=JSON.stringify(data.rounds||[]);
   if(options!==optionsKey){
    optionsKey=options;select.replaceChildren();const now=make('option','','Текущая память');now.value='current';select.append(now);
    for(const [value,text] of [['archive-errors','Все ошибки'],['archive-notes','Все заметки']]){const option=make('option','',text);option.value=value;select.append(option)}
    for(const row of data.rounds||[]){
     const task=row.action_target||row.task_title;
     const option=make('option','','Раунд '+row.round+' · '+row.verification_status+(task?' · '+task.slice(0,90):' · назначение не сохранено'));
     option.value=String(row.round);select.append(option);
    }
    select.value=choice;
   }
   if(data.truncated)notice.textContent='Список истории ограничен. ';
   const detail=choice==='current'?data:await request('/api/jobs/'+encodeURIComponent(jid)+(choice.startsWith('archive-')?'/memory/archive?kind='+encodeURIComponent(choice.slice(8))+'&page='+archivePage:'/memory/rounds/'+encodeURIComponent(choice)));
   if(token!==generation||jid!==job||choice!==selected||!dialog.open)return;
   render(detail);notice.textContent=(data.truncated?'Список раундов ограничен. ':'')+'Получено: '+new Date().toLocaleTimeString()+'. Обновление каждые 5 секунд; задание не запускается.';
  }catch(error){if(token===generation&&jid===job)notice.textContent='Данные устарели: '+error.message+'. Последний результат сохранён на экране.'}
  finally{if(token===generation)loading=false}
 }
 function switchJob(jid){
  if(jid===job)return;job=jid;generation++;taskVersion++;taskLoading=null;loading=false;selected='current';archivePage=1;fingerprint='';optionsKey='';overview=null;
  select.replaceChildren();content.replaceChildren();taskPanel.replaceChildren(make('h3','','Задача агента'),make('p','muted',jid?'Загрузка назначения…':'Выбери проект.'));
  notice.textContent=jid?'Открой память для просмотра.':'Выбери проект.';open.disabled=!jid;
  if(dialog.open)load(true);loadTask();
 }
 window.memorySwitchJob=switchJob;
 open.onclick=()=>{switchJob(typeof active!=='undefined'?active:null);if(!job)return;open.focus();dialog.showModal();load(true)};
 select.onchange=()=>{selected=select.value;archivePage=1;fingerprint='';content.replaceChildren();load(true)};
 refresh.onclick=()=>load(true);
 dialog.addEventListener('close',()=>{generation++;loading=false});
 setInterval(()=>switchJob(typeof active!=='undefined'?active:null),500);
 setInterval(()=>load(),5000);
 setInterval(()=>loadTask(),5000);
 switchJob(typeof active!=='undefined'?active:null);
})();
