"""Independent history boundary and persistence checks, without model calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from common import db
from common.history import list_attempts, read_attempt
from app import main as dashboard
from worker import engine


class AgentHistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.artifacts = self.root / 'artifacts'
        self.mailbox = self.root / 'agent-tasks'
        self.pid, self.jid = 'a' * 14, 'b' * 14
        self.tid = self.jid + '-round01-' + 'c' * 32
        self.stage = self.artifacts / self.pid / self.jid / 'round-01'
        for directory in ('requests', 'running', 'logs', 'results', 'cancel'):
            (self.mailbox / directory).mkdir(parents=True)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def request(self, tid=None, **extra):
        return {'id': tid or self.tid, 'project_id': self.pid, 'job_id': self.jid,
                'prompt': 'Прочитай ТЗ.\n<script>alert(1)</script>',
                'model': 'example-model', 'reasoning_effort': 'high', **extra}

    def archive(self, tid=None, events=None, **request_extra):
        tid = tid or self.tid
        folder = self.stage / 'agent-history' / tid
        self.write(folder / 'request.json', self.request(tid, **request_extra))
        self.write(folder / 'result.json', {'id': tid, 'status': 'PASS'})
        if events is not None:
            (folder / 'events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n'
                                                       for e in events), encoding='utf-8')
        return folder

    def listing(self):
        return list_attempts(self.artifacts, self.mailbox, self.pid, self.jid)

    def detail(self, tid=None, **kwargs):
        return read_attempt(self.artifacts, self.mailbox, self.pid, self.jid,
                            tid or self.tid, **kwargs)

    def assert_not_exposed(self, tid=None):
        tid = tid or self.tid
        self.assertNotIn(tid, [x['id'] for x in self.listing()['attempts']])
        try:
            detail = self.detail(tid)
        except (FileNotFoundError, ValueError):
            return
        self.assertIsNone(detail.get('request'))
        self.assertEqual(detail.get('events'), [])

    def symlink(self, path, target, directory=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('Host does not allow symlink creation: ' + str(exc))

    def test_archived_request_events_and_status_remain_available(self):
        event = {'type': 'item.completed', 'item': {'id': 'i1', 'type': 'agent_message',
                                                  'text': 'Ответ <img src=x onerror=alert(1)>'}}
        self.archive(events=[event])
        metadata = self.listing()['attempts']
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0]['id'], self.tid)
        self.assertTrue(metadata[0]['prompt_available'])
        self.assertEqual(metadata[0]['status'], 'PASS')
        detail = self.detail()
        self.assertEqual(detail['request']['prompt'], self.request()['prompt'])
        self.assertEqual(detail['events'][0]['data'], event)
        self.assertFalse(detail['has_more'])

    def test_live_running_request_uses_actual_saved_prompt_and_settings(self):
        self.write(self.mailbox / 'running' / (self.tid + '.json'), self.request())
        event = {'type': 'thread.started', 'thread_id': 'public-thread-id'}
        (self.mailbox / 'logs' / (self.tid + '.jsonl')).write_text(json.dumps(event) + '\n')
        self.assertIn(self.tid, [x['id'] for x in self.listing()['attempts']])
        detail = self.detail()
        self.assertEqual(detail['request']['model'], 'example-model')
        self.assertEqual(detail['events'][0]['data'], event)

    def test_live_log_progress_is_visible_when_archive_has_older_snapshot(self):
        first = {'type': 'turn.started'}
        second = {'type': 'turn.completed'}
        self.archive(events=[first])
        self.write(self.mailbox / 'running' / (self.tid + '.json'), self.request())
        (self.mailbox / 'logs' / (self.tid + '.jsonl')).write_text(
            json.dumps(first) + '\n' + json.dumps(second) + '\n')
        observed = [x['data'] for x in self.detail()['events']]
        self.assertEqual(observed, [first, second])

    def test_legacy_round_log_available_without_invented_prompt(self):
        self.stage.mkdir(parents=True)
        event = {'type': 'turn.completed', 'usage': {'input_tokens': 42}}
        (self.stage / 'codex.jsonl').write_text(json.dumps(event) + '\n')
        tid = 'legacy-round01'
        listing = self.listing()['attempts']
        self.assertEqual([x['id'] for x in listing], [tid])
        self.assertFalse(listing[0]['prompt_available'])
        detail = self.detail(tid)
        self.assertIsNone(detail['request'])
        self.assertEqual(detail['events'][0]['data'], event)

    def test_pagination_has_no_loss_or_duplicates(self):
        events = [{'type': 'item.completed', 'item': {'id': str(i), 'type': 'agent_message',
                                                     'text': 'текст' * (i + 1)}} for i in range(7)]
        self.archive(events=events)
        offset, stream = 0, ''
        observed = []
        for _ in range(12):
            page = self.detail(after=offset, limit=2, stream=stream)
            self.assertLessEqual(len(page['events']), 2)
            observed.extend(x['data'] for x in page['events'])
            if not page['has_more']:
                break
            self.assertGreater(page['next_offset'], offset)
            offset, stream = page['next_offset'], page['stream']
        else:
            self.fail('History pagination never ended')
        self.assertEqual(observed, events)

    def test_partial_json_line_waits_then_is_delivered_once(self):
        folder = self.archive()
        first = {'type': 'turn.started'}
        second = {'type': 'turn.completed'}
        path = folder / 'events.jsonl'
        partial = json.dumps(second)[:8]
        path.write_text(json.dumps(first) + '\n' + partial)
        page = self.detail()
        self.assertEqual([e['data'] for e in page['events']], [first])
        self.assertTrue(page['pending_line'])
        with path.open('a') as log:
            log.write(json.dumps(second)[8:] + '\n')
        page2 = self.detail(after=page['next_offset'], stream=page['stream'])
        self.assertEqual([e['data'] for e in page2['events']], [second])

    def test_malformed_plaintext_and_nonobject_json_do_not_hide_later_events(self):
        folder = self.archive()
        last = {'type': 'turn.completed'}
        (folder / 'events.jsonl').write_text('executor warning\n{broken}\n[]\nnull\n' + json.dumps(last) + '\n')
        page = self.detail()
        self.assertTrue(any(x['data'] == last for x in page['events']))
        self.assertLessEqual(len(page['events']), 100)

    def test_excessively_nested_log_record_does_not_break_history(self):
        folder = self.archive()
        last = {'type': 'turn.completed'}
        nested = '[' * 2000 + '0' + ']' * 2000
        (folder / 'events.jsonl').write_text(nested + '\n' + json.dumps(last) + '\n')
        page = self.detail()
        self.assertTrue(any(x['data'] == last for x in page['events']))

    def test_collaboration_event_keeps_exported_child_ids_and_request(self):
        event = {'type': 'item.completed', 'item': {'type': 'collab_tool_call',
                 'sender_thread_id': 'parent-session', 'receiver_thread_ids': ['child-session'],
                 'tool': 'spawn_agent', 'prompt': 'Review project independently'}}
        self.archive(events=[{'type': 'thread.started', 'thread_id': 'parent-session'}, event])
        event_detail = self.detail()['events'][-1]
        self.assertIn('child-session', event_detail['agents'])
        self.assertEqual(event_detail['data']['item']['prompt'], 'Review project independently')
        self.assertEqual(event_detail['agent'], 'main')

    def test_private_structured_fields_and_reasoning_are_not_exposed(self):
        hidden = {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'PRIVATE_REASONING'}}
        public = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Public answer',
                  'nested': {'access_token': 'PRIVATE_TOKEN', 'encrypted_content': 'PRIVATE_BLOB'}}}
        self.archive(events=[hidden, public])
        events = self.detail()['events']
        rendered = json.dumps(events)
        self.assertIn('Public answer', rendered)
        for private in ('PRIVATE_REASONING', 'PRIVATE_TOKEN', 'PRIVATE_BLOB'):
            self.assertNotIn(private, rendered)

    def test_oversized_line_keeps_response_bounded_and_later_event_reachable(self):
        folder = self.archive()
        final = {'type': 'turn.completed'}
        (folder / 'events.jsonl').write_text('x' * (2 * 1024 * 1024) + '\n' + json.dumps(final) + '\n')
        offset, stream = 0, ''
        found = False
        for _ in range(20):
            page = self.detail(after=offset, limit=2, stream=stream)
            self.assertLess(len(json.dumps(page)), 1024 * 1024)
            found = found or any(event['data'] == final for event in page['events'])
            if not page['has_more']:
                break
            self.assertGreater(page['next_offset'], offset)
            offset, stream = page['next_offset'], page['stream']
        self.assertTrue(found, 'An oversized log line hid later complete events')

    def test_cross_job_mailbox_request_is_not_exposed(self):
        foreign = self.request(job_id='d' * 14)
        self.write(self.mailbox / 'running' / (self.tid + '.json'), foreign)
        (self.mailbox / 'logs' / (self.tid + '.jsonl')).write_text('PRIVATE OTHER JOB\n')
        self.assert_not_exposed()

    def test_cross_project_archive_request_is_not_exposed(self):
        self.archive(project_id='d' * 14, events=[{'type': 'private-foreign'}])
        self.assert_not_exposed()

    def test_invalid_identifiers_rejected_before_path_access(self):
        for pid, jid, tid in ((self.pid, self.jid, '../secrets'),
                              ('../outside', self.jid, self.tid),
                              (self.pid, '../outside', self.tid),
                              (self.pid, self.jid, ('d' * 14) + '-round01'),
                              (self.pid, self.jid, self.tid + '/request.json')):
            with self.subTest(pid=pid, jid=jid, tid=tid), self.assertRaises(ValueError):
                read_attempt(self.artifacts, self.mailbox, pid, jid, tid)

    def test_unknown_attempt_does_not_use_arbitrary_mailbox_log(self):
        (self.mailbox / 'logs' / (self.tid + '.jsonl')).write_text('orphan log\n')
        self.assert_not_exposed()

    def test_archive_request_symlink_never_exposes_target(self):
        folder = self.stage / 'agent-history' / self.tid
        secret = self.root / 'private.json'
        self.write(secret, self.request(prompt='SECRET'))
        self.symlink(folder / 'request.json', secret)
        try:
            detail = self.detail()
        except (FileNotFoundError, ValueError):
            return
        self.assertNotIn('SECRET', json.dumps(detail))

    def test_archive_log_symlink_never_exposes_target(self):
        folder = self.archive()
        secret = self.root / 'private.jsonl'
        secret.write_text('{"type":"SECRET"}\n')
        self.symlink(folder / 'events.jsonl', secret)
        detail = self.detail()
        self.assertNotIn('SECRET', json.dumps(detail))
        self.assertEqual(detail['events'], [])

    def test_mailbox_directory_symlink_never_exposes_target(self):
        target = self.root / 'foreign-requests'
        self.write(target / (self.tid + '.json'), self.request(prompt='SECRET'))
        (self.mailbox / 'requests').rmdir()
        self.symlink(self.mailbox / 'requests', target, directory=True)
        self.assert_not_exposed()

    def test_archive_parent_symlink_never_exposes_target(self):
        outside = self.root / 'outside'
        folder = outside / 'agent-history' / self.tid
        self.write(folder / 'request.json', self.request(prompt='SECRET'))
        self.symlink(self.stage, outside, directory=True)
        self.assert_not_exposed()


class WorkerHistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.mailbox = self.root / 'mailbox'
        self.projects = self.root / 'projects'
        for directory in ('requests', 'running', 'logs', 'results', 'cancel'):
            (self.mailbox / directory).mkdir(parents=True)
        env = patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects), 'AGENT_TASKS': str(self.mailbox)})
        env.start()
        self.addCleanup(env.stop)
        state = patch.object(db, 'DB', self.root / 'state.sqlite')
        state.start()
        self.addCleanup(state.stop)
        self.pid, self.jid = db.create_project('History', 'spec.md', b'# A meaningful test specification')
        db.claim('history-verifier')
        self.stage = self.root / 'artifacts' / self.pid / self.jid / 'round-01'
        self.logfile = self.stage / 'codex.jsonl'

    def archived(self):
        return sorted((self.stage / 'agent-history').glob('*'))

    def test_failed_retry_preserves_both_original_requests_logs_and_results(self):
        seen = []
        def complete(_delay):
            request = next((self.mailbox / 'requests').glob('*.json'))
            payload = json.loads(request.read_text())
            tid = payload['id']
            seen.append(tid)
            request.rename(self.mailbox / 'running' / request.name)
            (self.mailbox / 'logs' / (tid + '.jsonl')).write_text(json.dumps({'type': 'turn.completed', 'attempt': len(seen)}) + '\n')
            engine.write_json(self.mailbox / 'results' / (tid + '.json'),
                              {'id': tid, 'status': 'FAIL' if len(seen) == 1 else 'PASS'})
        with patch.object(engine.time, 'sleep', complete):
            first = engine.request_agent(self.jid, self.pid, 1, 'original first prompt', self.logfile)
            second = engine.request_agent(self.jid, self.pid, 1, 'original second prompt', self.logfile)
        self.assertEqual((first['status'], second['status']), ('FAIL', 'PASS'))
        self.assertEqual(len(set(seen)), 2)
        self.assertEqual(len(self.archived()), 2)
        for index, tid in enumerate(seen, 1):
            folder = self.stage / 'agent-history' / tid
            request = json.loads((folder / 'request.json').read_text())
            self.assertEqual(request['prompt'], 'original ' + ('first' if index == 1 else 'second') + ' prompt')
            self.assertEqual(json.loads((folder / 'result.json').read_text())['id'], tid)
            self.assertEqual(json.loads((folder / 'events.jsonl').read_text())['attempt'], index)
        engine.retire_attempt(self.stage)
        self.assertEqual(len(self.archived()), 2)

    def test_pending_cancellation_preserves_request_before_mailbox_removal(self):
        db.update(self.jid, pause_requested=1)
        result = engine.request_agent(self.jid, self.pid, 1, 'paused pending prompt', self.logfile)
        self.assertEqual(result['status'], 'USER_PAUSED')
        self.assertEqual(list((self.mailbox / 'requests').glob('*.json')), [])
        folders = self.archived()
        self.assertEqual(len(folders), 1)
        self.assertEqual(json.loads((folders[0] / 'request.json').read_text())['prompt'], 'paused pending prompt')

    def test_recovery_preserves_actual_request_not_regenerated_prompt(self):
        class Crash(BaseException):
            pass
        with patch.object(engine.time, 'sleep', side_effect=Crash):
            with self.assertRaises(Crash):
                engine.request_agent(self.jid, self.pid, 1, 'actually dispatched prompt', self.logfile)
        pending = next((self.mailbox / 'requests').glob('*.json'))
        tid = json.loads(pending.read_text())['id']
        def complete(_delay):
            engine.write_json(self.mailbox / 'results' / (tid + '.json'), {'id': tid, 'status': 'PASS'})
        with patch.object(engine.time, 'sleep', complete):
            engine.request_agent(self.jid, self.pid, 1, 'must not appear as historical prompt', self.logfile)
        archive = self.stage / 'agent-history' / tid / 'request.json'
        self.assertEqual(json.loads(archive.read_text())['prompt'], 'actually dispatched prompt')

    def test_replacement_controller_cancellation_archives_before_retiring_attempt(self):
        class Crash(BaseException):
            pass
        with patch.object(engine.time, 'sleep', side_effect=Crash):
            with self.assertRaises(Crash):
                engine.request_agent(self.jid, self.pid, 1, 'request before recovery', self.logfile)
        pending = next((self.mailbox / 'requests').glob('*.json'))
        tid = json.loads(pending.read_text())['id']
        pending.rename(self.mailbox / 'running' / pending.name)
        (self.mailbox / 'logs' / (tid + '.jsonl')).write_text('{"type":"turn.started"}\n')
        db.update(self.jid, pause_requested=1)
        def acknowledge(_delay):
            self.assertTrue((self.mailbox / 'cancel' / tid).exists())
            engine.write_json(self.mailbox / 'results' / (tid + '.json'), {'id': tid, 'status': 'CANCELLED'})
        with patch.object(engine.time, 'sleep', acknowledge):
            engine.cancel_agent_tasks(self.jid, self.stage.parent)
        folder = self.stage / 'agent-history' / tid
        self.assertEqual(json.loads((folder / 'request.json').read_text())['prompt'], 'request before recovery')
        self.assertEqual(json.loads((folder / 'result.json').read_text())['status'], 'CANCELLED')
        self.assertEqual(json.loads((folder / 'events.jsonl').read_text())['type'], 'turn.started')
        self.assertFalse((self.stage / 'agent_attempt.json').exists())
        self.assertFalse((self.mailbox / 'running' / (tid + '.json')).exists())


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.mailbox, self.artifacts = self.root / 'mailbox', self.root / 'artifacts'
        env = patch.dict(os.environ, {'LAB_PROJECTS': str(self.root / 'projects')})
        env.start()
        self.addCleanup(env.stop)
        for owner, name, value in ((db, 'DB', self.root / 'lab.sqlite'),
                                   (dashboard, 'ARTIFACTS', self.artifacts),
                                   (dashboard, 'AGENT_TASKS', self.mailbox)):
            context = patch.object(owner, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.pid, self.jid = db.create_project('API test', 'spec.md', b'# A meaningful test specification')
        self.tid = self.jid + '-round01-' + 'f' * 32
        running = self.mailbox / 'running'
        running.mkdir(parents=True)
        (running / (self.tid + '.json')).write_text(json.dumps({
            'id': self.tid, 'project_id': self.pid, 'job_id': self.jid,
            'prompt': 'The actual request <script>alert(1)</script>'}))
        self.client = TestClient(dashboard.app)
        self.path = '/api/jobs/' + self.jid + '/agent-history'

    def test_api_reads_actual_prompt_and_does_not_change_job(self):
        before = db.job_row(self.jid)
        listing = self.client.get(self.path)
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()['attempts'][0]['id'], self.tid)
        detail = self.client.get(self.path + '/' + self.tid)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn('The actual request', detail.json()['request']['prompt'])
        self.assertEqual(db.job_row(self.jid), before)

    def test_unknown_job_attempt_and_bad_cursors_have_explicit_errors(self):
        self.assertEqual(self.client.get('/api/jobs/' + ('e' * 14) + '/agent-history').status_code, 404)
        self.assertEqual(self.client.get(self.path + '/' + self.jid + '-round02').status_code, 404)
        for query in ('?after=-1', '?limit=0', '?limit=201', '?after=not-a-number'):
            response = self.client.get(self.path + '/' + self.tid + query)
            self.assertIn(response.status_code, (400, 422), response.text)

    def test_another_jobs_attempt_cannot_be_selected(self):
        foreign_tid = ('e' * 14) + '-round01-' + ('f' * 32)
        response = self.client.get(self.path + '/' + foreign_tid)
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
