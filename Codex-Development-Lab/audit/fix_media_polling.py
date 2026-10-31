"""Preserve connected players when unrelated artifacts change during polling."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
path=root/'app/index.html'
page=path.read_text(encoding='utf-8')
page=page.replace("let renderedMediaKey='',mediaOpener=null;", "let renderedMediaKey='',renderedMediaJob=null,mediaOpener=null;const mediaRows=new Map();let mediaFooter=null;")
old="""function renderMediaFiles(files){
  const key=active+'|'+JSON.stringify(files);if(key===renderedMediaKey)return;renderedMediaKey=key;
  const container=q('files');container.replaceChildren();
  const icons=files.filter(isWorldIcon),visible=files.filter(file=>!isWorldIcon(file));
  for(const file of visible){
"""
new="""function renderMediaFiles(files,jid=active){
  if(jid!==active)return;
  const key=jid+'|'+JSON.stringify(files);if(key===renderedMediaKey)return;renderedMediaKey=key;
  const container=q('files');
  if(renderedMediaJob!==jid){
    for(const entry of mediaRows.values()){const video=entry.row.querySelector('video');if(video)video.pause()}
    mediaRows.clear();container.replaceChildren();renderedMediaJob=jid;
    mediaFooter=el('div');container.append(mediaFooter);
  }
  const icons=files.filter(isWorldIcon),visible=files.filter(file=>!isWorldIcon(file));
  const present=new Set(visible.map(file=>file.path));
  for(const [path,entry] of mediaRows){
    if(present.has(path))continue;
    const video=entry.row.querySelector('video');
    if(video&&!video.paused&&!video.ended)continue;
    entry.row.remove();mediaRows.delete(path);
  }
  for(const file of visible){
    const existing=mediaRows.get(file.path);
    if(existing&&existing.url===file.url&&existing.type===file.type){
      existing.row.querySelector('a').textContent=file.path+' ('+(file.size/1024).toFixed(1)+' KiB)';
      // Never detach, move, reload or change src of an existing video on a poll.
      if(file.type==='.mp4'){
        let warning=existing.row.querySelector('.media-warning');
        const invalid=file.media_status==='FAIL'||file.media_status==='ERROR';
        if(invalid&&!warning){warning=el('p','media-warning');existing.row.append(warning)}
        if(invalid)warning.textContent='Запись не прошла проверку изображения: '+(file.media_reason==='blank'?'чёрный экран':file.media_reason||'ошибка захвата')+'.';
        else if(warning)warning.remove();
        const note=existing.row.querySelector('.foot');if(file.media_status==='PASS'&&note)note.remove();
      }
      continue;
    }
    if(existing){existing.row.querySelector('video')?.pause();existing.row.remove()}
"""
assert old in page
page=page.replace(old,new,1)
old="""    container.append(row);
  }
  if(!visible.length)container.append(el('p','muted','Результатов пока нет.'));
  if(icons.length)container.append(el('p','foot','Иконки сохранённых миров ('+icons.length+') исключены из галереи: это служебные картинки 64×64, не скриншоты теста.'));
"""
new="""    mediaRows.set(file.path,{row,url:file.url,type:file.type});container.insertBefore(row,mediaFooter);
  }
  mediaFooter.replaceChildren();
  if(!mediaRows.size)mediaFooter.append(el('p','muted','Результатов пока нет.'));
  if(icons.length)mediaFooter.append(el('p','foot','Иконки сохранённых миров ('+icons.length+') исключены из галереи: это служебные картинки 64×64, не скриншоты теста.'));
"""
assert old in page
page=page.replace(old,new,1)
old="let files=await api('/api/jobs/'+active+'/files');renderMediaFiles(files);"
new="const mediaJob=active;let files=await api('/api/jobs/'+mediaJob+'/files');renderMediaFiles(files,mediaJob);"
assert old in page
page=page.replace(old,new,1)
path.write_text(page,encoding='utf-8')
