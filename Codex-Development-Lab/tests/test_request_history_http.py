"""Request pagination across providers without model calls or archive I/O on HTTP threads."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from common import db, request_history
from common.provider_client import GatewayError


class RequestHistoryHttpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for context in (patch.object(db, 'DB', self.root/'db.sqlite'),
                        patch.dict(os.environ, {'LAB_PROJECTS': str(self.root/'projects')})):
            context.start()
            self.addCleanup(context.stop)
        self.pid, self.jid = db.create_project('History', 'spec.md', b'# Spec')
        self.client = TestClient(main.app)
        self.rows = [{'request_id': str(i), 'sequence': i, 'provider': 'dslab' if i % 2 else 'anthropic',
                      'role': 'coder', 'started_at': i, 'input_tokens': i} for i in range(275, 0, -1)]
        snapshot_patch = patch.object(main.request_history, 'snapshot', return_value=({}, {
            'codex_loading': False, 'codex_stale': False}))
        self.snapshot = snapshot_patch.start()
        self.addCleanup(snapshot_patch.stop)

    def gateway(self, method, path):
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(path).query)
        limit = int(query['limit'][0])
        before = int(query.get('cursor', ['999999'])[0])
        rows = [r for r in self.rows if r['sequence'] < before]
        return {'available': True, 'requests': rows[:limit],
                'next_cursor': rows[limit-1]['sequence'] if len(rows) > limit else None}

    def get(self, cursor=None, limit=50):
        query = {'limit': limit}
        if cursor is not None:query['cursor'] = cursor
        response = self.client.get('/api/jobs/'+self.jid+'/requests', params=query)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_all_native_rows_page_in_insertion_order_beyond_two_hundred(self):
        with patch.object(main, 'provider_control', side_effect=self.gateway):
            result = self.get()
            rows = result['requests']
            while result['next_cursor']:
                result = self.get(result['next_cursor'])
                rows.extend(result['requests'])
        self.assertEqual([r['sequence'] for r in rows], list(range(275, 0, -1)))
        self.assertEqual({r['provider'] for r in rows}, {'dslab', 'anthropic'})

    def test_cursor_replay_and_concurrent_insert_preserve_page(self):
        with patch.object(main, 'provider_control', side_effect=self.gateway):
            first = self.get()
            cursor = first['next_cursor']
            second = self.get(cursor)
            self.rows.insert(0, {'sequence': 276, 'request_id': 'new', 'started_at': 999})
            self.assertEqual(self.get(cursor), second)
            self.assertEqual(second['requests'][0]['sequence'], 225)

    def test_cli_snapshot_merges_without_truncating_or_reordering_native(self):
        cli = [{'provider': 'codex', 'task_id': str(i), 'started_at': i+.5,
                'request_unit': 'completed_turns'} for i in range(280, 0, -1)]
        self.snapshot.return_value = ({'available': True, 'requests': cli}, {'codex_loading': False})
        # Deliberately move an older inserted row's wall clock far into the future.
        self.rows[1]['started_at'] = 10000
        with patch.object(main, 'provider_control', side_effect=self.gateway):
            result = self.get()
            all_rows = list(result['requests'])
            while result['next_cursor']:
                result = self.get(result['next_cursor'])
                all_rows.extend(result['requests'])
        self.assertEqual(len(all_rows), 555)
        self.assertEqual([r['sequence'] for r in all_rows if 'sequence' in r], list(range(275, 0, -1)))
        self.assertEqual(sum(r['provider'] == 'codex' for r in all_rows), 280)

    def test_cursor_is_job_bound_and_validation_precedes_gateway(self):
        with patch.object(main, 'provider_control', side_effect=self.gateway) as gateway:
            cursor = self.get()['next_cursor']
            _, other = db.create_project('Other', 'spec.md', b'# Other')
            self.assertEqual(self.client.get('/api/jobs/'+other+'/requests', params={'cursor': cursor}).status_code, 400)
            calls = gateway.call_count
            self.assertEqual(self.client.get('/api/jobs/'+self.jid+'/requests?limit=0').status_code, 400)
            self.assertEqual(self.client.get('/api/jobs/'+'f'*14+'/requests').status_code, 404)
            self.assertEqual(gateway.call_count, calls)

    def test_offline_gateway_keeps_cli_visible_and_cursor_retryable(self):
        self.snapshot.return_value = ({'available': True, 'requests': [
            {'provider': 'codex', 'task_id': 'cli', 'started_at': 0}]}, {'codex_loading': False})
        with patch.object(main, 'provider_control', side_effect=GatewayError('offline')):
            first = self.get()
        self.assertEqual(first['requests'][0]['task_id'], 'cli')
        self.assertFalse(first['api_available'])
        with patch.object(main, 'provider_control', side_effect=self.gateway):
            second = self.get(first['next_cursor'])
        self.assertEqual(second['requests'][0]['sequence'], 275)


class BackgroundIndexTests(unittest.TestCase):
    def test_cold_snapshot_and_http_usage_never_wait_for_disk_scan(self):
        entered, release = threading.Event(), threading.Event()
        def scan(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {'available': True, 'requests': []}
        with tempfile.TemporaryDirectory() as root, patch.object(request_history.token_usage, 'codex_usage', side_effect=scan):
            try:
                report, state = request_history.snapshot(root, {'coder': Path(root)/'box'}, 'a'*14, 'b'*14)
                self.assertEqual(report, {})
                self.assertTrue(state['codex_loading'])
                self.assertTrue(entered.wait(1))
                report, state = request_history.snapshot(root, {'coder': Path(root)/'box'}, 'a'*14, 'b'*14)
                self.assertTrue(state['codex_loading'])
            finally:
                release.set()
