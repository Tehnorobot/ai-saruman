"""Real isolated generator crash/recovery probe; never product acceptance."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import uuid

sys.path.insert(0,'/opt/lab')
from worker.world_fixture import WorldFixtureSession, PROFILE
from worker.world_templates import profile_key


def children(parent):
    result=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:
            parts=(entry/'stat').read_text().rsplit(')',1)[1].split()
            cmd=(entry/'cmdline').read_bytes().replace(b'\0',b' ')
            if int(parts[1])==parent and b'server-1.21.11.jar' in cmd:
                result.append(int(entry.name))
        except (OSError,IndexError,ValueError):pass
    return result


output=Path('/output/cancel-smoke')
output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='lab-cancel-probe-') as temporary:
    workspace=Path(temporary)
    (workspace/'gradle.properties').write_text('minecraft_version=1.21.11\n')
    store=Path('/world-templates/cancel-smoke-'+uuid.uuid4().hex)
    session=WorldFixtureSession(workspace,output/'killed',180,eula=True,store=store)
    deadline=time.monotonic()+30
    java=[]
    while time.monotonic()<deadline and not java:
        java=children(session.process.pid)
        time.sleep(.02)
    assert java,'Generator JVM did not start'
    os.kill(session.process.pid,signal.SIGKILL)
    session.process.wait(timeout=10)
    deadline=time.monotonic()+10
    def alive(pid):
        try:
            state=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[0]
            return state!='Z'
        except FileNotFoundError:return False
    while time.monotonic()<deadline and any(alive(pid) for pid in java):time.sleep(.05)
    assert not any(alive(pid) for pid in java),'Generator survived killed supervisor'
    assert not (store/'templates'/profile_key(PROFILE)).exists(),'Partial generation was published'
    failed=session.finish()
    assert failed['status']=='ERROR',failed
    recovery=WorldFixtureSession(workspace,output/'recovery',180,eula=True,store=store)
    ready=recovery.finish()
    assert ready['status']=='TEMPLATE_READY',ready
    warm=WorldFixtureSession(workspace,output/'warm',180,eula=True,store=store).finish()
    assert warm['status']=='TEMPLATE_READY' and warm['template']['reused'],warm
    receipt={'status':'GENERATOR_CRASH_RECOVERY_OBSERVED','killed_preparer_exit':session.process.returncode,
        'generator_did_not_survive':True,'partial_template_not_published':True,
        'fresh_generation_recovered':True,'warm_reused':True,
        'template_key':ready['template']['key'],'product_tests_executed':False}
    (output/'cancel-smoke.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt))
