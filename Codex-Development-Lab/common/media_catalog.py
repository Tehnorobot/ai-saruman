"""Paginated gallery: media/builds only, newest numeric round first."""
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from common.history import _open

KINDS={'.mp4','.png','.jpg','.jpeg','.jar','.zip'}
ROUND=re.compile(r'round-([0-9]{2,16})\Z')


def page(root,jid,*,round_number=None,offset=0,limit=100):
    if not re.fullmatch('[0-9a-f]{14}',jid):raise ValueError('Invalid job')
    if round_number is not None and (type(round_number) is not int or not 1<=round_number<=2**53-1):
        raise ValueError('Invalid round')
    if type(offset) is not int or not 0<=offset<=2**53-1:raise ValueError('Invalid offset')
    if type(limit) is not int or not 1<=limit<=250:raise ValueError('Invalid limit')
    root=Path(root)
    if root.is_symlink() or root.parent.is_symlink():raise ValueError('Linked artifact root')
    items=[];rounds=set();visited=0;truncated=False
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in ('agent-history','verification-retries')
                 and not (Path(directory)/name).is_symlink()]
        if Path(directory)==root:
            rounds.update(int(m[1]) for name in dirs if (m:=ROUND.fullmatch(name)))
            dirs.sort(key=lambda name:-(int(m[1]) if (m:=ROUND.fullmatch(name)) else 0))
            if round_number is not None:
                dirs[:]=[name for name in dirs if (m:=ROUND.fullmatch(name)) and int(m[1])==round_number]
                continue
        for name in files:
            path=Path(directory)/name
            if path.suffix.lower() not in KINDS:continue
            visited+=1
            if visited>100000:truncated=True;break
            relative=path.relative_to(root);parts=relative.parts
            match=ROUND.fullmatch(parts[0]);number=int(match[1]) if match else None
            if round_number is not None and number!=round_number:continue
            # Service icons are not screenshots of an executed scenario.
            if (path.suffix.lower()=='.png' and parts[0].startswith('round-')
                    and 'screenshots' in parts and 'saves' in name.lower() and name.endswith('-icon.png')):continue
            try:
                with _open(root,parts) as source:size=os.fstat(source.fileno()).st_size
            except (OSError,ValueError):continue
            rel=relative.as_posix()
            item={'path':rel,'size':size,'type':path.suffix.lower(),'round':number,
                  'url':'/api/jobs/'+jid+'/files/'+quote(rel,safe='/')}
            if path.suffix.lower()=='.mp4':
                try:
                    with _open(root,(*parts[:-1],'video_capture.json')) as f:
                        raw=f.read(65537)
                    quality=json.loads(raw) if len(raw)<=65536 else None
                    if isinstance(quality,dict) and quality.get('status') in ('PASS','FAIL','ERROR'):
                        item.update(media_status=quality['status'],media_reason=str(quality.get('reason',''))[:200])
                except (OSError,ValueError,UnicodeError,RecursionError):pass
            items.append(item)
        if truncated:break
    items.sort(key=lambda item:(-(item['round'] or 0),item['path']))
    selected=items[offset:offset+limit]
    return {'files':selected,'rounds':sorted(rounds,reverse=True),'round':round_number,
            'total':len(items),'offset':offset,'limit':limit,'has_more':offset+len(selected)<len(items),
            'next_offset':offset+len(selected),'truncated':truncated}
