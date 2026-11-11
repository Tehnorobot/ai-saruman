/* Presentation only. Navigation never starts, pauses, or changes a job. */
(() => {
 'use strict';
 const get=id=>document.getElementById(id),make=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
 const pages=new Set(['overview','acceptance','media','agents','usage','settings']);
 const labels={RUNNING:'В работе',QUEUED:'В очереди',USER_PAUSED:'На паузе',QUOTA_PAUSED:'Ожидание квоты',FAILED:'Ошибка',STOPPED:'Остановлено',FINISHED:'Лимит раундов достигнут'};
 const projectNodes=new Map();let currentJob=null,page='overview',summaryPending=null,summaryAt=0,summaryGeneration=0,projectsMounted=false;
 const acceptance=get('acceptanceDialog'),create=get('newProjectDialog');
 const savedProject=new URL(location.href).searchParams.get('project');
 if(/^[0-9a-f]{14}$/.test(savedProject||''))active=savedProject;
 // Existing modules retain ownership of data, controls, and mounted players.
 for(const [id,slot] of [['agentModelsPanel','modelsSlot'],['memoryPanel','memorySlot'],['plannerPanel','plannerSlot'],['usagePanel','usageSlot'],['roundTaskPanel','overviewTaskSlot']]){
  if(get(id))get(slot).append(get(id));
 }
 get('view-acceptance').append(acceptance);acceptance.classList.add('inline-acceptance');
 const caution=get('acceptanceCaution'),explanation=make('details','acceptance-explanation'),caption=make('summary','','Как читать результаты');
 caution.before(explanation);explanation.append(caption,caution,get('acceptanceCoverage'),get('acceptancePlanNotes'));
 get('acceptanceClose').textContent='К обзору';
 get('acceptanceOpen').textContent='Открыть приёмку →';
 get('acceptanceOpen').removeAttribute('aria-haspopup');get('acceptanceOpen').setAttribute('aria-controls','view-acceptance');
 const modelPanel=get('agentModelsPanel');
 if(modelPanel){const help=make('details'),title=make('summary','','Как работают роли и наследование');help.append(title);for(const node of [...modelPanel.children].filter(n=>n.tagName==='P'&&!n.id))help.append(node);modelPanel.append(help)}
 const memoryPanel=get('memoryPanel');if(memoryPanel)memoryPanel.querySelector('h2').textContent='Память по раундам';
 const usageButton=get('usagePanel')?.querySelector('button');if(usageButton)usageButton.textContent='История запросов →';

 function showPage(name,{focus=false,hash=true}={}){
  name=pages.has(name)?name:'overview';
  if(page==='acceptance'&&name!=='acceptance'&&acceptance.open)acceptance.close();
  // Pause hidden evidence without destroying its player or playback position.
  if(page!==name)for(const video of get('view-'+page).querySelectorAll('video'))video.pause();
  page=name;
  for(const view of document.querySelectorAll('.lab-view'))view.hidden=view.dataset.page!==page;
  for(const button of document.querySelectorAll('[data-view]')){if(button.dataset.view===page)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current')}
  if(hash&&location.hash!=='#'+page)history.pushState(null,'','#'+page);
  if(page==='acceptance'){
   get('acceptanceEmpty').hidden=!!active;
   if(active){acceptanceSwitchJob(active);if(!acceptance.open)acceptance.show();refreshAcceptance()}
  }
  if(page==='overview')loadSummary();
  if(focus)get('workspace').focus({preventScroll:true});
 }
 window.labOpenAcceptance=()=>showPage('acceptance',{focus:true});
 document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>showPage(button.dataset.view,{focus:true}));
 document.querySelectorAll('[data-go]').forEach(button=>button.onclick=()=>showPage(button.dataset.go,{focus:true}));
 function restoreLocation(){
  const jid=new URL(location.href).searchParams.get('project');
  if(/^[0-9a-f]{14}$/.test(jid||'')&&jid!==active){active=jid;activitySwitchJob(jid);acceptanceSwitchJob(jid);refresh().catch(connectionError)}
  showPage(location.hash.slice(1),{hash:false});
 }
 window.addEventListener('popstate',restoreLocation);
 window.addEventListener('hashchange',restoreLocation);
 acceptance.addEventListener('close',()=>{if(page==='acceptance'&&!acceptance.open)showPage('overview')});
 get('recordingSettingsOpen').onclick=()=>{
  showPage('settings');
  get('recordingSettings').scrollIntoView({block:'start'});
  get('recordingSettings').focus({preventScroll:true});
  window.dispatchEvent(new CustomEvent('lab-recording-settings-open'));
 };
 get('newProjectOpen').onclick=()=>{get('newProjectOpen').focus();create.showModal()};
 get('newProjectClose').onclick=()=>{create.close();get('newProjectOpen').focus()};
 create.addEventListener('close',()=>get('newProjectOpen').focus());
 window.addEventListener('lab-created',()=>{create.close();showPage('overview')});
 // Each module owns its opener; native Escape returns to the focused trigger.

 window.labRenderProjects=jobs=>{
  if(!projectsMounted){get('jobs').replaceChildren();projectsMounted=true}
  const ids=new Set(jobs.map(j=>j.id));
  for(const [id,node] of projectNodes)if(!ids.has(id)){node.remove();projectNodes.delete(id)}
  for(const job of jobs){
   let node=projectNodes.get(job.id);
   if(!node){node=make('button','item');node.type='button';node.dataset.job=job.id;node.append(make('span','project-name'),make('small','project-meta'));projectNodes.set(job.id,node);get('jobs').append(node)}
   node.firstElementChild.textContent=job.title;
   node.lastElementChild.textContent=(labels[job.status]||job.status)+' · '+displayRound(job)+'/'+roundLimitText(job);
   node.classList.toggle('selected',job.id===active);
   node.setAttribute('aria-pressed',String(job.id===active));
   node.onclick=()=>{if(active===job.id)return;const url=new URL(location.href);url.searchParams.set('project',job.id);history.pushState(null,'',url);active=job.id;activitySwitchJob(active);acceptanceSwitchJob(active);refresh().catch(connectionError)};
  }
  if(!jobs.length){if(!get('projectsEmpty')){const empty=make('p','muted','Проектов пока нет. Нажми «Новый проект».');empty.id='projectsEmpty';get('jobs').append(empty)}}else get('projectsEmpty')?.remove();
  get('connectionState').textContent='Подключено · '+new Date().toLocaleTimeString('ru-RU');
 };
 function connectionError(){get('connectionState').textContent='Нет обновления · повтор через 5 с'}
 window.addEventListener('lab-connection-error',connectionError);
 function resetSummary(){get('readinessTitle').textContent='Загрузка приёмки…';get('readinessDetail').textContent='Получаю результаты независимых проверок.';get('readinessProgress').value=0}
 function renderSummary(data){
  if(!data?.summary){get('readinessTitle').textContent='Нет результатов приёмки';get('readinessDetail').textContent='Открой приёмку для просмотра требований.';return}
  const s=data.summary,count=v=>Number.isFinite(Number(v))?Math.max(0,Math.floor(Number(v))):0,total=count(s.total),passed=Math.min(total,count(s.test_passed));
  get('readinessTitle').textContent=total?passed+' из '+total+' условий подтверждено':'Условия ещё не выделены';
  get('readinessProgress').max=total||1;get('readinessProgress').value=Math.min(passed,total);
  get('readinessProgress').setAttribute('aria-valuetext',passed+' из '+total+' условий ТЗ');
  get('readinessDetail').textContent='Частично: '+count(s.in_progress)+' · ошибок: '+count(s.failed)+' · перепроверить: '+count(s.stale)+' · не проверено: '+count(s.not_tested)+(data.verification_in_progress?'. Идёт новая проверка.':'. Результаты относятся к проверенной версии.');
  const tasks=data.task_summary;if(tasks?.total)get('readinessDetail').textContent+=' Задач подтверждено: '+(tasks.test_passed||0)+'/'+tasks.total+'.';
  if(data.history_summary&&Number.isInteger(data.history_summary.scenarios_ever_passed))get('readinessDetail').textContent+=' Сценариев проходили: '+data.history_summary.scenarios_ever_passed+'; для текущей версии: '+(data.history_summary.scenarios_current_passed||0)+'.';
 }
 async function loadSummary(){
  if(!active||page!=='overview'||summaryPending||Date.now()-summaryAt<30000)return;
  const token={job:active,generation:summaryGeneration};summaryPending=token;
  try{const data=await api('/api/jobs/'+token.job+'/acceptance-summary');if(active!==token.job||summaryGeneration!==token.generation)return;renderSummary(data);summaryAt=Date.now()}
  catch{if(active===token.job&&summaryGeneration===token.generation)get('readinessDetail').textContent='Не удалось обновить приёмку. Показаны последние данные.'}
  finally{if(summaryPending===token)summaryPending=null}
 }
 window.addEventListener('lab-acceptance',event=>{if(event.detail.job_id===active){renderSummary(event.detail.data);summaryAt=Date.now()}});
 window.addEventListener('lab-job',event=>{
  const job=event.detail;
  if(currentJob!==job.id){
   currentJob=job.id;summaryGeneration++;summaryPending=null;summaryAt=0;resetSummary();
   const url=new URL(location.href);url.searchParams.set('project',job.id);history.replaceState(null,'',url);
   get('usageChart').replaceChildren(make('p','muted','Загрузка расхода выбранного проекта…'));
   if(window.agentModelsSwitchJob)window.agentModelsSwitchJob(job.id);
   if(window.memorySwitchJob)window.memorySwitchJob(job.id);
   if(window.plannerSwitchJob)window.plannerSwitchJob(job.id);
   if(typeof refreshUsage==='function')refreshUsage();
   for(const dialog of document.querySelectorAll('dialog[open]:not(#acceptanceDialog):not(#newProjectDialog)'))dialog.close();
   if(page==='acceptance'){acceptanceSwitchJob(job.id);if(!acceptance.open)acceptance.show();refreshAcceptance()}
  }
  for(const [id,node] of projectNodes){node.classList.toggle('selected',id===job.id);node.setAttribute('aria-pressed',String(id===job.id))}
  const badge=get('projectBadge');badge.textContent=labels[job.status]||job.status;badge.className='status '+job.status;
  get('jobAlert').hidden=!job.last_error;
  get('jobAlertText').textContent=job.last_error?'Последняя ошибка: '+String(job.last_error).split('\n')[0].slice(0,220):'';
  get('roundMetric').textContent=displayRound(job)+' / '+roundLimitText(job);
  get('roundDetail').textContent=job.status==='FINISHED'?'Лимит достигнут. Готовность смотри в приёмке.':'Номер раунда не показывает готовность мода.';
  const phase=String(job.phase||'');
  let stage=({planning:'plan',decomposing:'plan',reflecting:'plan',pipeline_waiting:'plan',planner_error:'plan',
   codex_development:'code',coder_error:'code',reviewing:'test',reviewer_error:'test',verifying:'test',
   verification_error:'test',acceptance_review_required:'test',spec_clarification_required:'plan',
   acceptance_verified:'result',max_rounds_reached:'result'})[phase];
  if(!stage&&job.status==='FINISHED')stage='result';
  const paused=['USER_PAUSED','QUOTA_PAUSED','STOPPED','FAILED'].includes(job.status);
  for(const node of get('workflow').querySelectorAll('[data-stage]')){
   const selected=node.dataset.stage===stage;
   node.classList.toggle('current',selected);node.classList.toggle('workflow-paused',selected&&paused);
   if(selected){node.setAttribute('aria-current','step');node.setAttribute('title',paused?'Остановлено на этом этапе':'Текущий этап');}
   else{node.removeAttribute('aria-current');node.removeAttribute('title');}
  }
  loadSummary();
 });
 window.addEventListener('lab-usage',event=>{
  if(event.detail.job_id!==active)return;
  const rows=event.detail.report.roles||[],chart=get('usageChart'),values=rows.map(r=>({...r,count:['input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens'].reduce((n,k)=>n+Number(r[k]||0),0)})),total=values.reduce((n,r)=>n+r.count,0);
  chart.replaceChildren();
  if(!values.length){chart.append(make('p','muted','Учтённых запросов пока нет.'));return}
  for(const row of values){const item=make('div','usage-bar'),label=make('div','usage-bar-label',({planner:'Планировщик',coder:'Кодер',reviewer:'Ревьюер'})[row.role]||'Без роли'),track=make('div','usage-bar-track'),fill=make('div','usage-bar-fill');fill.style.width=(total?row.count/total*100:0)+'%';track.append(fill);item.append(label,track,make('span','',row.count.toLocaleString('ru-RU')));chart.append(item)}
 });
 showPage(location.hash.slice(1),{hash:false});
 setInterval(loadSummary,5000);
 // Display initial state even if the first response preceded this script.
 if(typeof active!=='undefined'&&active)api('/api/jobs/'+active).then(job=>{if(job.id===active)window.dispatchEvent(new CustomEvent('lab-job',{detail:job}))}).catch(connectionError);
})();
