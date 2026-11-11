"""Refresh advisory checkpoint navigation for one paused job; never run a model.

The caller checks service/job idleness. This maintenance step additionally holds
the project lock and verifies that sources, accepted plan and original evidence
remain unchanged. Execute inside a service image with project/artifact mounts.
"""
import argparse
import hashlib
import json
from pathlib import Path

from common import acceptance
from common.memory_archive import read_json
from common.project_lock import project_lock
from worker.pipeline import Cycle


class InspectionEngine:
    def poll_job(self, _):
        return None


class InspectionCycle(Cycle):
    def _usage(self):
        # Maintenance neither needs provider credentials nor makes paid calls.
        return None


def run(args):
    jobs = json.loads(args.jobs.read_text(encoding='utf-8'))
    assert all(j['status'] in ('USER_PAUSED', 'FAILED', 'STOPPED', 'FINISHED') for j in jobs)
    job = next(j for j in jobs if j['id'] == args.job_id)
    assert job['status'] == 'USER_PAUSED'
    workspace = Path('/projects') / job['project_id'] / 'workspace'
    output = Path('/artifacts') / job['project_id'] / job['id']
    with project_lock(workspace.parent):
        state = read_json(output, 'pipeline/state.json')
        number = (state.get('decision') or {}).get('scope', {}).get('round', job['round'] + 1)
        stage = output / f'round-{number:02d}'
        files = [output / 'pipeline' / name for name in
                 ('catalog.json', 'graph.json', 'manifest.json', 'latest-result.json')]
        files += [output / 'latest_acceptance.json', output / 'latest_verification.json']
        files += [stage / 'pipeline' / name for name in ('plan/complete.json', 'coding-state.json')]
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
        source = acceptance.source_fingerprint(workspace)
        cycle = InspectionCycle(InspectionEngine(), job, workspace, output, number)
        catalog = read_json(output, 'pipeline/catalog.json')
        graph = read_json(output, 'pipeline/graph.json')
        summary, reference = cycle.refresh_condition_progress(catalog, graph)
        cycle.save()
        reminders = read_json(output, 'pipeline/unresolved-work.json')
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
        assert before == after, 'Accepted work or original verification changed'
        assert acceptance.source_fingerprint(workspace) == source, 'Product sources changed'
        print(json.dumps({'job_id': job['id'], 'status': job['status'], 'completed_round': job['round'],
                          'working_round': number, 'progress_ref': reference,
                          'reminders': [{k: r[k] for k in ('reminder_id', 'scenario_id', 'checkpoint_id',
                              'condition_ids', 'scheduled_condition_ids', 'unscheduled_condition_ids')}
                                        for r in reminders['reminders']],
                          'accepted_plan_and_evidence_unchanged': True, 'product_source_unchanged': True,
                          'model_invoked': False, 'jobs_resumed': False}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--jobs', type=Path, default=Path('/input/jobs.json'))
    run(parser.parse_args())
