"""Independent dispatch/history boundaries after removing the two-digit round ceiling."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import daemon
from common import history


class LargeRoundIdentifiersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mailbox = self.root / 'mailbox'
        self.projects = self.root / 'projects'
        self.artifacts = self.root / 'artifacts'
        self.pid, self.jid = 'a' * 14, 'b' * 14
        (self.projects / self.pid / 'workspace').mkdir(parents=True)
        for name in ('requests', 'running', 'logs', 'results', 'cleanup-failed'):
            (self.mailbox / name).mkdir(parents=True)

    def tid(self, number, modern=True):
        return self.jid + f'-round{number:02d}' + ('-' + 'c' * 32 if modern else '')

    def request(self, tid):
        return {'id': tid, 'job_id': self.jid, 'project_id': self.pid,
                'prompt': 'Actual synthetic dispatch text', 'model': '', 'reasoning_effort': 'default'}

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_dispatch_round_hundred_and_thousand_reaches_executor_without_codex(self):
        for number in (100, 1000, 2**53 - 1):
            for modern in (True, False):
                with self.subTest(number=number, modern=modern):
                    tid = self.tid(number, modern)
                    source = self.mailbox / 'running' / (tid + '.json')
                    self.write(source, self.request(tid))
                    with patch.object(daemon, 'PROJECTS', self.projects), \
                         patch.object(daemon, 'install_skills'), \
                         patch.object(daemon, '_run_task', return_value='synthetic executor') as executor:
                        self.assertEqual(daemon.run_task(source), 'synthetic executor')
                    executor.assert_called_once_with(source)

    def test_cleanup_marker_round_hundred_acknowledges_only_new_namespace(self):
        tid = self.tid(100)
        marker = self.mailbox / 'cleanup-failed' / (tid + '.json')
        result = {'id': tid, 'status': 'ERROR', 'error': 'original cleanup error'}
        self.write(marker, {'pid_namespace': 111, 'result': result})
        self.write(self.mailbox / 'running' / (tid + '.json'), self.request(tid))
        with patch.object(daemon, 'ROOT', self.mailbox), patch.object(daemon, 'pid_namespace', return_value=111):
            with self.assertRaises(daemon.TaskCleanupFailed):
                daemon.acknowledge_restarted_cleanup()
        self.assertTrue(marker.exists())
        with patch.object(daemon, 'ROOT', self.mailbox), patch.object(daemon, 'pid_namespace', return_value=222):
            daemon.acknowledge_restarted_cleanup()
        self.assertFalse(marker.exists())
        self.assertFalse((self.mailbox / 'running' / (tid + '.json')).exists())
        self.assertEqual(json.loads((self.mailbox / 'results' / (tid + '.json')).read_text()), result)

    def test_modern_and_legacy_task_archive_list_and_read_after_two_digits(self):
        for number in (100, 1000):
            for modern in (True, False):
                tid = self.tid(number, modern)
                event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': tid}}
                stage = self.artifacts / self.pid / self.jid / f'round-{number:02d}'
                self.write(self.mailbox / 'requests' / (tid + '.json'), self.request(tid))
                (self.mailbox / 'logs' / (tid + '.jsonl')).write_text(json.dumps(event) + '\n')
                history.archive_attempt(stage, self.mailbox, self.pid, self.jid, tid,
                                        result={'id': tid, 'status': 'PASS'})
                # The durable archive must work after transport retirement.
                (self.mailbox / 'requests' / (tid + '.json')).unlink()
                (self.mailbox / 'logs' / (tid + '.jsonl')).unlink()
                listed = history.list_attempts(self.artifacts, self.mailbox, self.pid, self.jid)['attempts']
                self.assertIn(tid, [item['id'] for item in listed])
                item = next(item for item in listed if item['id'] == tid)
                self.assertEqual(item['round'], number)
                detail = history.read_attempt(self.artifacts, self.mailbox, self.pid, self.jid, tid)
                self.assertEqual(detail['request']['prompt'], self.request(tid)['prompt'])
                self.assertEqual(detail['events'][0]['data'], event)

    def test_legacy_codex_log_round_hundred_and_thousand_are_distinct(self):
        for number in (100, 1000):
            stage = self.artifacts / self.pid / self.jid / f'round-{number:02d}'
            stage.mkdir(parents=True)
            event = {'type': 'turn.completed', 'usage': {'input_tokens': number}}
            (stage / 'codex.jsonl').write_text(json.dumps(event) + '\n')
        listed = history.list_attempts(self.artifacts, self.mailbox, self.pid, self.jid)['attempts']
        self.assertEqual([(item['id'], item['round']) for item in listed],
                         [('legacy-round100', 100), ('legacy-round1000', 1000)])
        for number in (100, 1000):
            detail = history.read_attempt(self.artifacts, self.mailbox, self.pid, self.jid,
                                          'legacy-round' + str(number))
            self.assertIsNone(detail['request'])
            self.assertEqual(detail['events'][0]['data']['usage']['input_tokens'], number)

    def test_history_rejects_traversal_unicode_and_oversized_round_identifiers(self):
        for tid in ('../outside', self.jid + '-round100/../../outside',
                    self.jid + '-round' + '1' * 17,
                    self.jid + '-round\u0661\u0660\u0660'):
            with self.subTest(tid=tid):
                with self.assertRaises(ValueError):
                    history.read_attempt(self.artifacts, self.mailbox, self.pid, self.jid, tid)
                with self.assertRaises(ValueError):
                    history.archive_attempt(self.artifacts / 'round-100', self.mailbox,
                                            self.pid, self.jid, tid, request=self.request(tid))

    def test_dispatch_rejects_oversized_or_unicode_round_before_execution(self):
        for suffix in ('1' * 17, '\u0661\u0660\u0660'):
            tid = self.jid + '-round' + suffix
            source = self.mailbox / 'running' / (tid + '.json')
            self.write(source, self.request(tid))
            with patch.object(daemon, 'PROJECTS', self.projects), patch.object(daemon, '_run_task') as executor:
                with self.assertRaises(ValueError):
                    daemon.run_task(source)
            executor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
