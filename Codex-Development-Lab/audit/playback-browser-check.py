"""Independent real-Edge media verification; read-only live API, no jobs started."""
import base64, hashlib, json, os, socket, struct, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'playback-inspection'; OUT.mkdir(exist_ok=True)
URL='http://localhost:8080/api/jobs/89cd3a16ad2c4e/files/video-fix-check/minecraft_client_capture.mp4'
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=('<!doctype html><video controls muted autoplay src="'+URL+'"></video>').encode()
        if self.path=='/gallery':
            page=(ROOT.parent/'app/index.html').read_text(encoding='utf-8')
            mock="<script>window.fetch=async()=>({ok:true,status:200,text:async()=>'[]'});</script>"
            body=page.replace('<script>',mock+'<script>',1).encode('utf-8')
        self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

class WS:
    def __init__(self,url):
        from urllib.parse import urlsplit
        u=urlsplit(url); self.s=socket.create_connection((u.hostname,u.port)); self.s.settimeout(10)
        key=base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET {u.path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        header=b''
        while not header.endswith(b'\r\n\r\n'): header+=self.s.recv(1)
        assert header.startswith(b'HTTP/1.1 101 '),header
        self.seq=0; self.events=[]
    def recv(self,n):
        b=b''
        while len(b)<n:
            c=self.s.recv(n-len(b))
            if not c: raise EOFError()
            b+=c
        return b
    def receive(self):
        a,b=self.recv(2); n=b&127
        if n==126:n=struct.unpack('!H',self.recv(2))[0]
        elif n==127:n=struct.unpack('!Q',self.recv(8))[0]
        return json.loads(self.recv(n))
    def call(self,method,params=None):
        self.seq+=1; payload=json.dumps({'id':self.seq,'method':method,'params':params or {}}).encode(); mask=os.urandom(4)
        n=len(payload); hdr=bytes([129,128|n]) if n<126 else bytes([129,254])+struct.pack('!H',n)
        self.s.sendall(hdr+mask+bytes(v^mask[i%4] for i,v in enumerate(payload)))
        while True:
            r=self.receive()
            if r.get('id')==self.seq:return r
            self.events.append(r)
    def evaluate(self,script):
        return self.call('Runtime.evaluate',{'expression':script,'returnByValue':True})

STATE="""(()=>{let v=document.querySelector('video');return {url:location.href,video:!!v,paused:v?.paused,time:v?.currentTime,duration:v?.duration,ended:v?.ended,ready:v?.readyState,network:v?.networkState,error:v?.error&&{code:v.error.code,message:v.error.message},buffered:v&&Array.from({length:v.buffered.length},(_,i)=>[v.buffered.start(i),v.buffered.end(i)]),events:window.mediaEvents||[]}})()"""
def check(ws,url):
    ws.call('Page.navigate',{'url':url}); time.sleep(2)
    initial=ws.evaluate(STATE)
    ws.evaluate("window.mediaEvents=[];var v=document.querySelector('video');if(v){for(let e of ['error','ended','seeking','seeked','playing','pause','waiting','stalled'])v.addEventListener(e,()=>mediaEvents.push([e,v.currentTime]));v.muted=true;v.currentTime=1;v.play().catch(e=>mediaEvents.push(['playerror',e.message]))}")
    samples=[]
    for i in range(4):time.sleep(1); samples.append(ws.evaluate(STATE))
    ws.evaluate("document.querySelector('video').currentTime=10")
    time.sleep(2); seek=ws.evaluate(STATE)
    ws.evaluate("document.querySelector('video').currentTime=28")
    time.sleep(4); end=ws.evaluate(STATE)
    events=ws.events[:];ws.events=[]
    return {'url':url,'initial':initial,'samples':samples,'seek10':seek,'end':end,'cdp_events':events}

def check_gallery(ws,url):
    ws.call('Page.navigate',{'url':url});time.sleep(1)
    script="""active='fixture';window.fixtureFiles=[{path:'round-05/video.mp4',url:VIDEO_URL,type:'.mp4',size:1671334,media_status:'PASS'}];renderMediaFiles(fixtureFiles,'fixture');window.originalVideo=document.querySelector('#files video');originalVideo.muted=true;originalVideo.play();""".replace('VIDEO_URL',json.dumps(URL))
    ws.evaluate(script);time.sleep(2)
    ws.evaluate("window.beforeTime=originalVideo.currentTime;window.mediaMutationEvents=[];for(let e of ['emptied','abort','pause','ended'])originalVideo.addEventListener(e,()=>mediaMutationEvents.push(e));")
    results=[]
    changes=[
        "fixtureFiles.push({path:'round-05/build.log',url:'/build.log',type:'.log',size:1});",
        "fixtureFiles[1].size=500;",
        "fixtureFiles.push({path:'round-06/report.json',url:'/report.json',type:'.json',size:1});",
        "fixtureFiles.push({path:'round-06/screenshots/frame.png',url:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM1sAAAAASUVORK5CYII=',type:'.png',size:100});",
        "fixtureFiles[0].media_status='FAIL';fixtureFiles[0].media_reason='blank';",
        "fixtureFiles.pop();",
    ]
    for mutation in changes:
        results.append(ws.evaluate(mutation+"renderMediaFiles(fixtureFiles,'fixture');JSON.stringify({same:originalVideo===document.querySelector('#files video'),connected:originalVideo.isConnected,time:originalVideo.currentTime,paused:originalVideo.paused,events:mediaMutationEvents});"))
        time.sleep(.3)
    time.sleep(2)
    results.append(ws.evaluate("renderMediaFiles([],'stale-job');JSON.stringify({same:originalVideo===document.querySelector('#files video'),connected:originalVideo.isConnected,before:beforeTime,after:originalVideo.currentTime,events:mediaMutationEvents,paused:originalVideo.paused});"))
    return {'url':url,'checks':results}

def value(reply):return reply['result']['result'].get('value')

def validate(results):
    for media in results[:2]:
        samples=[value(r) for r in media['samples']]
        assert samples[-1]['time']>samples[0]['time']+2,(media['url'],samples)
        assert all(s['ready']>=2 and s['error'] is None and not s['ended'] for s in samples),samples
        assert value(media['seek10'])['time']>11,value(media['seek10'])
        end=value(media['end'])
        assert end['ended'] and abs(end['time']-31.4)<.01 and end['error'] is None,end
    checks=[json.loads(value(r)) for r in results[2]['checks']]
    assert all(r['same'] and r['connected'] and not r['paused'] and not r['events'] for r in checks),checks
    assert checks[-1]['after']>checks[-1]['before']+3,checks[-1]

def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler); threading.Thread(target=server.serve_forever,daemon=True).start()
    edge=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    log=open(OUT/'edge.log','wb')
    endpoint=OUT/'edge-profile'/'DevToolsActivePort'
    endpoint.unlink(missing_ok=True)
    process=subprocess.Popen([edge,'--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-extensions','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=0','--user-data-dir='+str(OUT/'edge-profile'),'about:blank'],stdout=log,stderr=log)
    try:
        for _ in range(100):
            if endpoint.exists():break
            time.sleep(.1)
        port=endpoint.read_text().splitlines()[0]
        targets=json.load(urllib.request.urlopen('http://127.0.0.1:'+port+'/json'))
        ws=WS(next(t['webSocketDebuggerUrl'] for t in targets if t['type']=='page'))
        ws.call('Page.enable');ws.call('Runtime.enable');ws.call('Log.enable');ws.call('Network.enable')
        results=[check(ws,URL),check(ws,f'http://127.0.0.1:{server.server_port}/'),check_gallery(ws,f'http://127.0.0.1:{server.server_port}/gallery')]
        name='browser-result-final.json' if '--expect-pass' in sys.argv else 'browser-result.json'
        (OUT/name).write_text(json.dumps(results,indent=2),encoding='utf-8')
        if '--expect-pass' in sys.argv:validate(results); print('PASS: direct, embedded, seek, end, gallery identity/progress')
        for r in results:
            print(r['url'])
            if 'checks' in r:
                print('gallery',json.dumps(r['checks']));continue
            for k in ['initial','samples','seek10','end']:
                v=r[k];print(k,[i.get('result',{}).get('result',{}).get('value') for i in v] if isinstance(v,list) else v.get('result',{}).get('result',{}).get('value'))
    finally:
        process.terminate();process.wait(10);server.shutdown();log.close()
if __name__=='__main__':main()
