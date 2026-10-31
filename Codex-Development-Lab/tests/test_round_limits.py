"""Independent user-controlled round-budget tests; no model, Engine, or game calls."""
from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

from common import db
from worker import engine
from tests import test_extend_rounds as fixtures


class RoundLimitTests(unittest.TestCase):
    setUp = fixtures.ExtendRoundsTests.setUp

    def limit(self, value, jid=None):
        return self.client.post('/api/jobs/' + (jid or self.jid) + '/round-limit',
                                data={'max_rounds': str(value)})

    def test_finished_can_change_to_unlimited_without_starting_model_or_job(self):
        before = db.job_row(self.jid)
        response = self.limit(0)
        self.assertEqual(response.status_code, 200, response.text)
        row = db.job_row(self.jid)
        self.assertEqual((row['status'], row['phase'], row['round'], row['max_rounds']),
                         ('USER_PAUSED', 'round_limit_updated', 5, 0))
        for key in ('id', 'project_id', 'model', 'reasoning_effort', 'resume_generation'):
            self.assertEqual(row[key], before[key])
        self.assertEqual(self.sentinel.read_bytes(), b'original implementation\x00\xff')
        self.assertEqual(json.loads((self.stage5 / 'verification_complete.json').read_text()), self.old_report)
        self.assertIsNone(db.claim('must-not-start-yet'))
        db.init()
        self.assertEqual(db.job_row(self.jid)['max_rounds'], 0)
        response = self.client.post('/api/jobs/' + self.jid + '/resume')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(db.job_row(self.jid)['status'], 'QUEUED')
        self.assertEqual(db.claim('test-worker')['id'], self.jid)

    def test_finite_limit_is_not_capped_at_twenty_and_can_be_reduced_above_current_round(self):
        for value in (1000, 2**53 - 1, 6):
            response = self.limit(value)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(db.job_row(self.jid)['max_rounds'], value)
            self.assertEqual(db.job_row(self.jid)['status'], 'USER_PAUSED')

    def test_negative_or_unrepresentable_limits_do_not_mutate_state(self):
        before, events = db.job_row(self.jid), db.events_list(self.jid)
        for value in (-1, 2**53, 10**100):
            with self.subTest(value=value):
                response = self.limit(value)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(db.job_row(self.jid), before)
                self.assertEqual(db.events_list(self.jid), events)

    def test_finite_limit_must_exceed_current_round(self):
        before, events = db.job_row(self.jid), db.events_list(self.jid)
        for value in (1, 4, 5):
            self.assertEqual(self.limit(value).status_code, 409)
            self.assertEqual(db.job_row(self.jid), before)
            self.assertEqual(db.events_list(self.jid), events)

    def test_only_idle_nonterminal_or_exhausted_jobs_allow_limit_editing(self):
        for status, phase in (('RUNNING', 'verifying'),
                              ('STOPPED', 'interrupted'), ('FINISHED', 'accepted')):
            with self.subTest(status=status):
                db.update(self.jid, status=status, phase=phase)
                before, events = db.job_row(self.jid), db.events_list(self.jid)
                self.assertEqual(self.limit(0).status_code, 409)
                self.assertEqual(db.job_row(self.jid), before)
                self.assertEqual(db.events_list(self.jid), events)
        for status in ('QUEUED', 'USER_PAUSED', 'QUOTA_PAUSED', 'FAILED'):
            with self.subTest(status=status):
                db.update(self.jid, status=status, phase='idle')
                self.assertEqual(self.limit(0).status_code, 200)
                self.assertEqual(db.job_row(self.jid)['status'], status)

    def test_unknown_and_malformed_limits(self):
        self.assertEqual(self.limit(0, jid='missing-job').status_code, 404)
        before = db.job_row(self.jid)
        for payload in ({}, {'max_rounds': 'unlimited'}, {'max_rounds': '1.5'}):
            response = self.client.post('/api/jobs/' + self.jid + '/round-limit', data=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(db.job_row(self.jid), before)

    def test_failed_limit_update_preserves_error_and_never_queues_automatically(self):
        db.update(self.jid, status='FAILED', phase='agent_error', last_error='original diagnostic')
        self.assertEqual(self.limit(100).status_code, 200)
        row = db.job_row(self.jid)
        self.assertEqual((row['status'], row['phase'], row['last_error']),
                         ('FAILED', 'agent_error', 'original diagnostic'))
        self.assertIsNone(db.claim('must-not-run-failed-job'))

    def test_uncleared_worker_or_cancel_request_blocks_limit_editing(self):
        for fields in ({'worker': 'not-cleared'}, {'cancel_requested': 1}):
            with self.subTest(fields=fields):
                db.update(self.jid, status='USER_PAUSED', worker=None, cancel_requested=0)
                db.update(self.jid, **fields)
                before, events = db.job_row(self.jid), db.events_list(self.jid)
                self.assertEqual(self.limit(0).status_code, 409)
                self.assertEqual(db.job_row(self.jid), before)
                self.assertEqual(db.events_list(self.jid), events)

    def test_event_failure_rolls_back_budget_and_terminal_transition(self):
        before, events = db.job_row(self.jid), db.events_list(self.jid)
        with patch.object(db, 'event', side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic write failure'):
                db.set_job_round_limit(self.jid, 0)
        self.assertEqual(db.job_row(self.jid), before)
        self.assertEqual(db.events_list(self.jid), events)

    def test_strict_db_validation_rejects_nonintegers_and_accepts_zero(self):
        for value in (True, False, None, '0', 1.5, -1, 2**53):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    db.validate_round_limit(value)
        for value in (0, 1, 21, 2**53 - 1):
            self.assertEqual(db.validate_round_limit(value), value)

    def test_new_project_accepts_unlimited_and_more_than_twenty(self):
        for value in (0, 50):
            response = self.client.post('/api/projects', data={'max_rounds': str(value)},
                files={'spec': ('requirements.md', b'# Requirements\nTest only.', 'text/markdown')})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(db.job_row(response.json()['job_id'])['max_rounds'], value)

    def test_new_project_invalid_limit_is_rejected_before_creating_files_or_jobs(self):
        before_jobs = db.jobs_list()
        before_paths = sorted(str(path) for path in self.projects.rglob('*'))
        for value in (-1, 2**53):
            response = self.client.post('/api/projects', data={'max_rounds': str(value)},
                files={'spec': ('requirements.md', b'# Requirements\nTest only.', 'text/markdown')})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(db.jobs_list(), before_jobs)
            self.assertEqual(sorted(str(path) for path in self.projects.rglob('*')), before_paths)

    def run_synthetic(self, stop_after, action='pause'):
        self.assertEqual(self.limit(0).status_code, 200)
        self.assertEqual(self.client.post('/api/jobs/' + self.jid + '/resume').status_code, 200)
        db.claim('synthetic-only-worker')
        observed = []
        def develop(jid, pid, number, workspace, previous, stage, generation):
            observed.append(('develop', number))
            return {'status': 'PASS', 'tail': 'Synthetic transport only; no gameplay proof'}
        def verify(workspace, stage, jid):
            number = int(stage.name.split('-')[1])
            observed.append(('verify', number))
            if number == stop_after:
                db.control_job(jid, action)
            if number > stop_after:
                raise AssertionError('Worker ignored control request')
            # The limit test simulates progress; unchanged rounds are covered
            # separately by the controller no-progress pause regression.
            return {'status': 'INCOMPLETE', 'checks': [],
                    'acceptance':{'source_sha256':format(number,'064x')}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(engine, 'copy_skill_environment', return_value=self.workspace))
            stack.enter_context(patch.object(engine.DockerSandbox, 'recover'))
            stack.enter_context(patch.object(engine, 'develop_round', side_effect=develop))
            stack.enter_context(patch.object(engine, 'checks', side_effect=verify))
            stack.enter_context(patch.object(engine, 'snapshot_request'))
            stack.enter_context(patch.object(engine, 'request_agent', side_effect=AssertionError('Real agent forbidden')))
            engine.replay_legacy(self.jid)
        return observed

    def test_unlimited_really_runs_beyond_twenty_and_acknowledges_pause(self):
        observed = self.run_synthetic(23)
        self.assertEqual(observed, [(kind, n) for n in range(6, 24) for kind in ('develop', 'verify')])
        row = db.job_row(self.jid)
        self.assertEqual((row['status'], row['round'], row['max_rounds']), ('USER_PAUSED', 23, 0))
        self.assertIsNone(row['worker'])

    def test_unlimited_acknowledges_stop(self):
        observed = self.run_synthetic(6, action='stop')
        self.assertEqual(observed, [('develop', 6), ('verify', 6)])
        self.assertEqual(db.job_row(self.jid)['status'], 'STOPPED')

    def test_unlimited_restart_at_round_twenty_one_skips_completed_checkpoint(self):
        stage = self.output / 'round-21'
        stage.mkdir()
        checkpoint = {'status': 'INCOMPLETE', 'checks': [], 'checkpoint': 'round-21'}
        (stage / 'verification_complete.json').write_text(json.dumps(checkpoint))
        (self.output / 'latest_verification.json').write_text(json.dumps(checkpoint))
        db.update(self.jid, status='USER_PAUSED', phase='verification_interrupted', round=21)
        observed = self.run_synthetic(22)
        self.assertEqual(observed, [('develop', 22), ('verify', 22)])
        self.assertEqual(json.loads((stage / 'verification_complete.json').read_text()), checkpoint)
        self.assertEqual(db.job_row(self.jid)['status'], 'USER_PAUSED')

    def test_checkpoint_ninety_nine_continues_with_round_hundred(self):
        stage = self.output / 'round-99'
        stage.mkdir()
        checkpoint = {'status': 'INCOMPLETE', 'checks': [], 'checkpoint': 'round-99'}
        (stage / 'verification_complete.json').write_text(json.dumps(checkpoint))
        (self.output / 'latest_verification.json').write_text(json.dumps(checkpoint))
        db.update(self.jid, status='USER_PAUSED', phase='verification_interrupted', round=99)
        self.assertEqual(self.run_synthetic(100), [('develop', 100), ('verify', 100)])
        self.assertEqual(json.loads((stage / 'verification_complete.json').read_text()), checkpoint)
        self.assertEqual(db.job_row(self.jid)['round'], 100)


if __name__ == '__main__':
    unittest.main()
