"""Synthetic checks through the live restricted runner; no DB/model/game calls."""
import json
from pathlib import Path
from worker.sandbox import DockerSandbox

workspace=Path('/projects/'+'e'*14+'/workspace')
workspace.mkdir(parents=True,exist_ok=True)
output=Path('/output')
sandbox=DockerSandbox()
program='''import json,os
from pathlib import Path
assert not Path('/data/lab.sqlite3').exists()
assert not Path('/agent-tasks').exists()
assert not Path('/var/run/docker.sock').exists()
assert os.getenv('MINECRAFT_EULA_ACCEPTED')=='false'
assert os.getenv('JAVA_TOOL_OPTIONS')=='-XX:ActiveProcessorCount=2'
assert os.getenv('LP_NUM_THREADS')=='2'
Path('runner-result.txt').write_text('isolated runner completed')
print(json.dumps({'isolated':True,'consent':'false','pid_limit':Path('/sys/fs/cgroup/pids.max').read_text().strip()}))
'''
result=sandbox.execute(['python3','-c',program],workspace,output/'runner.log',45,'f'*14,lambda:None,
                       env={'MINECRAFT_EULA_ACCEPTED':'false'})
assert result['status']=='PASS',result
assert (workspace/'runner-result.txt').read_text()=='isolated runner completed'
timeout=sandbox.execute(['python3','-c',
    'from pathlib import Path;import time;Path("started.txt").write_text("started");'
    'time.sleep(30);Path("should-not-exist.txt").write_text("unsafe")'],
    workspace,output/'timeout.log',4,'f'*14,lambda:None)
assert timeout['status']=='TIMEOUT',timeout
assert (workspace/'started.txt').exists()
assert not (workspace/'should-not-exist.txt').exists()
assert not (workspace.parent/'.lab-runner.json').exists()
report={'normal':result,'timeout':timeout,'workspace_restored':True,'run_marker_removed':True}
(output/'runner-smoke.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
