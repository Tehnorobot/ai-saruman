"""Read-only evidence receipt for conditional client dependency preparation."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
DOCKER=Path('C:/Users/aimihail/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe')
ENV=dict(os.environ,DOCKER_CONFIG=str(Path(tempfile.gettempdir())/'codex-lab-history-cli'))


def command(*args):
    return subprocess.check_output([str(DOCKER),*args],cwd=ROOT,env=ENV,text=True,timeout=30).strip()


def read(relative):
    return json.loads((ROOT/relative).read_text('utf-8'))


def tests(relative):
    data=(ROOT/relative).read_bytes()
    text=data.decode('utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'Ran (\d+) tests in ([0-9.]+)s',text)
    assert match and re.search(r'^OK(?: \(skipped=\d+\))?\s*$',text,re.M),relative
    assert not re.search(r'^FAILED ',text,re.M),relative
    skipped=re.search(r'OK \(skipped=(\d+)\)',text)
    return {'count':int(match[1]),'seconds':float(match[2]),
            'skipped':int(skipped[1]) if skipped else 0,'log':relative,'status':'OK'}


def main():
    full=tests('audit/client-dependencies-full-tests-v4.txt')
    focused=tests('audit/client-dependencies-focused-tests-final.txt')
    transport=tests('audit/client-dependencies-transport-tests.txt')
    assert full['count']>=1537 and focused['count']==61 and transport['count']==9,(full,focused,transport)
    clients=[read('audit/client-dependency-smoke-v2/client-dependency-smoke.json'),
             read('audit/client-dependency-smoke-v3/client-dependency-smoke.json')]
    for receipt in clients:
        assert receipt['status']=='DEPENDENCY_WORLD_OBSERVED'
        assert receipt['network']=='none' and receipt['client_uid']==1000
        assert not receipt['host_game_directory_mounted'] and not receipt['project_sources_loaded']
        assert not receipt['product_tests_executed']
        for run in receipt['runs']:
            assert run['normal_exit_code']==0 and run['player_joined']
            assert run['dependency_ready_before_player_join']
            assert run['preparation']['status']=='WORLD_LOADED'
            assert not run['preparation']['product_assertions']
            assert all(run['preparation']['facts'][key] for key in
                       ('title_screen_observed','task_chain_present','slot_handler_present','world_loaded','player_present'))
    loom=read('audit/client-dependency-loom-v3/loom-dependency-smoke.json')
    assert loom['status']=='ACTUAL_LOOM_DEPENDENCY_SELECTION_OBSERVED'
    assert loom['helper_selected'] and loom['native_quickplay_removed'] and loom['private_game_directory']
    assert not loom['live_cache_written'] and not loom['product_tests_executed']
    images={name:command('image','inspect','codex-lab-'+family+':'+tag,'--format','{{.Id}}')
            for name,family,tag in (('worker','worker','client-dependencies-v4'),
                                    ('service','service','client-dependencies-v4'))}
    with urlopen('http://localhost:8080/api/jobs',timeout=20) as response:jobs=json.load(response)
    deployment=ROOT/'audit/client-dependencies-v4-deployment.json'
    result={'status':'VALIDATED','images':images,'full_suite':full,'focused_suite':focused,'transport_suite':transport,
            'real_client_runs':sum(len(c['runs']) for c in clients),'real_client_receipts':clients,
            'real_loom':loom,'installed':deployment.exists(),
            'jobs':[{key:j[key] for key in ('id','status','round','phase')} for j in jobs],
            'new_llm_roles':False,'model_invoked_by_validation':False,
            'env_or_credentials_changed':False,'host_game_directory_mounted':False,
            'product_gameplay_claimed':False,
            'scope_note':'Conditional dependency setup is validated. Actual addon acceptance requires its independent gameplay scenarios.'}
    recovery=read('audit/client-dependencies-orphan-recovery.json')
    assert recovery['never_started'] and recovery['project_sources_unchanged'] and not recovery['job_resumed']
    result['unstarted_run_recovery']=recovery
    if deployment.exists():
        result['deployment']=read(deployment.relative_to(ROOT))
        assert result['deployment']['installed'] and result['deployment']['jobs_unchanged']
        for service,key in (('worker','worker'),('dashboard','service')):
            actual=command('inspect','codex-development-lab-'+service+'-1','--format','{{.Image}}')
            assert actual==images[key],(service,actual,images[key])
        inventory=read('audit/client-dependencies-source-inventory.json')
        program=('import hashlib,json; from pathlib import Path; '
                 'from worker.sandbox import DockerSandbox; from common.acceptance import source_fingerprint; '
                 'names='+repr(list(inventory))+'; print(json.dumps({'
                 '"sources":{p:hashlib.sha256((Path("/opt/lab")/p).read_bytes()).hexdigest() for p in names},'
                 '"project_source_sha256":source_fingerprint(Path("/projects/7aa2e3592f1640/workspace")),'
                 '"runner_health":DockerSandbox().api("GET","/health")}))')
        observed=json.loads(command('exec','codex-development-lab-worker-1','python3','-c',program))
        assert observed['sources']==inventory
        assert observed['project_source_sha256']==recovery['source_sha256']
        assert observed['runner_health']=={'ready':True}
        assert not list((ROOT/'projects').glob('*/.lab-runner.json'))
        assert all(j['status'] in ('USER_PAUSED','FAILED','STOPPED','FINISHED') for j in jobs)
        result['installed_sources_match']=True
        result['project_sources_unchanged_during_installation']=True
        result['runner_health']=observed['runner_health']
    (ROOT/'audit/client-dependencies-validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('status','images','full_suite','focused_suite','real_client_runs','installed')}))


if __name__=='__main__':main()
