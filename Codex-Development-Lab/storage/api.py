"""Upload watcher and private S3 URL signer; credentials never exposed to worker/dashboard."""
from __future__ import annotations
import asyncio, os
from pathlib import Path
from fastapi import FastAPI, HTTPException
try:
    from . import s3_cli as s3
except ImportError:
    import s3_cli as s3
app=FastAPI(docs_url=None,redoc_url=None)

async def watcher():
    while True:
        try:
            conf=s3.config()
            client=s3.s3client(conf)
            # blocking ffprobe/upload confined to thread, independent of request handling
            result=await asyncio.to_thread(s3.sync_once,Path(os.getenv('STORAGE_RUNS','/artifacts')),
                Path(os.getenv('STORAGE_STATE','/s3-state')),conf,client,20)
            if result['uploaded'] or result['errors']:
                print(f"S3 uploaded={result['uploaded']} errors={result['errors']}",flush=True)
        except ValueError:
            # Empty .env is allowed for offline/local use.
            pass
        except Exception as exc:
            print('S3 sync failed (retry later): '+str(type(exc).__name__),flush=True)
        await asyncio.sleep(45)

@app.on_event('startup')
async def on_start():
    app.state.background=asyncio.create_task(watcher())

@app.on_event('shutdown')
async def on_stop():
    app.state.background.cancel()

@app.get('/presign')
def presign(key:str):
    try:
        conf=s3.config()
        state=s3.state_load(Path(os.getenv('STORAGE_STATE','/s3-state'))/'upload_index.json')
        if not key.startswith(conf['prefix']+'/'):raise HTTPException(403,'Outside configured prefix')
        record=s3.uploaded_record(state,key,conf)
        if record is None:raise HTTPException(404,'Not uploaded to configured destination')
        url=s3.s3client(conf).generate_presigned_url('get_object',
            Params={'Bucket':record['bucket'],'Key':record['key'],'ResponseContentDisposition':'inline'},
            ExpiresIn=conf['presign'])
        return {'url':url,'expires_in':conf['presign']}
    except ValueError:raise HTTPException(503,'S3 not configured')

@app.get('/healthz')
def health():return {'status':'ok'}
