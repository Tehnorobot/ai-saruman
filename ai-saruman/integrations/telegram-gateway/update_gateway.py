"""Update the existing VPS gateway over owner SSH. Preserve .env and publications."""
import argparse
import ast
import base64
import json
from pathlib import Path
import re
import subprocess


def remote_script(folder):
    files={name:(Path(folder)/name).read_text(encoding='utf-8-sig') for name in ('gateway.py','media.py')}
    for source in files.values():ast.parse(source)
    payload=base64.b64encode(json.dumps(files).encode()).decode()
    return '''import ast,base64,json,os,pathlib,subprocess,tempfile,time,urllib.request
root=pathlib.Path('/opt/saruman-telegram')
if not (root/'.env').is_file():raise SystemExit('Existing server .env is missing; no changes made')
files=json.loads(base64.b64decode('''+repr(payload)+'''))
for source in files.values():ast.parse(source)
# Never interrupt a publication that is currently inside its Telegram API call.
import sys,sqlite3
sys.path.insert(0,str(root))
from gateway import Settings
settings=Settings.load(root/'.env')
with sqlite3.connect(settings.database,timeout=10) as db:
    if db.execute("SELECT 1 FROM publications WHERE status='sending' LIMIT 1").fetchone():
        raise SystemExit('A publication is in flight. Pause Telegram in Lab and rerun after delivery.')
backup={name:(root/name).read_bytes() if (root/name).exists() else None for name in files}
try:
    subprocess.run(['systemctl','stop','saruman-telegram'],check=True)
    for name,source in files.items():
        path=root/name
        temporary=path.with_suffix('.update')
        temporary.write_text(source,encoding='utf-8');temporary.chmod(0o644);os.replace(temporary,path)
    subprocess.run(['systemctl','start','saruman-telegram'],check=True)
    for attempt in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8787/healthz',timeout=2) as response:health=json.load(response)
            if health.get('api_version',0)>=4:break
        except (OSError,ValueError):pass
        time.sleep(.5)
    else:raise RuntimeError('Updated gateway did not report API v4')
except BaseException:
    subprocess.run(['systemctl','stop','saruman-telegram'],check=False)
    for name,body in backup.items():
        path=root/name
        if body is None:path.unlink(missing_ok=True)
        else:path.write_bytes(body)
    subprocess.run(['systemctl','start','saruman-telegram'],check=False)
    raise
print('Gateway API v4 ready. Credentials, channel and publication IDs preserved. No test post sent.')
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',required=True);parser.add_argument('--port',type=int,default=22)
    parser.add_argument('--identity',type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*',args.server) or not 1<=args.port<=65535:
        parser.error('Invalid SSH host/port')
    argv=['ssh','-p',str(args.port),'-o','StrictHostKeyChecking=yes']
    if args.identity:argv+=['-i',str(args.identity)]
    argv+=['root@'+args.server,'python3','-']
    print('Pause Telegram / Stop in Lab first. Enter the VPS root password in the SSH prompt if requested.')
    subprocess.run(argv,input=remote_script(Path(__file__).parent).encode('utf-8'),check=True)


if __name__=='__main__':main()
