"""Apply the reviewed isolated fixture patch to an idle project, retaining history."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
import xml.etree.ElementTree as ET
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from common import acceptance, evidence_access, pipeline_memory
from common.project_lock import project_lock

PROJECT='7aa2e3592f1640'
JOB='69496be60a264a'
WORK=ROOT/'audit/mage-home-fixture-work'
LIVE=ROOT/'projects'/PROJECT/'workspace'
JOB_ROOT=ROOT/'artifacts'/PROJECT/JOB


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def idle():
    with urlopen('http://localhost:8080/api/jobs',timeout=30) as response:jobs=json.load(response)
    job=next(j for j in jobs if j['id']==JOB)
    assert job['project_id']==PROJECT and job['status']=='USER_PAUSED'
    assert not (LIVE.parent/'.lab-runner.json').exists()
    for box in ('agent-tasks','planner-tasks'):
        for state in ('requests','running'):assert not list((ROOT/box/state).glob('*.json'))
    return job


def run():
    before_job=idle()
    tests=[ET.fromstring(p.read_bytes()) for p in (WORK/'workspace/build/test-results').rglob('TEST-*.xml')]
    assert sum(int(t.get('tests','0')) for t in tests)==128
    assert all(int(t.get(k,'0'))==0 for t in tests for k in ('failures','errors','skipped'))
    gameplay=json.loads((ROOT/'audit/mage-home-gameplay-v5/gameplay-check.json').read_text())
    assert gameplay['status']=='EXECUTION_OBSERVED'
    assert gameplay['addon_sha256']==digest(WORK/'workspace/build/libs/mage_home-0.1.0.jar')
    expected={'persistence-after-exit','union-task-regression','site-blocked-by-water',
              'no-new-house-inside-radius','next-palette-after-radius'}
    assert {s['scenario_id'] for s in gameplay['scenarios']}==expected
    for scenario in gameplay['scenarios']:
        assert all(e['command']['status']=='PASS' for e in scenario['executions'])
        cases=[c for e in scenario['executions'] for report in e['junit'] for c in report['cases']]
        assert any(c['name']==scenario['scenario_id'] and c['status']=='PASS' for c in cases),scenario['scenario_id']
    changes=json.loads((WORK/'changes.json').read_text())
    bases=json.loads((WORK/'base-hashes.json').read_text(encoding='utf-8-sig'))
    assert len(changes)==12 and len({r['path'] for r in changes})==12
    assert all(not p.startswith('/') and '..' not in Path(p).parts and '\\' not in p for p in bases)
    assert all(digest(LIVE/p)==sha for p,sha in bases.items()),'Live sources changed; review a three-way merge'
    with project_lock(LIVE.parent):
        idle()
        retained=['pipeline/catalog.json','pipeline/graph.json','pipeline/state.json',
                  'pipeline/latest-result.json','latest_acceptance.json']
        original_state={p:digest(JOB_ROOT/p) for p in retained if (JOB_ROOT/p).is_file()}
        source_before=acceptance.source_fingerprint(LIVE)
        for row in changes:
            path=row['path']
            assert path in bases or row['kind']=='added'
            assert not Path(path).is_absolute() and '..' not in Path(path).parts and '\\' not in path
            target=LIVE/path
            assert not target.is_symlink()
            assert (digest(target) if target.is_file() else None)==row['base_sha256'],path
            assert digest(WORK/'workspace'/path)==row['sha256'],path
        for row in changes:
            target=LIVE/row['path'];target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name('.fixture-update-'+uuid.uuid4().hex+'.tmp')
            with temporary.open('xb') as handle:
                handle.write((WORK/'workspace'/row['path']).read_bytes());handle.flush();os.fsync(handle.fileno())
            temporary.replace(target)
            assert digest(target)==row['sha256']
        assert original_state=={p:digest(JOB_ROOT/p) for p in original_state},'Canonical history changed'
        after_job=idle()
        for key in ('status','phase','round','provider','model','reasoning_effort','role_models','resume_generation'):
            assert before_job.get(key)==after_job.get(key),key
        scope={'project_id':PROJECT,'spec_revision':hashlib.sha256(acceptance.read_spec(LIVE.parent).encode()).hexdigest(),
               'source_revision':acceptance.source_fingerprint(LIVE),'round':after_job['round']}
        refs=[]
        for row in changes:
            refs.append(evidence_access.register_file(LIVE,WORK/'workspace',row['path'],
                scope=scope,kind='manual_source_snapshot',metadata={'source_sha256':row['sha256'],
                    'tested_addon_sha256':gameplay['addon_sha256'],'canonical_acceptance':False}))
        for folder in ('mage-home-gameplay-v1','mage-home-gameplay-v3','mage-home-gameplay-v4','mage-home-gameplay-v5'):
            source=ROOT/'audit'/folder
            if not source.exists():continue
            run_report=source/'gameplay-check.json'
            run_jar_sha=(json.loads(run_report.read_text()).get('addon_sha256') if run_report.is_file()
                         else digest(source/'tested-addon.jar') if (source/'tested-addon.jar').is_file() else None)
            metadata={'canonical_acceptance':False,'run_addon_sha256':run_jar_sha,
                      'historical_run':folder!='mage-home-gameplay-v5',
                      'scope_meaning':'Current project navigation; original run is identified by its own JAR hash and receipts.'}
            for path in sorted(source.rglob('*')):
                if not path.is_file() or path.is_symlink():continue
                if path.suffix not in ('.json','.jsonl','.xml','.log'):continue
                refs.append(evidence_access.register_file(LIVE,ROOT/'audit',path.relative_to(ROOT/'audit').as_posix(),
                    scope=scope,kind='manual_runtime_diagnostic',metadata=metadata))
        frozen=WORK/'workspace/build/libs/mage_home-0.1.0-sources.jar'
        refs.append(evidence_access.register_file(LIVE,WORK,'workspace/build/libs/'+frozen.name,
            scope=scope,kind='manual_frozen_sources',metadata={'canonical_acceptance':False,
                'run_addon_sha256':gameplay['addon_sha256'],'historical_run':False}))
        result={'applied':True,'at':datetime.now(timezone.utc).isoformat(),'project_id':PROJECT,'job_id':JOB,
            'source_before':source_before,'source_after':scope['source_revision'],
            'files':changes,'canonical_state_unchanged':True,'job_unchanged':True,
            'historical_acceptance_rewritten':False,'model_invoked':False,'evidence_refs':refs}
        payload={**result,'summary':'Executable scenario fixtures and durable step reports installed; real-game diagnostic results retained.',
            'notes_md':'The real UnionClef regression observed the root task, ordinary material receipt and an actual first block. '
                'Water, radius and palette checks have independent diagnostic JUnit. Persistence uses two real JVMs with one '
                'private game directory; read the final v5 evidence for its result. The v1 failed preparation remains in history. '
                'ClientWorldView now distinguishes a missing client chunk from air using nullable FULL lookup, without forcing loading. '
                'Use runPersistenceCheck for the persistence scenario; the proposed manifest selects that task. '
                'Continue the existing project and preserve its requirements/graph. These manual diagnostics do not change canonical '
                'acceptance: the ordinary independent verifier still checks the complete current build, including full-house, '
                'give-refused and stop/resume behaviours. Read the frozen sources and exact logs rather than treating initial SKIP '
                'reports as proof that nothing started.'}
        record=pipeline_memory.append_event(JOB_ROOT,LIVE,'manual',payload,scope=scope,producer='controller',tags=[])
        result['handoff_record_id']=record['record_id']
        (ROOT/'audit/mage-home-fixture-installation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({'applied':True,'files':len(changes),'source_revision':scope['source_revision'],
            'handoff_record_id':record['record_id'],'job_status':after_job['status'],'round':after_job['round']}))


if __name__=='__main__':run()
