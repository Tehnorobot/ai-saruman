from pathlib import Path

p=Path(__file__).resolve().parents[1]/'app/index.html'
s=p.read_text(encoding='utf-8')
start=s.index('async function refresh(){')
end=s.index('\n',start)
s=s[:start]+'''async function refresh(){
 const jobs=await api('/api/jobs');
 if(active&&!jobs.some(job=>job.id===active))active=null;
 if(!active&&jobs.length)active=jobs[0].id;
 if(window.labRenderProjects)window.labRenderProjects(jobs);
 else{q('jobs').replaceChildren();for(const job of jobs){const button=el('button','item',job.title);button.type='button';button.onclick=()=>{active=job.id;activitySwitchJob(active);acceptanceSwitchJob(active);refresh()};q('jobs').append(button)}}
 acceptanceSwitchJob(active);activitySwitchJob(active);if(!active)return;
 const jid=active,j=await api('/api/jobs/'+jid);if(jid!==active||j.id!==jid)return;
 window.dispatchEvent(new CustomEvent('lab-job',{detail:j}));refreshCurrentActivity(j);
 if(settingsFor!==jid){q('updateModel').value=j.model||'';q('updateReasoning').value=j.reasoning_effort||'default';if(window.loadProviderSettings)window.loadProviderSettings(j);settingsFor=jid}
 q('saveSettings').disabled=!['QUEUED','USER_PAUSED','QUOTA_PAUSED'].includes(j.status);
 q('title').textContent=j.title;
 q('status').textContent='Состояние: '+roundStatus(j)+' · '+j.phase+' · цикл '+displayRound(j)+'/'+roundLimitText(j)+'\\n'+modelSelectionSummary(j)+(j.last_error?'\\nОшибка: '+j.last_error:'')+(j.status==='FINISHED'?'\\nЛимит исчерпан. Готовность мода определяется отчётом и покрытием требований ТЗ.':'');
 q('pause').disabled=!['QUEUED','RUNNING','QUOTA_PAUSED'].includes(j.status);
 q('resume').disabled=!['USER_PAUSED','QUOTA_PAUSED','FAILED'].includes(j.status);
 q('stop').disabled=['STOPPED','FAILED','FINISHED'].includes(j.status);
 renderRoundExtension(j);renderRoundLimitSettings(j);
 const results=await Promise.allSettled([
  refreshAgentHistory(jid),refreshEvents(jid),refreshMediaCatalog(jid),
  api('/api/jobs/'+jid+'/verification'),api('/api/jobs/'+jid+'/backups'),api('/api/jobs/'+jid+'/s3')]);
 if(jid!==active)return;
 if(results[3].status==='fulfilled'){const v=results[3].value;q('acceptanceSummary').textContent=acceptanceText(v);q('verification').textContent=JSON.stringify(v,null,2).slice(0,9000)}
 if(results[4].status==='fulfilled'){
  const backups=results[4].value;q('backups').replaceChildren(el('span','','Резервные копии: '));
  for(const bk of backups){const a=el('a','',bk.filename+' ');a.href='/api/jobs/'+jid+'/backups/'+encodeURIComponent(bk.filename);q('backups').append(a)}
  if(!backups.length)q('backups').append(el('span','','пока нет'));
 }
 if(results[5].status==='fulfilled'){
  const objects=results[5].value;q('s3').replaceChildren(el('p','muted','Видео в Yandex S3: '+objects.length));
  for(const object of objects){const button=el('button','',object.key.split('/').pop()+' · открыть');button.onclick=async()=>{try{const response=await api('/api/jobs/'+jid+'/s3-link?key='+encodeURIComponent(object.key));if(active===jid)window.open(response.url,'_blank','noopener')}catch(error){alert(error.message)}};q('s3').append(button)}
 }
 for(const result of results)if(result.status==='rejected'){console.warn(result.reason);window.dispatchEvent(new Event('lab-connection-error'))}
}'''+s[end:]
s=s.replace("setInterval(()=>refresh().catch(e=>console.warn(e)),5000);refresh().catch(e=>q('jobs').textContent=e.message);", "setInterval(()=>refresh().catch(e=>{console.warn(e);window.dispatchEvent(new Event('lab-connection-error'))}),5000);refresh().catch(e=>{q('jobs').textContent=e.message;window.dispatchEvent(new Event('lab-connection-error'))});")
p.write_text(s,encoding='utf-8')
