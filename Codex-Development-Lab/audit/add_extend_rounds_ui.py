from pathlib import Path
root=Path(__file__).resolve().parents[1]
path=root/'app/index.html'
page=path.read_text(encoding='utf-8')
anchor='<div class="row"><details style="width:100%">'
assert page.count(anchor)==1
page=page.replace(anchor,'''<form id="extendRounds" hidden><div class="row"><label class="muted" for="additionalRounds">Добавить раундов: <input id="additionalRounds" name="additional_rounds" type="number" min="1" max="15" value="5" required></label><button id="extendButton" class="primary" type="submit">Добавить раунды и продолжить</button></div><p id="extendNote" class="foot" role="status"></p></form><p id="extendLimit" class="foot" hidden>Общий лимит 20 раундов достигнут. Дополнительные раунды для этого задания недоступны.</p>'''+anchor,1)
anchor="async function action(act)"
assert page.count(anchor)==1
page=page.replace(anchor,'''let extendingJob=null,extensionFor=null;
function renderRoundExtension(job){
 const finished=job.status==='FINISHED'&&job.phase==='max_rounds_reached';
 const remaining=20-job.max_rounds;
 q('extendRounds').hidden=!(finished&&remaining>0);
 q('extendLimit').hidden=!(finished&&remaining<=0);
 if(!finished||remaining<=0)return;
 const input=q('additionalRounds');input.max=String(remaining);
 if(extensionFor!==job.id){input.value=String(Math.min(5,remaining));extensionFor=job.id}
 q('extendButton').disabled=extendingJob===job.id;input.disabled=extendingJob===job.id;
 if(extendingJob!==job.id)q('extendNote').textContent='Продолжение с раунда '+(job.round+1)+'. Код, ТЗ и история сохранятся. Общий лимит — до 20 раундов.';
}
q('extendRounds').onsubmit=async event=>{
 event.preventDefault();const jid=active;if(!jid||extendingJob)return;
 const body=new FormData(q('extendRounds'));extendingJob=jid;
 q('extendButton').disabled=true;q('additionalRounds').disabled=true;
 q('extendNote').textContent='Добавляю раунды…';
 try{await api('/api/jobs/'+jid+'/extend-rounds',{method:'POST',body});await refresh()}
 catch(error){if(active===jid)q('extendNote').textContent='Ошибка: '+error.message}
 finally{extendingJob=null;if(active===jid){q('extendButton').disabled=false;q('additionalRounds').disabled=false}}
};
'''+anchor,1)
anchor="q('stop').disabled=['STOPPED','FAILED','FINISHED'].includes(j.status);"
assert page.count(anchor)==1
page=page.replace(anchor,anchor+'renderRoundExtension(j);',1)
path.write_text(page,encoding='utf-8')
