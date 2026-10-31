"""Offline request-history contracts against a real SQLite gateway store."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from provider_gateway.main import Store, create_app


class RequestHistoryGatewayTests(unittest.TestCase):
    job_id = '89cd3a16ad2c4e'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / 'usage.sqlite3')
        config = patch.dict(os.environ, {'DSLAB_CONTROL_TOKEN': 'control-test-credential-123456789012345'})
        config.start()
        self.addCleanup(config.stop)
        self.client = TestClient(create_app(store=self.store))
        self.addCleanup(self.client.close)
        self.control = {'authorization': 'Bearer ' + os.environ['DSLAB_CONTROL_TOKEN']}
        self.path = '/internal/jobs/' + self.job_id + '/requests'

    def seed(self, count):
        with self.store.connect() as conn:
            for provider, role in (('dslab', 'planner'), ('minimax', 'coder'), ('litellm', 'reviewer')):
                conn.execute('INSERT OR IGNORE INTO task_roles VALUES(?,?)', (provider, role))
                conn.execute('INSERT OR IGNORE INTO task_providers VALUES(?,?)', (provider, provider))
            for index in range(count):
                # Repeated, backwards and wildly future clocks must all be irrelevant.
                timestamp = (1000, 1000, 10**10, 900 - index)[index % 4]
                conn.execute(
                    'INSERT INTO requests(request_id,task_id,job_id,model,started_at,status,'
                    'input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,'
                    'estimated_rub,diagnostic_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('request-' + str(index), ('dslab', 'minimax', 'litellm')[index % 3],
                     self.job_id, 'test-model', timestamp, 'connecting', 60, 20, 10, 30, 1.25,
                     '{"message":"redacted diagnostic"}'))

    def page(self, **params):
        response = self.client.get(self.path, headers=self.control, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_every_request_beyond_legacy_cap_is_reachable_in_insertion_order(self):
        self.seed(237)
        collected = []
        page = self.page()
        self.assertEqual(len(page['requests']), 50)
        while True:
            self.assertTrue(page['available'])
            self.assertEqual(page['job_id'], self.job_id)
            collected.extend(page['requests'])
            if page['next_cursor'] is None:
                break
            self.assertEqual(page['next_cursor'], page['requests'][-1]['sequence'])
            page = self.page(cursor=page['next_cursor'])
        self.assertEqual([r['request_id'] for r in collected],
                         ['request-' + str(i) for i in reversed(range(237))])
        self.assertEqual([r['sequence'] for r in collected], list(range(237, 0, -1)))
        self.assertEqual(len({r['request_id'] for r in collected}), 237)
        legacy = self.store.job_usage(self.job_id)
        self.assertEqual(legacy['totals']['requests'], 237)
        self.assertEqual(len(legacy['requests']), 200)
        self.assertEqual([r['sequence'] for r in legacy['requests']], list(range(237, 37, -1)))

    def test_status_updates_and_new_insert_cannot_move_rows_across_cursor(self):
        self.seed(6)
        first = self.page(limit=2)
        with self.store.connect() as conn:
            conn.execute('UPDATE requests SET started_at=?,finished_at=?,status=? WHERE request_id=?',
                         (10**11, 1, 'completed', 'request-0'))
            conn.execute('UPDATE requests SET status=? WHERE request_id=?', ('streaming', 'request-3'))
            conn.execute('INSERT INTO requests(request_id,task_id,job_id,model,started_at,status) '
                         'VALUES(?,?,?,?,?,?)', ('new', 'dslab', self.job_id, 'test', 0, 'connecting'))
        second = self.page(limit=200, cursor=first['next_cursor'])
        self.assertEqual([r['request_id'] for r in second['requests']],
                         ['request-3', 'request-2', 'request-1', 'request-0'])
        self.assertEqual(second['requests'][0]['status'], 'streaming')
        self.assertEqual(second['requests'][-1]['status'], 'completed')
        self.assertIsNone(second['next_cursor'])
        self.assertEqual(self.page(limit=1)['requests'][0]['request_id'], 'new')

    def test_public_metadata_metrics_cost_and_legacy_defaults(self):
        self.seed(3)
        with self.store.connect() as conn:
            conn.execute('DELETE FROM task_providers WHERE task_id=?', ('dslab',))
            conn.execute('DELETE FROM task_roles WHERE task_id=?', ('dslab',))
            # A neighboring job must not leak through either joined metadata or paging.
            conn.execute('INSERT INTO requests(request_id,task_id,job_id,model,started_at,status) '
                         'VALUES(?,?,?,?,?,?)', ('other-job', 'dslab', 'aaaaaaaaaaaaaa', 'test', 1, 'completed'))
        rows = self.page()['requests']
        self.assertEqual([r['provider'] for r in rows], ['litellm', 'minimax', 'dslab'])
        self.assertEqual([r['role'] for r in rows], ['reviewer', 'coder', 'unknown'])
        self.assertEqual([r['estimated_rub'] for r in rows], [None, None, 1.25])
        self.assertEqual([r['cost_available'] for r in rows], [False, False, True])
        for row in rows:
            self.assertEqual(row['input_tokens_total'], 100)
            self.assertEqual(row['total_tokens'], 120)
            self.assertEqual(row['saved_input_tokens'], 30)
            self.assertEqual(row['cache_hit_percent'], 30)
            self.assertEqual(row['diagnostic_json'], '{"message":"redacted diagnostic"}')
        payload = json.dumps(rows)
        self.assertNotIn('capability', payload)
        self.assertNotIn(os.environ['DSLAB_CONTROL_TOKEN'], payload)

    def test_authentication_and_pagination_validation(self):
        self.assertEqual(self.client.get(self.path).status_code, 401)
        self.assertEqual(self.client.get(self.path, headers={'authorization': 'Bearer task-capability'}).status_code, 401)
        for params in ({'limit': 0}, {'limit': 201}, {'limit': '1.5'}, {'limit': 'abc'},
                       {'cursor': 0}, {'cursor': -1}, {'cursor': '1.5'},
                       {'cursor': '9223372036854775808'}, {'cursor': '1 OR 1=1'}):
            with self.subTest(params=params):
                response = self.client.get(self.path, headers=self.control, params=params)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/internal/jobs/invalid/requests', headers=self.control).status_code, 400)

    def test_empty_and_exact_page_boundary(self):
        self.assertEqual(self.page(), {'available': True, 'job_id': self.job_id,
                                      'requests': [], 'next_cursor': None})
        self.seed(200)
        page = self.page(limit=200)
        self.assertEqual(len(page['requests']), 200)
        self.assertIsNone(page['next_cursor'])
        self.assertEqual(self.page(cursor=1)['requests'], [])


if __name__ == '__main__':
    unittest.main()
