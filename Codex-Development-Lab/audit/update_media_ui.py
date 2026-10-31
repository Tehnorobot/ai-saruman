"""One-time source migration for the local media gallery. No service/API calls."""
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'app/index.html'
text=path.read_text(encoding='utf-8')
assert 'function renderMediaFiles(' not in text
styles='''
.FINISHED{color:#ffdc99}#files{max-height:700px}.media-preview{display:block;width:100%;max-height:420px;object-fit:contain;margin:10px 0;cursor:zoom-in}.media-warning{color:#ffdc99;border-left:3px solid #aa8138;padding:8px 12px;margin:10px 0;font-size:13px}.media-dialog{background:#111927;color:#e9eef9;border:1px solid #405174;border-radius:12px;max-width:96vw;width:fit-content;max-height:95vh;padding:16px}.media-dialog::backdrop{background:#000b}.media-dialog header{margin-bottom:12px}.media-dialog .image-scroll{overflow:auto;max-width:90vw;max-height:78vh}.media-dialog img{display:block;max-width:88vw;max-height:75vh;object-fit:contain}.media-dialog.full-size img{max-width:none;max-height:none}.media-dialog button:focus-visible,.media-preview:focus-visible{outline:3px solid #a9c8ff;outline-offset:3px}
'''
text=text.replace('</style>',styles+'</style>',1)
dialog='''<dialog class="media-dialog" id="mediaDialog" aria-labelledby="mediaTitle"><header><span id="mediaTitle">Изображение</span><button type="button" id="mediaZoom">Масштаб 100%</button><button type="button" id="mediaClose">Закрыть</button></header><div class="image-scroll"><img id="mediaImage" alt="Полный кадр проверки"></div></dialog>
'''
text=text.replace('<script>',dialog+'<script>',1)
functions='''
let renderedMediaKey='',mediaOpener=null;
function roundStatus(job){return job.status==='FINISHED'?'Лимит раундов достигнут':job.status}
function isWorldIcon(file){return /(?:^|\\/)screenshots\\/.*saves.*-icon\\.png$/i.test(file.path)}
function openMedia(file,opener){mediaOpener=opener;q('mediaImage').src=file.url;q('mediaImage').alt=file.path;q('mediaTitle').textContent=file.path;q('mediaDialog').classList.remove('full-size');q('mediaZoom').textContent='Масштаб 100%';q('mediaDialog').showModal()}
q('mediaClose').onclick=()=>q('mediaDialog').close();
q('mediaZoom').onclick=()=>{const full=q('mediaDialog').classList.toggle('full-size');q('mediaZoom').textContent=full?'Вписать в окно':'Масштаб 100%'};
q('mediaDialog').addEventListener('close',()=>{if(mediaOpener?.isConnected)mediaOpener.focus()});
function renderMediaFiles(files){
  const key=active+'|'+JSON.stringify(files);if(key===renderedMediaKey)return;renderedMediaKey=key;
  const container=q('files');container.replaceChildren();
  const icons=files.filter(isWorldIcon),visible=files.filter(file=>!isWorldIcon(file));
  for(const file of visible){
    const row=el('div','file'),link=el('a','',file.path+' ('+(file.size/1024).toFixed(1)+' KiB)');
    link.href=file.url;link.target='_blank';link.rel='noopener';row.append(link);
    if(file.type==='.mp4'){
      const video=el('video');video.controls=true;video.preload='metadata';video.src=file.url;
      if(file.media_status==='FAIL'||file.media_status==='ERROR'){
        row.append(el('p','media-warning','Запись не прошла проверку изображения: '+(file.media_reason==='blank'?'чёрный экран':file.media_reason||'ошибка захвата')+'. Наличие MP4 не подтверждает успешную запись игры.'));
        const details=el('details');details.append(el('summary','','Показать исходную запись'),video);row.append(details);
      }else{row.append(video);if(file.media_status!=='PASS')row.append(el('p','foot','Содержимое этой записи ещё не проверено.'))}
    }else if(['.png','.jpg','.jpeg'].includes(file.type)){
      const image=el('img','media-preview');image.src=file.url;image.alt=file.path;image.loading='lazy';image.tabIndex=0;image.setAttribute('role','button');image.setAttribute('aria-label','Открыть изображение в полном размере');
      image.onclick=()=>openMedia(file,image);image.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openMedia(file,image)}};row.append(image);
      if(file.path.endsWith('/capture-frame.png'))row.append(el('p','foot','Настоящий кадр из видеозаписи. Нажми для просмотра в полном размере.'));
    }
    container.append(row);
  }
  if(!visible.length)container.append(el('p','muted','Результатов пока нет.'));
  if(icons.length)container.append(el('p','foot','Иконки сохранённых миров ('+icons.length+') исключены из галереи: это служебные картинки 64×64, не скриншоты теста.'));
}
'''
text=text.replace('const historyJobs=new Map()',functions+'\nconst historyJobs=new Map()',1)
start=text.index("q('files').replaceChildren();for(let f of files)")
end=text.index('let backups=await api(',start)
text=text[:start]+'renderMediaFiles(files);'+text[end:]
text=text.replace("j.status+' · раунд '","roundStatus(j)+' · раунд '")
text=text.replace("'Состояние: '+j.status+", "'Состояние: '+roundStatus(j)+")
needle="+(j.last_error?'\\nОшибка: '+j.last_error:'');q('pause')"
replacement="+(j.last_error?'\\nОшибка: '+j.last_error:'')+(j.status==='FINISHED'?'\\nЛимит исчерпан. Готовность мода определяется отчётом и покрытием требований ТЗ.':'');q('pause')"
assert needle in text
text=text.replace(needle,replacement,1)
text=text.replace('Циклов Codex:', 'Лимит раундов:')
text=text.replace('<p id="upload"></p>', '<p class="foot">От 1 до 20. Один раунд: изменение кода → сборка → проверки. Завершение раунда не означает готовность мода.</p><p id="upload"></p>')
text=text.replace('Видео создаются только во время настоящих графических тестов; без них запись не подменяется демонстрацией.', 'Запись проверяется на пустой экран. Размер файла не доказывает наличие игры. Скриншоты открываются по нажатию; кнопка «Масштаб 100%» показывает исходный размер.')
path.write_text(text,encoding='utf-8')
print('Updated media gallery, full-size viewer, stable playback and round labels.')
