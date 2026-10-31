"""Independent continuation regressions; temporary state, no model/game/Engine calls."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main as dashboard
from common import db
from worker import engine


class ExtendRoundsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = self.root / 'projects'
        self.artifacts = self.root / 'artifacts'
        for owner, name, value in ((db, 'DB', self.root / 'state.sqlite'),
                                   (dashboard, 'PROJECTS', self.projects),
                                   (dashboard, 'ARTIFACTS', self.artifacts),
                                   (engine, 'WORKSPACE_ROOT', self.projects),
                                   (engine, 'ARTIFACTS', self.artifacts)):
            context = patch.object(owner, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects),
                                         'AGENT_TASKS': str(self.root / 'mailbox'),
                                         'BACKUP_REQUESTS': str(self.root / 'backups')})
        context.start()
        self.addCleanup(context.stop)
        db.init()
        self.client = TestClient(dashboard.app)
        self.pid, self.jid = db.create_project('Continuation regression', 'original.md',
            b'# Original requirements\nKeep all existing implementation and history.',
            max_rounds=5, model='example-model', reasoning_effort='high')
        db.update(self.jid, status='FINISHED', phase='max_rounds_reached', round=5)
        self.workspace = self.projects / self.pid / 'workspace'
        self.output = self.artifacts / self.pid / self.jid
        self.output.mkdir(parents=True)
        self.sentinel = self.workspace / 'existing-code.txt'
        self.sentinel.write_bytes(b'original implementation\x00\xff')
        self.old_report = {'status': 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',
                           'checks': [{'label': 'existing_test', 'status': 'PASS'}]}
        self.stage5 = self.output / 'round-05'
        self.stage5.mkdir()
        (self.stage5 / 'verification_complete.json').write_text(json.dumps(self.old_report))
        (self.stage5 / 'codex.jsonl').write_bytes(b'{"original":"history"}\n')
        (self.output / 'latest_verification.json').write_text(json.dumps(self.old_report))

    def extend(self, amount=5, jid=None):
        return self.client.post('/api/jobs/' + (jid or self.jid) + '/extend-rounds',
                                data={'additional_rounds': str(amount)})

    def test_finished_five_of_five_queues_five_of_ten_preserving_identity_and_files(self):
        original = db.job_row(self.jid)
        events = db.events_list(self.jid)
        before_files = {p.relative_to(self.root): p.read_bytes()
                        for directory in (self.projects, self.artifacts)
                        for p in directory.rglob('*') if p.is_file()}
        response = self.extend()
        self.assertEqual(response.status_code, 200, response.text)
        row = db.job_row(self.jid)
        self.assertEqual((row['status'], row['round'], row['max_rounds']), ('QUEUED', 5, 10))
        for name in ('id', 'project_id', 'model', 'reasoning_effort', 'created_at'):
            self.assertEqual(row[name], original[name], name)
        self.assertEqual(row['resume_generation'], original['resume_generation'] + 1)
        self.assertEqual((row['cancel_requested'], row['pause_requested'], row['lease_until']), (0, 0, 0))
        self.assertIsNone(row['worker'])
        self.assertEqual(db.events_list(self.jid)[:len(events)], events)
        self.assertEqual(len(db.events_list(self.jid)), len(events) + 1)
        for path, value in before_files.items():
            self.assertEqual((self.root / path).read_bytes(), value, str(path))
        self.assertEqual(len(db.jobs_list()), 1)
        db.init()  # Restart/migration must retain the larger budget.
        self.assertEqual(db.job_row(self.jid)['max_rounds'], 10)

    def test_addition_beyond_old_twenty_round_ceiling_is_supported(self):
        response = self.extend(195)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(db.job_row(self.jid)['max_rounds'], 200)

    def test_invalid_amounts_are_400_and_leave_state_and_events_unchanged(self):
        before, events = db.job_row(self.jid), db.events_list(self.jid)
        for amount in (0, -1, 2**53):
            with self.subTest(amount=amount):
                response = self.extend(amount)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(db.job_row(self.jid), before)
                self.assertEqual(db.events_list(self.jid), events)

    def test_exceeding_saved_total_limit_is_409_without_mutation(self):
        before, events = db.job_row(self.jid), db.events_list(self.jid)
        response = self.extend((2**53 - 1) - 4)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(db.job_row(self.jid), before)
        self.assertEqual(db.events_list(self.jid), events)

    def test_malformed_form_is_rejected_without_mutation(self):
        before = db.job_row(self.jid)
        for payload in ({}, {'additional_rounds': 'five'}, {'additional_rounds': '1.5'}):
            response = self.client.post('/api/jobs/' + self.jid + '/extend-rounds', data=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(db.job_row(self.jid), before)

    def test_inconsistent_or_uncleared_terminal_state_is_409(self):
        for fields in ({'round': 4}, {'worker': 'not-cleared-worker'}, {'cancel_requested': 1}):
            with self.subTest(fields=fields):
                db.update(self.jid, round=5, worker=None, cancel_requested=0)
                db.update(self.jid, **fields)
                before = db.job_row(self.jid)
                self.assertEqual(self.extend().status_code, 409)
                self.assertEqual(db.job_row(self.jid), before)

    def test_unknown_job_is_404(self):
        self.assertEqual(self.extend(jid='absent-job').status_code, 404)

    def test_only_finished_limit_reached_jobs_can_be_extended(self):
        for status, phase in (('QUEUED', 'queued'), ('RUNNING', 'verifying'),
                              ('USER_PAUSED', 'interrupted'), ('FAILED', 'agent_error'),
                              ('STOPPED', 'interrupted'), ('FINISHED', 'accepted')):
            with self.subTest(status=status, phase=phase):
                db.update(self.jid, status=status, phase=phase)
                before, events = db.job_row(self.jid), db.events_list(self.jid)
                response = self.extend()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(db.job_row(self.jid), before)
                self.assertEqual(db.events_list(self.jid), events)

    def test_sequential_double_submit_adds_rounds_only_once(self):
        self.assertEqual(self.extend().status_code, 200)
        self.assertEqual(self.extend().status_code, 409)
        self.assertEqual(db.job_row(self.jid)['max_rounds'], 10)

    def test_simultaneous_double_submit_has_one_winner(self):
        start = threading.Barrier(3)
        results = []
        def request():
            start.wait(10)
            try:
                results.append(db.extend_job_rounds(self.jid, 5))
            except Exception as exc:
                results.append(exc)
        threads = [threading.Thread(target=request, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait(10)
        for thread in threads:
            thread.join(10)
            self.assertFalse(thread.is_alive(), 'Concurrent extension deadlocked')
        self.assertEqual(sum(isinstance(x, dict) for x in results), 1, results)
        self.assertEqual(sum(isinstance(x, ValueError) for x in results), 1, results)
        self.assertEqual(db.job_row(self.jid)['max_rounds'], 10)

    def test_event_failure_rolls_back_requeue_and_budget(self):
        before, events = db.job_row(self.jid), db.events_list(self.jid)
        with patch.object(db, 'event', side_effect=RuntimeError('synthetic event failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic event failure'):
                db.extend_job_rounds(self.jid, 5)
        self.assertEqual(db.job_row(self.jid), before)
        self.assertEqual(db.events_list(self.jid), events)

    def test_concurrent_claim_cannot_claim_before_extension_commit(self):
        original_connect = db.connect
        attempted, finished = threading.Event(), threading.Event()
        result = []
        @contextmanager
        def observe_connect():
            with original_connect() as con:
                if threading.current_thread().name == 'extension-request':
                    attempted.set()
                yield con
        def extend():
            try:
                result.append(db.extend_job_rounds(self.jid, 5))
            finally:
                finished.set()
        with original_connect() as con:
            con.execute('BEGIN IMMEDIATE')
            with patch.object(db, 'connect', observe_connect):
                thread = threading.Thread(target=extend, name='extension-request', daemon=True)
                thread.start()
                self.assertTrue(attempted.wait(10))
                self.assertFalse(finished.is_set())
                self.assertEqual(con.execute('SELECT status FROM jobs WHERE id=?',
                                            (self.jid,)).fetchone()['status'], 'FINISHED')
                con.commit()
                thread.join(10)
                self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        claimed = db.claim('continuation-worker')
        self.assertEqual((claimed['id'], claimed['round'], claimed['max_rounds']), (self.jid, 5, 10))
        self.assertIsNone(db.claim('duplicate-worker'))

    def test_worker_skips_completed_fifth_round_and_develops_sixth_with_previous_report(self):
        self.assertEqual(self.extend().status_code, 200)
        db.claim('test-only-worker')
        observed = []
        def develop(jid, pid, number, workspace, previous, stage, generation):
            observed.append(('develop', number, previous))
            self.assertEqual((jid, pid, workspace), (self.jid, self.pid, self.workspace))
            self.assertEqual(generation, 1)
            return {'status': 'PASS', 'tail': 'synthetic verifier transport'}
        def verify(workspace, stage, jid):
            observed.append(('verify', int(stage.name[-2:])))
            return {'status': 'INCOMPLETE', 'checks': [{'status': 'NOT_CONFIGURED'}],
                    'acceptance':{'source_sha256':format(int(stage.name[-2:]),'064x')}}
        original_history = (self.stage5 / 'codex.jsonl').read_bytes()
        with patch.object(engine, 'copy_skill_environment', return_value=self.workspace), \
             patch.object(engine.DockerSandbox, 'recover'), \
             patch.object(engine, 'develop_round', side_effect=develop), \
             patch.object(engine, 'request_agent', side_effect=AssertionError('Real agent forbidden')), \
             patch.object(engine, 'checks', side_effect=verify), \
             patch.object(engine, 'snapshot_request'):
            engine.replay_legacy(self.jid)
        self.assertEqual([(item[0], item[1]) for item in observed],
                         [(kind, number) for number in range(6, 11) for kind in ('develop', 'verify')])
        self.assertEqual(observed[0][2], self.old_report)
        self.assertEqual((self.stage5 / 'codex.jsonl').read_bytes(), original_history)
        self.assertEqual(json.loads((self.stage5 / 'verification_complete.json').read_text()), self.old_report)
        row = db.job_row(self.jid)
        self.assertEqual((row['status'], row['round'], row['max_rounds']), ('FINISHED', 10, 10))
        self.assertEqual(self.sentinel.read_bytes(), b'original implementation\x00\xff')


if __name__ == '__main__':
    unittest.main()
