/* Read-only planning and user-check viewer. Existing players stay mounted. */
(() => {
 'use strict';
 const make=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=String(text);return el};
 const panel=make('section','panel planner-panel');panel.id='plannerPanel';
 const heading=make('div','row');heading.append(make('h2','','Планировщик и ручные проверки'));
 const refresh=make('button','','Обновить');refresh.type='button';refresh.id='refreshPlanner';heading.append(refresh);
 const notice=make('p','muted');notice.id='plannerNotice';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
 const content=make('div');content.id='plannerContent';
 panel.append(heading,notice,content);
 const anchor=document.getElementById('agentHistory');if(anchor)anchor.before(panel);else document.body.append(panel);
 let job=null,generation=0,pending=null,fingerprint='';
 const actions={repair_acceptance_plan:'Исправить план приёмки',repair_scenario:'Исправить сценарий',repair_verification_failure:'Исправить ошибку проверки',implement_and_map_requirement:'Реализовать требование и добавить проверку',implement_or_verify_scenario:'Реализовать или проверить сценарий',verify_task:'Проверить задачу',deep_diagnosis:'Изучить причину повторного сбоя',implement_requirement:'Реализовать требование',manual_review:'Подготовить ручную проверку'};
 function list(parent,values,ordered=false){
  if(!Array.isArray(values)||!values.length)return;
  const el=make(ordered?'ol':'ul');for(const value of values.slice(0,30))el.append(make('li','',value));parent.append(el);
 }
 actions.diagnose_failure='Изучить причину повторного сбоя';
 actions.await_user_review='Подготовить проверку пользователем';
 function render(value){
  const key=JSON.stringify(value);if(key===fingerprint)return;fingerprint=key;
  const expanded=new Set([...content.querySelectorAll('details[open]')].map(el=>el.dataset.reviewId));
  const fragment=document.createDocumentFragment(),planner=value.planner||{},data=planner.data;
  if(value.runtime_error)fragment.append(make('p','media-warning','Последний запуск планировщика остановлен: '+value.runtime_error+' Автоматической смены модели нет.'));
  if(data){
   const card=make('section','planner-card');
   card.append(make('h3','','Раунд '+(data.round??'—')+' · '+(data.mode==='deep_diagnosis'?'Углублённая диагностика':'План следующего действия')));
   if(data.status)card.append(make('p','foot','Состояние плана: '+data.status));
   if(data.summary)card.append(make('p','',data.summary));
   const model=data.model_selection;
   if(model)card.append(make('p','','Модель планировщика: '+model.provider+' / '+(model.model||'по умолчанию')));
   if(data.plan_reused){
    card.append(make('p','media-warning','Использован сохранённый план. Нового обращения к модели не было.'+(model?'':' Модель старого плана не записана.')));
    if(data.requested_model)card.append(make('p','foot','Текущий выбор для нового запуска: '+data.requested_model.provider+' / '+(data.requested_model.model||'по умолчанию')));
   }
   if(data.failure_detail)card.append(make('pre','media-warning','Ошибка обращения к модели: '+data.failure_detail));
   if(data.automatic_switch===false)card.append(make('p','foot','Автоматической смены на другую модель нет.'));
   const action=data.action||{};
   card.append(make('p','planner-action',actions[action.kind]||action.kind||'Действие ещё не назначено'));
   if(action.target)card.append(make('p','','Цель: '+action.target));
   if(action.reason)card.append(make('p','','Почему выбрано: '+action.reason));
   if(action.expected_progress)card.append(make('p','planner-expected-progress',
    'Ожидаемый результат: '+action.expected_progress.observable_result+' · '+action.expected_progress.kind));
   if(action.requirement_ids?.length)card.append(make('p','foot','Пункты ТЗ: '+action.requirement_ids.join(', ')));
   if(action.diagnostic)card.append(make('pre','',action.diagnostic));
   const additional=Array.isArray(data.additional_actions)?data.additional_actions.slice(0,3):[];
   if(additional.length){
    const bundle=make('section','planner-bundle');
    bundle.append(make('h4','','Пакет раунда: '+(additional.length+1)+' задачи'),
      make('p','',data.bundle_reason||'Связанные действия текущей задачи.'));
    const members=make('ol');
    for(const member of [action,...additional]){
     const row=make('li');row.append(make('strong','',(actions[member.kind]||member.kind)+' · '+member.target));
     if(member.reason)row.append(make('p','',member.reason));
     if(member.expected_progress)row.append(make('p','', 'Результат: '+member.expected_progress.observable_result));
     if(member.depends_on?.length)row.append(make('p','foot','После: '+member.depends_on.join(', ')));
     if(member.requirement_ids?.length)row.append(make('p','foot','Пункты ТЗ: '+member.requirement_ids.join(', ')));
     members.append(row);
    }
    bundle.append(members,make('p','foot','Проверок за проход: до '+(data.verification_limit||4)+'. Выполнение каждой задачи подтверждается отдельно.'));
    card.append(bundle);
   }
   const routing=data.scheduling_directive;
   if(routing?.kind==='rotate_requirement')card.append(make('p','',
    'Контроллер назначил независимое требование. Возврат к ошибке: '+routing.deferred_scenario+' · следующий раунд '+routing.return_due_round+'.'));
   if(routing?.kind==='return_to_failure')card.append(make('p','','Обязательный возврат к ошибке: '+routing.required_target+'.'));
   if(routing?.kind==='reflect_failure')card.append(make('p','media-warning',
    'Рефлексия: изучить предыдущие попытки и получить недостающие данные по '+routing.required_target+'. Ошибка остаётся открытой.'));
   if(routing?.kind==='repair_after_reflection')card.append(make('p','',
    'Исправление после диагностики: использовать полученные данные и повторить проверку '+routing.required_target+'.'));
   if(routing?.kind==='implement_functional_goal')card.append(make('p','media-warning',
    'Приоритет — первый рабочий сценарий: '+(routing.source_condition||routing.required_target)+'. '+routing.reason));
   if(action.verification_objective)card.append(make('p','','Результат проверки: '+action.verification_objective));
   if(data.hypotheses?.length){card.append(make('h4','','Гипотезы для проверки'));list(card,data.hypotheses)}
   if(data.next_steps?.length){card.append(make('h4','','Следующие шаги'));list(card,data.next_steps,true)}
   if(planner.source)card.append(make('p','foot','Источник: '+planner.source));
   fragment.append(card);
  }else fragment.append(make('p','muted',planner.state&&planner.state!=='missing'?'План недоступен ('+planner.state+'). Выполнение задач не подтверждено.':'План пока не сохранён. Выполнение задач не подтверждено.'));
  const reviews=Array.isArray(value.manual_reviews)?value.manual_reviews:[];
  fragment.append(make('h3','','Проверить пользователю: '+reviews.length),make('p','muted','Эти пункты требуют вашей проверки. Они не считаются пройденными автоматически.'));
  if(!reviews.length)fragment.append(make('p','muted','Ручные проверки пока не назначены. Это не означает, что мод готов.'));
  for(const item of reviews){
   const review=item.manual_review||{},detail=make('details','planner-manual');detail.dataset.reviewId=String(item.id);detail.open=expanded.has(String(item.id));
   detail.append(make('summary','',item.title||item.id||'Ручная проверка'));
   detail.append(make('p','planner-review-status','Требуется проверка пользователем · '+(item.review_status||'USER_TEST_REQUIRED')));
   if(item.source?.text)detail.append(make('p','',item.source.text));
   if(review.reason)detail.append(make('p','','Причина: '+review.reason));
   if(review.automation_blocker)detail.append(make('p','','Почему нельзя проверить автоматически: '+review.automation_blocker));
   if(review.category)detail.append(make('p','foot','Категория: '+review.category));
   detail.append(make('h4','','Как проверить'));
   if(review.steps?.length)list(detail,review.steps,true);else detail.append(make('p','muted','Инструкция ещё не подготовлена.'));
   if(review.expected_result)detail.append(make('p','','Ожидаемый результат: '+review.expected_result));
   fragment.append(detail);
  }
  content.replaceChildren(fragment);
 }
 async function load(){
  if(!job||pending)return;const token={job,generation};pending=token;
  try{
   const response=await fetch('/api/jobs/'+encodeURIComponent(job)+'/planning');
   if(!response.ok)throw Error('HTTP '+response.status);
   const value=await response.json();if(token.generation!==generation||token.job!==job)return;
   render(value);notice.textContent='Обновлено: '+new Date().toLocaleTimeString()+'. Просмотр не запускает агентов.';
  }catch(error){if(token.generation===generation&&token.job===job)notice.textContent='Не удалось обновить план: '+error.message+'. Данные на экране могут быть устаревшими.'}
  finally{if(pending===token)pending=null}
 }
 function switchJob(jid){
  if(job===jid)return;job=jid;generation++;pending=null;fingerprint='';
  content.replaceChildren();notice.textContent=jid?'Загрузка плана…':'Выбери проект.';refresh.disabled=!jid;load();
 }
 refresh.onclick=()=>load();window.plannerSwitchJob=switchJob;
 setInterval(()=>switchJob(typeof active!=='undefined'?active:null),500);
 setInterval(load,5000);switchJob(typeof active!=='undefined'?active:null);
})();
