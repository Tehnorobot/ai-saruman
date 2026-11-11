"""Operator recording limits persist independently from jobs and provider state."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from common import db, recording_settings


class RecordingSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        override = patch.object(db, 'DB', self.root / 'data/lab.sqlite3')
        override.start()
        self.addCleanup(override.stop)
        self.client = TestClient(main.app)

    def test_default_available_in_fresh_database_without_project_setup(self):
        response = self.client.get('/api/recording-settings')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'max_seconds': 300, 'scope': 'global',
                                          'applies_to': 'next_test_run'})

    def test_large_choice_survives_new_process_and_db_initialization(self):
        seconds = 100_000
        response = self.client.put('/api/recording-settings', json={'max_seconds': seconds})
        self.assertEqual(response.status_code, 200, response.text)
        db.init()
        result = subprocess.run([sys.executable, '-c',
            'from common.recording_settings import max_seconds;print(max_seconds())'],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'LAB_DB': str(db.DB)},
            text=True, capture_output=True, timeout=30, check=True)
        self.assertEqual(result.stdout.strip(), str(seconds))
        self.assertEqual(recording_settings.max_seconds(), seconds)

    def test_value_has_no_old_one_minute_or_thousand_second_ceiling(self):
        for seconds in (61, 120, 3_601, 100_001, 2**63 + 1):
            with self.subTest(seconds=seconds):
                self.assertEqual(recording_settings.set_max_seconds(seconds)['max_seconds'], seconds)
                self.assertEqual(recording_settings.max_seconds(), seconds)

    def test_invalid_limits_and_extra_fields_leave_saved_value_unchanged(self):
        recording_settings.set_max_seconds(120)
        for value in (0, -1, 3.5, True, False, '180', None, [], {}):
            with self.subTest(value=value):
                response = self.client.put('/api/recording-settings', json={'max_seconds': value})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(recording_settings.max_seconds(), 120)
        for value in ({}, {'max_seconds': 180, 'job_id': 'invalid'}):
            response = self.client.put('/api/recording-settings', json=value)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(recording_settings.max_seconds(), 120)

    def test_malformed_or_nonobject_json_leaves_saved_value_unchanged(self):
        recording_settings.set_max_seconds(240)
        for payload in ('{"max_seconds":', 'null', '[]', '3', '"180"'):
            with self.subTest(payload=payload):
                response = self.client.put('/api/recording-settings', content=payload,
                                           headers={'Content-Type': 'application/json'})
                self.assertIn(response.status_code, (400, 422), response.text)
                self.assertEqual(recording_settings.max_seconds(), 240)

    def test_settings_change_preserves_existing_job_rows(self):
        db.init()
        # No worker/model call is needed to store a global recording preference.
        with db.connect() as connection:
            connection.execute('INSERT INTO projects VALUES(?,?,?,?)', ('p', 'existing', 'today', 'T.md'))
            connection.execute('''INSERT INTO jobs(id,project_id,status,phase,round,max_rounds,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)''', ('j', 'p', 'RUNNING', 'coding', 9, 20, 'today', 'today'))
        before = db.job_row('j')
        response = self.client.put('/api/recording-settings', json={'max_seconds': 600})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.job_row('j'), before)


if __name__ == '__main__':
    unittest.main()
