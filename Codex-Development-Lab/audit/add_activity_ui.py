from pathlib import Path
root=Path(__file__).resolve().parents[1]
path=root/'app/index.html'
page=path.read_text(encoding='utf-8')
anchor='</style></head>'
assert page.count(anchor)==1
page=page.replace(anchor,''' .activity-card{margin-top:16px;padding:15px;border:1px solid #405174;border-radius:10px;background:#111a2b}.activity-card h3{margin:0 0 9px;font-size:14px;color:#a9c8ff}.activity-title{font-size:19px;font-weight:650}.activity-card p{margin:8px 0}.activity-card.is-stale{border-color:#a08246}.activity-card.is-stale .activity-freshness{color:#ffdc99}.activity-meta{font-variant-numeric:tabular-nums}
'''+anchor,1)
anchor='<div id="status" class="muted">Статусы обновляются каждые 5 секунд.</div>'
assert page.count(anchor)==1
page=page.replace(anchor,anchor+'''<div id="activityCard" class="activity-card"><h3>Сейчас выполняется</h3><div id="activityTitle" class="activity-title" role="status" aria-live="polite">Выбери проект</div><p id="activityDetail" class="muted"></p><p id="activityMeta" class="muted activity-meta"></p><div id="activityFreshness" class="muted activity-freshness">Обновляется каждые 5 секунд.</div></div>''',1)
anchor='function roundLimitText(job)'
assert page.count(anchor)==1
page=page.replace(anchor,'''let activityJob=null,currentActivity=null,activityRevision=0,activityPending=null;
function displayRound(job){return job.round+(job.status==='RUNNING'&&job.phase==='codex_development'?1:0)}
function activitySwitchJob(jid){
 if(activityJob===jid)return;activityJob=jid;activityRevision++;activityPending=null;currentActivity=null;
 q('activityCard').classList.remove('is-stale');q('activityTitle').textContent=jid?'Получаю текущий этап…':'Выбери проект';
 q('activityDetail').textContent='';q('activityMeta').textContent='';q('activityFreshness').textContent='Обновляется каждые 5 секунд.';
}
function updateActivityElapsed(){
 if(!currentActivity||currentActivity.job_id!==active)return;
 const limit=currentActivity.max_rounds===0?'∞':currentActivity.max_rounds;
 let text='Раунд '+currentActivity.current_round+'/'+limit;
 const start=Date.parse(currentActivity.started_at||'');
 if(Number.isFinite(start)){
   const elapsed=Math.max(0,Math.floor((Date.now()-start)/1000));
   text+=' · Этап раунда начат '+new Date(start).toLocaleTimeString()+' · '+Math.floor(elapsed/60)+' мин '+String(elapsed%60).padStart(2,'0')+' с';
 }else text+=' · Время начала этапа не передано';
 q('activityMeta').textContent=text;
}
function renderCurrentActivity(data){
 if(!data||data.job_id!==active||data.job_id!==activityJob)return;
 currentActivity=data;q('activityTitle').textContent=data.title||'Этап уточняется';q('activityDetail').textContent=data.detail||'';
 q('activityCard').classList.toggle('is-stale',Boolean(data.stale));
 q('activityFreshness').textContent=data.stale?'Нет свежего подтверждения операции. Это само по себе не означает ошибку.':'По состоянию обработчика и журналу действий · обновление каждые 5 секунд';
 updateActivityElapsed();
}
async function refreshCurrentActivity(job){
 if(!job||job.id!==active)return;activitySwitchJob(job.id);
 if(activityPending===job.id)return;
 const jid=job.id,revision=activityRevision;activityPending=jid;
 try{
   const data=await api('/api/jobs/'+encodeURIComponent(jid)+'/activity');
   if(active!==jid||activityJob!==jid||revision!==activityRevision)return;
   if(!data||data.job_id!==jid)throw Error('Invalid activity response');
   renderCurrentActivity(data);
 }catch(error){
   if(active!==jid||activityJob!==jid||revision!==activityRevision)return;
   q('activityCard').classList.add('is-stale');
   if(!currentActivity)q('activityTitle').textContent='Текущий этап недоступен';
   q('activityFreshness').textContent='Не удалось обновить данные. Показан последний полученный статус; он может быть устаревшим.';
 }finally{if(revision===activityRevision&&activityPending===jid)activityPending=null}
}
setInterval(updateActivityElapsed,1000);
'''+anchor,1)
old="j.round+'/'+roundLimitText(j)"
assert page.count(old)==2
page=page.replace(old,"displayRound(j)+'/'+roundLimitText(j)")
anchor="active=j.id;acceptanceSwitchJob(active);refresh()"
assert anchor in page
page=page.replace(anchor,"active=j.id;activitySwitchJob(active);acceptanceSwitchJob(active);refresh()",1)
anchor="acceptanceSwitchJob(active);if(!active)return;let j=await api('/api/jobs/'+active);"
assert anchor in page
page=page.replace(anchor,"acceptanceSwitchJob(active);activitySwitchJob(active);if(!active)return;let j=await api('/api/jobs/'+active);if(j.id!==active)return;refreshCurrentActivity(j);",1)
path.write_text(page,encoding='utf-8')
