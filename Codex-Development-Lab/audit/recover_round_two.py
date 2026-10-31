"""Run inside the worker: return preserved files, never rerun model/game commands."""
import hashlib
import json
from pathlib import Path
import stat
import tempfile

from common.project_lock import project_lock
from common.safe_archive import unpack_tree
from worker.sandbox import DockerSandbox
from worker.engine import write_json

pid,jid='94a2c07a7a5b44','89cd3a16ad2c4e'
project=Path('/projects')/pid
job=Path('/artifacts')/pid/jid
stage=job/'round-02'
evidence=job/'recovery-2026-10-21'
token='0c568b8a2e4a47ed80f5b78b5ea06e6d'
sandbox=DockerSandbox()

def manifest(folder):
    result={}
    for path in folder.rglob('*'):
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode):raise ValueError('Unexpected link in captured source')
        if stat.S_ISREG(info.st_mode):
            result[path.relative_to(folder).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result

with project_lock(project,allow_runner=True):
    state=sandbox.api('GET','/runs/'+token)
    assert state['Config']['Labels']['codex-lab.project']==pid
    assert sandbox.api('GET','/runs/by-job/'+jid)['Id']==token
    assert not state['State']['Running'] and state['State']['ExitCode']==0
    assert not state['State'].get('OOMKilled')
    with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
        source,output=Path(source),Path(output)
        with (evidence/'workspace.tar').open('rb') as archive:unpack_tree(archive,source)
        with (evidence/'output.tar').open('rb') as archive:unpack_tree(archive,output)
        expected=manifest(source)
        report=json.loads((output/'verification.json').read_text())
        assert report['status']=='BUILD_VERIFIED_GAMEPLAY_UNVERIFIED'
        assert all(check['status']=='PASS' for check in report['checks'])
        assert [c['label'] for c in report['checks']]==[
            'gradle_build','discover_game_test','minecraft_client_gametest']
        for jar in report['jars']:
            actual=output/'builds'/jar['filename']
            assert actual.is_file() and actual.stat().st_size==jar['size']
            assert hashlib.sha256(actual.read_bytes()).hexdigest()==jar['sha256']
        # recover() confirms stop, restores the workspace, deletes the owned run,
        # then removes quarantine. The original snapshots remain in evidence.
        sandbox.recover(project)
        assert manifest(project/'workspace')==expected
        sandbox._copy_output(output,stage)
        report['recovery_note']='Recovered from preserved supervisor output; no verification command was rerun.'
        write_json(stage/'verification.json',report)
        write_json(stage/'verification_complete.json',report)
        write_json(job/'latest_verification.json',report)
        result={'workspace_files':len(expected),'source_hashes_match':True,
                'jars':report['jars'],'verification_status':report['status'],
                'checks':[{k:c[k] for k in ('label','status','duration_seconds')} for c in report['checks']],
                'runner_removed':True,'quarantine_removed':True,'model_or_game_restarted':False}
        assert not (project/'.lab-runner.json').exists()
        assert not (project/'.lab-workspace-swap.json').exists()
        write_json(evidence/'recovery-result.json',result)
        print(json.dumps(result))
