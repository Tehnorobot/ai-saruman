"""Independent HTTP-level incremental history verification; no model or Docker calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from common import db


class HistoryHttpVerification(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mailbox = self.root / 'mailbox'
        self.artifacts = self.root / 'artifacts'
        for directory in ('running', 'requests', 'logs', 'results'):
            (self.mailbox / directory).mkdir(parents=True)
        for context in (patch.dict(os.environ, {'LAB_PROJECTS': str(self.root / 'projects')}),
                        patch.object(db, 'DB', self.root / 'lab.sqlite'),
                        patch.object(main, 'AGENT_TASKS', self.mailbox),
                        patch.object(main, 'ARTIFACTS', self.artifacts)):
            context.start()
            self.addCleanup(context.stop)
        self.pid, self.jid = db.create_project('HTTP history test', 'spec.md', b'# Meaningful self-contained requirements')
        self.tid = self.jid + '-round01-' + '1' * 32
        self.base = '/api/jobs/' + self.jid + '/agent-history'
        self.log = self.mailbox / 'logs' / (self.tid + '.jsonl')
        self.write(self.mailbox / 'running' / (self.tid + '.json'),
                   {'id': self.tid, 'job_id': self.jid, 'project_id': self.pid, 'prompt': 'Exactly dispatched prompt'})
        self.client = TestClient(main.app)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def events(self, values):
        self.log.write_text(''.join(json.dumps(value) + '\n' for value in values), encoding='utf-8')

    def get(self, tid=None, **params):
        response = self.client.get(self.base + '/' + (tid or self.tid), params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_http_pagination_and_live_append_deliver_each_complete_event_once(self):
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': str(index)}} for index in range(4)]
        self.events(events[:3])
        first = self.get(limit=2)
        self.assertTrue(first['has_more'])
        self.assertEqual([e['data'] for e in first['events']], events[:2])
        second = self.get(after=first['next_offset'], stream=first['stream'], limit=2)
        self.assertEqual([e['data'] for e in second['events']], events[2:3])
        self.assertFalse(second['has_more'])
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(events[3])[:12])
        partial = self.get(after=second['next_offset'], stream=second['stream'])
        self.assertTrue(partial['pending_line'])
        self.assertEqual(partial['events'], [])
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(events[3])[12:] + '\n')
        appended = self.get(after=partial['next_offset'], stream=partial['stream'])
        self.assertEqual([e['data'] for e in appended['events']], events[3:])
        self.assertEqual(appended['request']['prompt'], 'Exactly dispatched prompt')
        self.assertFalse(appended['reset'])

    def test_http_stream_replacement_resets_stale_cursor(self):
        self.events([{'type': 'thread.started', 'thread_id': 'old'}, {'type': 'turn.completed'}])
        first = self.get()
        replacement = self.log.with_suffix('.replacement')
        replacement.write_text('{"type":"thread.started","thread_id":"new"}\n', encoding='utf-8')
        replacement.replace(self.log)
        second = self.get(after=first['next_offset'], stream=first['stream'])
        self.assertTrue(second['reset'])
        self.assertNotEqual(second['stream'], first['stream'])
        self.assertEqual(second['events'][0]['data']['thread_id'], 'new')

    def test_http_legacy_log_is_readable_without_fabricated_prompt(self):
        legacy = self.artifacts / self.pid / self.jid / 'round-02' / 'codex.jsonl'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"type":"turn.completed","usage":{"input_tokens":7}}\n', encoding='utf-8')
        listing = self.client.get(self.base).json()
        self.assertIn('legacy-round02', [attempt['id'] for attempt in listing['attempts']])
        detail = self.get('legacy-round02')
        self.assertIsNone(detail['request'])
        self.assertFalse(detail['attempt']['prompt_available'])
        self.assertEqual(detail['events'][0]['data']['usage']['input_tokens'], 7)

    def test_http_another_project_cannot_read_existing_attempt_or_mutate_queue(self):
        before = db.job_row(self.jid)
        other_pid, other_jid = db.create_project('Other project', 'spec.md', b'# A different project specification')
        self.assertNotEqual(other_pid, self.pid)
        response = self.client.get('/api/jobs/' + other_jid + '/agent-history/' + self.tid)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Exactly dispatched prompt', response.text)
        # A correctly named task whose ownership says another project is hidden.
        self.write(self.mailbox / 'running' / (self.tid + '.json'),
                   {'id': self.tid, 'job_id': self.jid, 'project_id': other_pid, 'prompt': 'FOREIGN SECRET'})
        self.events([{'type': 'message', 'text': 'FOREIGN SECRET'}])
        listing = self.client.get(self.base)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()['attempts'], [])
        hidden = self.client.get(self.base + '/' + self.tid)
        self.assertEqual(hidden.status_code, 404)
        self.assertNotIn('FOREIGN SECRET', hidden.text)
        self.assertEqual(db.job_row(self.jid), before)


if __name__ == '__main__':
    unittest.main()
