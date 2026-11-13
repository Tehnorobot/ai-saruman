"""One public utterance, with lossless Minecraft transport segmentation."""
from saruman.activity import emit
from saruman.mod_goals import public_chat_text


def public_text(value):
    if not isinstance(value,str):raise ValueError('Expected public speech text')
    if any(line.lstrip().startswith(('@','/','!','$')) for line in value.splitlines()):
        raise ValueError('Public speech cannot contain command lines')
    if any(ord(char)<32 and not char.isspace() for char in value):
        raise ValueError('Public speech contains control characters')
    return ' '.join(value.split())


def chat_parts(text,limit=240):
    """Preserve every character. Reserve room for server prefixes in the 256-unit limit."""
    parts=[];remaining=text
    while remaining:
        units=end=0
        for char in remaining:
            cost=2 if ord(char)>0xffff else 1
            if units+cost>limit:break
            units+=cost;end+=1
        if not end:raise ValueError('Chat transport limit too small')
        cut=end
        if end<len(remaining):
            space=remaining.rfind(' ',0,end)
            if space>0:cut=space+1
            while remaining[cut:].lstrip().startswith(('@','/','!','$')):
                previous=remaining.rfind(' ',0,cut-1)
                if previous>0:cut=previous+1
                else:
                    cut=end
                    while cut>0 and remaining[cut:].lstrip().startswith(('@','/','!','$')):cut-=1
                    if not cut:raise ValueError('Cannot split speech safely')
                    break
        part=remaining[:cut]
        public_chat_text(part.strip())  # Validate ALL parts before enqueueing anything.
        parts.append(part);remaining=remaining[cut:]
    return parts


def queue_public_speech(ctx,text,emotion,*,request_id='',chat=True):
    text=public_text(text)
    parts=chat_parts(text) if chat and text else []
    if not text:return ''
    ctx['tts_queue'].append({'text':text,'emotion':emotion,'request_id':request_id})
    ctx['chat_queue'].extend(parts)
    emit('response.queued',status='queued',trace=request_id,
        details={'text':text,'emotion':emotion,'chat_queued':bool(parts),'chat_parts':len(parts),
                 'voice_queue_size':len(ctx['tts_queue'])})
    return text
