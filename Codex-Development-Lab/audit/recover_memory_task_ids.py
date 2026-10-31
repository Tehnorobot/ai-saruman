"""Commit the already executed round-13 result; never run models or tests."""
import json
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from common import acceptance, pipeline_memory
from common.project_lock import project_lock
from worker import pipeline

root = Path(__file__).resolve().parents[1]
jid = 'fd124aec0a1647'
project = root / 'projects/66b5a30bdb6945'
output = root / 'artifacts/66b5a30bdb6945' / jid
ws = project / 'workspace'
stage = output / 'round-13'

with project_lock(project):
    with sqlite3.connect('file:' + (root / 'data/lab.sqlite3').as_posix() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
    assert job['status'] in ('FAILED', 'USER_PAUSED'), 'Job must remain idle'
    result = pipeline.read_json(stage, 'pipeline-verification-result.json')
    session = pipeline.read_json(stage, 'pipeline/session.json')
    ready = pipeline.read_json(stage, 'pipeline/development-ready.json')
    assert result['scope']['source_revision'] == acceptance.source_fingerprint(ws) == ready['source_revision']
    assert result['scope']['spec_revision'] == session['scope']['spec_revision']
    assert result['scope']['spec_revision'] == hashlib.sha256(acceptance.read_spec(project).encode()).hexdigest()
    assert result['scope']['project_id'] == job['project_id'] and result['scope']['round'] == 13
    assert (stage/'pipeline/budget.json').is_file(), 'Recovery must not contact usage gateway'
    index = pipeline.read_json(output, 'pipeline-memory/index.json')
    assert not any(x['kind'] == 'verification' and x['scope'] == result['scope'] for x in index['records']), 'Result already committed'
    assert not (output/'pipeline/memory-task-ids-recovery-before.json').exists(), 'Keep original recovery backup'
    # Preserve the entire pre-recovery status and immutable result provenance.
    pipeline.write_json(output, 'pipeline/memory-task-ids-recovery-before.json', {
        'state': pipeline.read_json(output, 'pipeline/state.json'),
        'public': pipeline.read_json(output, 'pipeline/public.json'),
        'result': result})
    cycle = pipeline.Cycle(SimpleNamespace(poll_job=lambda _: None, job_update=lambda *a, **k: None), job, ws, output, 13)
    cycle.record_verification(session['catalog'], ready['manifest'], session['decision'],
                              result, pipeline.read_json(stage, 'verification.json'))
    assert pipeline_memory.read_payload(output, cycle.state['last_verification_record']) == result
    cycle.publish('waiting', catalog=session['catalog'], graph=session['graph'],
                  decision=session['decision'], review=ready['review'],
                  reason='Saved independent round-13 result recovered after memory indexing fix; continue explicitly.')
    report = {'restored': True, 'round': 13, 'models_invoked': False, 'tests_rerun': False,
              'job_status_unchanged': job['status'], 'record_id': cycle.state['last_verification_record']}
    pipeline.write_json(root, 'audit/memory-task-ids-v1-recovery.json', report)
    print(json.dumps(report))
