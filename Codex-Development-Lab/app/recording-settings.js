/* One global operator preference. Project polling never resets unsaved edits. */
(() => {
 'use strict';
 const get=id=>document.getElementById(id),form=get('recordingSettingsForm');
 if(!form)return;
 const input=get('recordingMaxSeconds'),save=get('recordingSave'),retry=get('recordingRetry'),
       status=get('recordingSettingsStatus'),current=get('recordingCurrentLimit');
 let saved=null,loading=false,saving=false;
 const value=()=>{const number=Number(input.value);return input.value.trim()&&Number.isSafeInteger(number)&&number>0?number:null};
 const dirty=()=>saved!==null&&value()!==saved;
 const describe=seconds=>seconds.toLocaleString('ru-RU')+' с'+(seconds%60===0?' ('+(seconds/60).toLocaleString('ru-RU')+' мин)':'');
 function controls(){
  input.disabled=loading||saving||saved===null;
  save.disabled=loading||saving||saved===null||value()===null||!dirty();
  save.textContent=saving?'Сохраняю…':'Сохранить';
  form.setAttribute('aria-busy',String(loading||saving));
 }
 function validateResponse(data){
  if(!data||!Number.isSafeInteger(data.max_seconds)||data.max_seconds<1)throw new Error('Сервер вернул неверный предел записи.');
  return data.max_seconds;
 }
 async function responseData(response){
  let data;
  try{data=await response.json()}catch{throw new Error('Не удалось прочитать ответ сервера.')}
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Ошибка запроса ('+response.status+').');
  return data;
 }
 async function load(){
  if(loading||saving||dirty())return;
  loading=true;retry.hidden=true;status.textContent='Загрузка настройки…';controls();
  try{
   const data=await responseData(await fetch('/api/recording-settings',{cache:'no-store'}));
   saved=validateResponse(data);input.value=String(saved);input.removeAttribute('aria-invalid');
   current.textContent='Текущий предел: '+describe(saved)+'.';
   status.textContent='Настройка сохранена на сервере.';
  }catch(error){status.textContent='Не удалось загрузить настройку. '+error.message;retry.hidden=false}
  finally{loading=false;controls()}
 }
 input.addEventListener('input',()=>{
  const valid=value()!==null;
  if(valid)input.removeAttribute('aria-invalid');else input.setAttribute('aria-invalid','true');
  status.textContent=!valid?'Введите целое число секунд больше нуля.':dirty()?'Изменено. Нажми «Сохранить».':'Настройка сохранена на сервере.';
  controls();
 });
 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(loading||saving||saved===null)return;
  const seconds=value();
  if(seconds===null){input.setAttribute('aria-invalid','true');status.textContent='Введите целое число секунд больше нуля.';input.focus();return}
  if(!dirty())return;
  saving=true;retry.hidden=true;status.textContent='Сохраняю настройку…';controls();
  try{
   const data=await responseData(await fetch('/api/recording-settings',{
    method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_seconds:seconds})
   }));
   saved=validateResponse(data);input.value=String(saved);input.removeAttribute('aria-invalid');
   current.textContent='Текущий предел: '+describe(saved)+'.';
   status.textContent='Сохранено. Новый предел действует для следующих запусков проверки.';
  }catch(error){status.textContent='Не удалось сохранить. '+error.message}
  finally{saving=false;controls()}
 });
 retry.addEventListener('click',load);
 window.addEventListener('lab-recording-settings-open',load);
 load();
})();
