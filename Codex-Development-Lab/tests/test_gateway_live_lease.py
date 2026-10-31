"""Renewal preserves authorization scope, billing, and terminal revocation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from provider_gateway.main import Store, create_app


class GatewayLiveLeaseTests(unittest.TestCase):
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
        self.registration = {'task_id': 'renewable-review', 'job_id': 'a' * 14,
            'project_id': 'b' * 14, 'provider': 'dslab', 'role': 'reviewer',
            'model': 'claude-sonnet-5-5', 'token_budget': 50000,
            'max_output_tokens': 2000, 'max_calls': 40, 'ttl_seconds': 60}
        with patch('provider_gateway.main.time.time', return_value=1000):
            response = self.client.post('/internal/tasks', headers=self.control, json=self.registration)
        self.assertEqual(response.status_code, 200, response.text)
        self.cap = response.json()['capability']
        self.path = '/internal/tasks/renewable-review/renew'
        self.body = {key: self.registration[key] for key in ('job_id', 'project_id')}
        self.body['ttl_seconds'] = 3600

    def renew(self, **changes):
        with patch('provider_gateway.main.time.time', return_value=1020):
            return self.client.post(self.path, headers=self.control, json={**self.body, **changes})

    def test_live_lease_can_outlast_original_deadline_without_resetting_budget_or_usage(self):
        with self.store.connect() as conn:
            conn.execute('INSERT INTO requests(request_id,task_id,job_id,model,started_at,status,'
                         'input_tokens,output_tokens,billing_unknown) VALUES(?,?,?,?,?,?,?,?,?)',
                         ('charged', self.registration['task_id'], self.registration['job_id'],
                          self.registration['model'], 1010, 'completed', 1200, 300, 0))
        before = self.store.task(self.cap)
        totals = self.store.totals(self.registration['task_id'])
        response = self.renew()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['expires'], 4620)
        after = self.store.task(self.cap)
        self.assertEqual({k: v for k, v in before.items() if k != 'expires'},
                         {k: v for k, v in after.items() if k != 'expires'})
        self.assertEqual(self.store.totals(self.registration['task_id']), totals)
        self.assertNotIn(self.cap, json.dumps(response.json()))
        # Renewal remains valid after the original lease would have expired.
        with patch('provider_gateway.main.time.time', return_value=1100):
            again = self.client.post(self.path, headers=self.control, json=self.body)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['expires'], 4700)

    def test_renewal_never_shortens_a_live_lease(self):
        with self.store.connect() as conn:
            conn.execute('UPDATE tasks SET expires=9000')
        self.assertEqual(self.renew(ttl_seconds=60).json()['expires'], 9000)

    def test_revoked_and_expired_leases_cannot_be_resurrected(self):
        for expires in (0, 1019, 1020):
            with self.subTest(expires=expires), self.store.connect() as conn:
                conn.execute('UPDATE tasks SET expires=?', (expires,))
            self.assertEqual(self.renew().status_code, 409)
            self.assertEqual(self.store.task(self.cap)['expires'], expires)

    def test_wrong_scope_missing_task_and_invalid_lease_are_rejected(self):
        for changes in ({'job_id': 'c' * 14}, {'project_id': 'c' * 14}):
            self.assertEqual(self.renew(**changes).status_code, 409)
        for ttl in (0, 59, 604801, True, '3600', 3600.5):
            with self.subTest(ttl=ttl):
                self.assertEqual(self.renew(ttl_seconds=ttl).status_code, 400)
        response = self.client.post('/internal/tasks/missing/renew', headers=self.control, json=self.body)
        self.assertEqual(response.status_code, 404)

    def test_agent_capability_cannot_renew_itself(self):
        for headers in ({}, {'authorization': 'Bearer ' + self.cap}):
            response = self.client.post(self.path, headers=headers, json=self.body)
            self.assertEqual(response.status_code, 401)

    def test_revoke_after_renewal_stays_terminal(self):
        self.assertEqual(self.renew().status_code, 200)
        self.client.post('/internal/tasks/renewable-review/revoke', headers=self.control)
        self.assertEqual(self.renew().status_code, 409)
        self.assertEqual(self.store.task(self.cap)['expires'], 0)


if __name__ == '__main__':
    unittest.main()
