"""Expose user-selected finite budgets and explicit unlimited mode."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
path=root/'app/index.html'
page=path.read_text(encoding='utf-8')
def replace(old,new):
    global page
    assert old in page,old[:100]
    page=page.replace(old,new,1)
replace('value="5" min="1" max="20"','value="5" min="1" step="1"')
replace('<button class="primary" type="submit">Начать разработку</button>',
        '<label class="muted"><input id="newUnlimited" type="checkbox"> Без лимита</label><button class="primary" type="submit">Начать разработку</button>')
replace('От 1 до 20. Один раунд: изменение кода → сборка → проверки. Завершение раунда не означает готовность мода.',
        'Лимит задаёшь ты. «Без лимита» продолжает работу до паузы, остановки или ошибки; ограничения времени отдельных попыток сохраняются. Завершение раунда не означает готовность мода.')
replace('min="1" max="15" value="5"','min="1" step="1" value="5"')
replace('<p id="extendLimit" class="foot" hidden>Общий лимит 20 раундов достигнут. Дополнительные раунды для этого задания недоступны.</p>',
'''<form id="roundLimitSettings"><div class="row"><label class="muted" for="roundLimitValue">Новый общий предел: <input id="roundLimitValue" type="number" min="1" step="1" value="5" required></label><label class="muted"><input id="roundLimitUnlimited" type="checkbox"> Без лимита</label><button id="saveRoundLimit" type="submit">Сохранить лимит</button></div><p id="roundLimitNote" class="foot" role="status">Для изменения работающего задания сначала нажми «Пауза».</p></form>''')
replace("function roundStatus(job)","function roundLimitText(job){return job.max_rounds===0?'∞':job.max_rounds}\nfunction roundStatus(job)")
replace("j.round+'/'+j.max_rounds", "j.round+'/'+roundLimitText(j)")
replace("j.round+'/'+j.max_rounds", "j.round+'/'+roundLimitText(j)")
replace("let f=new FormData(q('form'));", "let f=new FormData(q('form'));if(q('newUnlimited').checked)f.set('max_rounds','0');")
start=page.index('function renderRoundExtension(job){')
end=page.index("q('extendRounds').onsubmit",start)
page=page[:start]+'''function renderRoundExtension(job){
 const finished=job.status==='FINISHED'&&job.phase==='max_rounds_reached'&&job.max_rounds>0;
 q('extendRounds').hidden=!finished;
 if(!finished)return;
 const input=q('additionalRounds');
 if(extensionFor!==job.id){input.value='5';extensionFor=job.id}
 q('extendButton').disabled=extendingJob===job.id;input.disabled=extendingJob===job.id;
 if(extendingJob!==job.id)q('extendNote').textContent='Продолжение с раунда '+(job.round+1)+'. Код, ТЗ и история сохранятся. Количество дополнительных раундов задаёшь ты.';
}
let roundLimitFor=null,savingLimitJob=null;
q('newUnlimited').onchange=()=>{q('rounds').disabled=q('newUnlimited').checked};
q('roundLimitUnlimited').onchange=()=>{q('roundLimitValue').disabled=q('roundLimitUnlimited').checked||q('saveRoundLimit').disabled};
function renderRoundLimitSettings(job){
 const editable=['QUEUED','USER_PAUSED','QUOTA_PAUSED','FAILED'].includes(job.status)||(job.status==='FINISHED'&&job.phase==='max_rounds_reached');
 const key=job.id+'|'+job.max_rounds;
 if(roundLimitFor!==key){
   q('roundLimitUnlimited').checked=job.max_rounds===0;
   q('roundLimitValue').value=String(job.max_rounds>job.round?job.max_rounds:job.round+5);
   roundLimitFor=key;
 }
 const disabled=!editable||savingLimitJob===job.id;
 q('saveRoundLimit').disabled=disabled;q('roundLimitUnlimited').disabled=disabled;
 q('roundLimitValue').min=String(job.round+1);q('roundLimitValue').disabled=disabled||q('roundLimitUnlimited').checked;
 if(savingLimitJob!==job.id)q('roundLimitNote').textContent=editable?'Сохранение не возобновляет приостановленное или завершённое задание. Для запуска нажми «Продолжить».':'Для изменения работающего задания сначала нажми «Пауза».';
}
q('roundLimitSettings').onsubmit=async event=>{
 event.preventDefault();const jid=active;if(!jid||savingLimitJob)return;
 const body=new FormData();body.set('max_rounds',q('roundLimitUnlimited').checked?'0':q('roundLimitValue').value);
 savingLimitJob=jid;q('saveRoundLimit').disabled=true;q('roundLimitUnlimited').disabled=true;q('roundLimitValue').disabled=true;
 q('roundLimitNote').textContent='Сохраняю лимит…';
 try{await api('/api/jobs/'+jid+'/round-limit',{method:'POST',body});savingLimitJob=null;await refresh()}
 catch(error){if(active===jid)q('roundLimitNote').textContent='Ошибка: '+error.message}
 finally{savingLimitJob=null;if(active===jid){q('saveRoundLimit').disabled=false;q('roundLimitUnlimited').disabled=false;q('roundLimitValue').disabled=q('roundLimitUnlimited').checked}}
};
'''+page[end:]
replace('renderRoundExtension(j);','renderRoundExtension(j);renderRoundLimitSettings(j);')
path.write_text(page,encoding='utf-8')
