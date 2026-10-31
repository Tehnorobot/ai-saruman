"""HTTP boundaries for the compact dashboard, with temporary data only."""
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from common import db


class DashboardNavigationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        for context in (patch.object(db,'DB',root/'lab.sqlite'),
                        patch.dict(os.environ,{'LAB_PROJECTS':str(root/'projects')}),
                        patch.object(main,'PROJECTS',root/'projects'),
                        patch.object(main,'ARTIFACTS',root/'artifacts')):
            context.start();self.addCleanup(context.stop)
        self.pid,self.jid=db.create_project('Dashboard test','spec.md',b'# Mod\n\n## Behavior\n\n- Attack hostile mobs.\n- Stop when disabled.')
        self.client=TestClient(main.app)

    def test_summary_preserves_verifier_counts_without_returning_spec_or_mutating_job(self):
        db.update(self.jid,status='FINISHED',phase='max_rounds_reached',round=5)
        before=db.job_row(self.jid)
        response=self.client.get('/api/jobs/'+self.jid+'/acceptance-summary')
        self.assertEqual(response.status_code,200,response.text)
        summary=response.json()
        self.assertGreater(summary['summary']['total'],0)
        self.assertEqual(summary['summary']['test_passed'],0)
        self.assertNotIn('requirements',summary)
        self.assertNotIn('tasks',summary)
        self.assertEqual(db.job_row(self.jid),before)
        full=self.client.get('/api/jobs/'+self.jid+'/acceptance').json()
        self.assertEqual(summary['summary'],full['summary'])
        self.assertEqual(summary['task_summary'],full['task_summary'])

    def test_summary_unknown_job_and_static_asset_types(self):
        self.assertEqual(self.client.get('/api/jobs/'+'f'*14+'/acceptance-summary').status_code,404)
        for path,kind in (('/dashboard.js','application/javascript'),('/dashboard.css','text/css')):
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn(kind,response.headers['content-type'])
