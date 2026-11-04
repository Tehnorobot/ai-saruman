"""Independent activity-card checks using synthetic data; no model/game calls."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main
from common import db


class ActivityHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mailbox = self.root / 'mailbox'
        for folder in ('running', 'requests', 'logs', 'results'):
            (self.mailbox / folder).mkdir(parents=True)
        for context in (patch.dict(os.environ, {'LAB_PROJECTS': str(self.root / 'projects')}),
                        patch.object(db, 'DB', self.root / 'lab.sqlite'),
                        patch.object(main, 'AGENT_TASKS', self.mailbox),
                        patch.object(main, 'ARTIFACTS', self.root / 'artifacts')):
            context.start()
            self.addCleanup(context.stop)
        self.pid, self.jid = db.create_project('Activity fixture', 'spec.md', b'# Activity testing specification')
        self.tid = self.jid + '-round03-' + '1' * 32
        self.log = self.mailbox / 'logs' / (self.tid + '.jsonl')
        self.request = self.mailbox / 'running' / (self.tid + '.json')
        self.client = TestClient(main.app)
        self.base = '/api/jobs/' + self.jid + '/activity'

    def get(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def running(self, phase='codex_development'):
        db.update(self.jid, status='RUNNING', phase=phase, round=2, lease_until=int(time.time()) + 600)

    def command(self, command, *, kind='item.started', private=''):
        self.request.write_text(json.dumps({'id': self.tid, 'job_id': self.jid,
                                             'project_id': self.pid, 'prompt': 'PRIVATE PROMPT'}), encoding='utf-8')
        self.log.write_text(json.dumps({'type': kind, 'item': {
            'id': 'x', 'type': 'command_execution', 'command': command,
            'aggregated_output': private, 'encrypted_reasoning': private}}) + '\n', encoding='utf-8')

    def test_unknown_job_returns_404(self):
        self.assertEqual(self.client.get('/api/jobs/' + 'f' * 14 + '/activity').status_code, 404)

    def test_memory_and_completed_request_override_stale_coder_label(self):
        self.running()
        root=self.root/'artifacts'/self.pid/self.jid/'pipeline'
        root.mkdir(parents=True)
        state={'operation':{'status':'running'},'service_operation':{
            'stage':'memory_sync','active':True,'round':3,'completed':17,'total':20}}
        (root/'state.json').write_text(json.dumps(state))
        result=self.get()
        self.assertEqual(result['stage'],'memory_sync')
        self.assertIn('17 из 20',result['detail'])
        state['service_operation']['active']=False
        state['operation']={'status':'processing_result','executor_status':'FAIL'}
        (root/'state.json').write_text(json.dumps(state))
        self.assertEqual(self.get()['stage'],'processing_agent_result')
        state['operation']={'status':'retrying','retry_category':'rate_limit'}
        (root/'state.json').write_text(json.dumps(state))
        self.assertEqual(self.get()['stage'],'provider_wait')
        db.update(self.jid,status='USER_PAUSED',phase='interrupted')
        self.assertEqual(self.get()['stage'],'paused')

    def test_activity_read_is_readonly_and_has_observation_contract(self):
        before = db.job_row(self.jid)
        first = self.get()
        self.assertEqual(db.job_row(self.jid), before)
        for field in ('job_id', 'status', 'phase', 'stage', 'title', 'detail', 'started_at',
                      'observed_at', 'current_round', 'max_rounds', 'source', 'stale'):
            self.assertIn(field, first)
        self.assertEqual(first['job_id'], self.jid)
        self.assertEqual(first['status'], 'QUEUED')

    def test_verifying_does_not_pretend_to_know_exact_game_process(self):
        self.running('verifying')
        value = self.get()
        self.assertEqual(value['title'], 'Сборка и независимые тесты')
        self.assertNotIn('Тестирует в Minecraft', value['title'])

    def test_terminal_and_pause_reasons_are_distinct(self):
        seen = []
        for status, phase in (('USER_PAUSED', 'interrupted'), ('USER_PAUSED', 'auth_required'),
                              ('QUOTA_PAUSED', 'quota_wait'), ('FAILED', 'worker_exception'),
                              ('STOPPED', 'interrupted'), ('FINISHED', 'max_rounds_reached')):
            with self.subTest(status=status, phase=phase):
                db.update(self.jid, status=status, phase=phase)
                value = self.get()
                self.assertEqual(value['status'], status)
                seen.append(value['title'])
                self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertEqual(len(set(seen)), len(seen))

    def test_unknown_running_phase_is_honest(self):
        self.running('future_phase_unrecognized')
        value = self.get()
        self.assertNotEqual(value['title'], 'Пишет код и исправляет ошибки')
        self.assertNotEqual(value['title'], 'Сборка и независимые тесты')
        self.assertIn('не', (value['title'] + ' ' + value['detail']).lower())

    def test_private_reasoning_prompt_command_and_output_are_not_echoed(self):
        self.running()
        self.command('cat ТЗ.md; echo TOP_SECRET_COMMAND', private='TOP_SECRET_OUTPUT')
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'type': 'item.completed', 'item': {
                'type': 'reasoning', 'text': 'TOP_SECRET_REASONING'}}) + '\n')
        encoded = json.dumps(self.get(), ensure_ascii=False)
        for secret in ('PRIVATE PROMPT', 'TOP_SECRET_COMMAND', 'TOP_SECRET_OUTPUT', 'TOP_SECRET_REASONING'):
            self.assertNotIn(secret, encoded)

    def test_malformed_cli_log_does_not_break_activity(self):
        self.running()
        self.command('cat ТЗ.md')
        self.log.write_bytes(b'not json\n' + b'{"type":"item.started","item":')
        value = self.get()
        self.assertEqual(value['status'], 'RUNNING')
        self.assertEqual(value['stage'], 'developing')

    def test_running_log_symlink_is_not_followed(self):
        self.running()
        self.command('cat ТЗ.md')
        private = self.root / 'private.jsonl'
        private.write_text(self.log.read_text(encoding='utf-8'), encoding='utf-8')
        self.log.unlink()
        try:
            self.log.symlink_to(private)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('Symlink creation is not permitted on this host: ' + str(exc))
        value = self.get()
        self.assertEqual(value['stage'], 'developing')

    def test_current_command_classification_is_explicitly_observed_and_not_output(self):
        self.running()
        for command, stage in (('cat ТЗ.md', 'reading_spec'),
                               ('cat acceptance-plan.json', 'acceptance_planning'),
                               ('./gradlew test', 'agent_checks'),
                               ('./gradlew runClientGameTest', 'agent_minecraft')):
            with self.subTest(command=command):
                self.command(command)
                value = self.get()
                self.assertEqual(value['stage'], stage)
                self.assertEqual(value['current_round'], 3)
                self.assertNotIn(command, value['detail'])

    def test_wrong_round_and_wrong_project_logs_are_not_current_activity(self):
        self.running()
        self.command('cat ТЗ.md')
        db.update(self.jid, round=3)
        self.assertEqual(self.get()['stage'], 'developing')
        db.update(self.jid, round=2)
        request = json.loads(self.request.read_text(encoding='utf-8'))
        request['project_id'] = 'f' * 14
        self.request.write_text(json.dumps(request), encoding='utf-8')
        self.assertEqual(self.get()['stage'], 'developing')

    def test_old_command_is_not_reported_as_current_work(self):
        self.running()
        self.command('cat ТЗ.md')
        previous = time.time() - 300
        os.utime(self.log, (previous, previous))
        value = self.get()
        self.assertEqual(value['stage'], 'developing')
        self.assertTrue(value['stale'])

    def test_heartbeat_timestamp_is_never_used_as_phase_start(self):
        self.running()
        value = self.get()
        self.assertIsNone(value['started_at'])
        start = '2026-01-01T12:00:00+00:00'
        with db.connect() as con:
            con.execute('INSERT INTO events(job_id,created_at,level,text) VALUES(?,?,?,?)',
                        (self.jid, start, 'info', 'Раунд 3/5: Codex создаёт или исправляет код; сохранённый проект'))
            con.execute('UPDATE jobs SET updated_at=? WHERE id=?', ('2026-01-01T12:01:30+00:00', self.jid))
        self.assertEqual(self.get()['started_at'], start)

    def test_expired_controller_lease_is_recovery_not_active_coding(self):
        self.running()
        self.command('cat ТЗ.md')
        db.update(self.jid, lease_until=int(time.time()) - 1)
        self.assertEqual(self.get()['stage'], 'recovering')

    def test_claims_in_messages_outputs_and_reasoning_are_not_runtime_proof(self):
        self.running()
        self.command('echo harmless', private='./gradlew runClientGameTest')
        with self.log.open('a', encoding='utf-8') as handle:
            for item_type in ('agent_message', 'reasoning'):
                handle.write(json.dumps({'type': 'item.completed', 'item': {
                    'type': item_type, 'text': 'Сейчас тестирую в Minecraft: ./gradlew runClientGameTest'}}) + '\n')
        self.assertEqual(self.get()['stage'], 'developing')

    def test_completed_command_is_not_presented_as_still_running(self):
        self.running()
        self.command('./gradlew runClientGameTest')
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'type': 'item.completed', 'item': {
                'id': 'x', 'type': 'command_execution', 'command': './gradlew runClientGameTest',
                'exit_code': 0}}) + '\n')
        self.assertEqual(self.get()['stage'], 'developing')

    def test_tail_activity_remains_usable_when_old_log_exceeds_scan_budget(self):
        self.running()
        self.command('cat ТЗ.md')
        latest = self.log.read_bytes()
        self.log.write_bytes(b'{"type":"irrelevant"}\n' * 15000 + latest)
        self.assertEqual(self.get()['stage'], 'reading_spec')

    def test_nonobject_and_oversized_events_do_not_break_or_classify_activity(self):
        self.running()
        self.command('cat ТЗ.md')
        for data in (b'[]\nnull\n"text"\n', b'{"type":"item.started","item":[]}\n',
                     b'{"type":"item.started","item":{"type":"command_execution","command":' +
                     json.dumps('cat ТЗ.md ' + 'x' * (256 * 1024)).encode() + b'}}\n'):
            with self.subTest(length=len(data)):
                self.log.write_bytes(data)
                self.assertEqual(self.get()['stage'], 'developing')

    def test_request_symlink_cannot_provide_current_attempt_identity(self):
        self.running()
        self.command('cat ТЗ.md')
        foreign = self.root / 'outside-request.json'
        foreign.write_bytes(self.request.read_bytes())
        self.request.unlink()
        try:
            self.request.symlink_to(foreign)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('Symlink creation is not permitted on this host: ' + str(exc))
        self.assertEqual(self.get()['stage'], 'developing')


if __name__ == '__main__':
    unittest.main()
