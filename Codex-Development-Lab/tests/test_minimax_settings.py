"""MiniMax Explore selections survive role/global settings without implicit fallback."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from common import db,provider_settings as settings


class MiniMaxSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        for context in (patch.object(db,'DB',root/'db.sqlite'),
                        patch.dict(os.environ,{'LAB_PROJECTS':str(root/'projects')})):
            context.start();self.addCleanup(context.stop)
        self.client=TestClient(main.app)

    def test_catalogue_exposes_only_documented_explore_model_and_efforts(self):
        response=self.client.get('/api/providers');self.assertEqual(response.status_code,200)
        data=response.json();self.assertIn('minimax',data['providers'])
        self.assertEqual([r['id'] for r in data['catalogue']['minimax']],['MiniMax-M3.1-Flash'])
        entry=data['catalogue']['minimax'][0]
        self.assertEqual(entry['efforts'],['default','low','medium','high','xhigh','max'])
        self.assertEqual(entry['default_effort'],'max')
        self.assertTrue(entry['thinking_required'])

    def test_all_efforts_validate_and_unsupported_model_or_effort_rejected(self):
        for effort in settings.MINIMAX_EFFORTS:
            self.assertEqual(settings.validate_settings('minimax','MiniMax-M3.1-Flash',effort)[:3],
                             ('minimax','MiniMax-M3.1-Flash',effort))
        for model,effort in [('MiniMax-M3.1-Flash','none'),('MiniMax-M3.1-Flash','ultra'),
                             ('MiniMax-M3.1-Flash','minimal'),('MiniMax-M3','max'),
                             ('MiniMax-M3.1-Flash-Preview','high'),('claude-sonnet-5-5','high')]:
            with self.subTest(model=model,effort=effort),self.assertRaises(ValueError):
                settings.validate_settings('minimax',model,effort)

    def test_new_project_can_use_minimax_without_changing_other_projects(self):
        old=db.create_project('Existing','spec.md',b'# Preserve existing project')[1]
        before=db.job_row(old)
        response=self.client.post('/api/projects',files={'spec':('spec.md',b'# New mod\nImplement tree planting.')},
            data={'provider':'minimax','model':'MiniMax-M3.1-Flash','reasoning_effort':'high'})
        self.assertEqual(response.status_code,200,response.text)
        job=db.job_row(response.json()['job_id'])
        self.assertEqual(settings.resolve_role(job,'coder')['provider'],'minimax')
        self.assertEqual(db.job_row(old),before)

    def test_role_selection_is_persistent_explicit_and_does_not_resume_job(self):
        jid=db.create_project('Roles','spec.md',b'# Implement mod')[1]
        db.update(jid,status='USER_PAUSED')
        roles={'planner':{'provider':'minimax','model':'MiniMax-M3.1-Flash','reasoning_effort':'medium'},
               'coder':{'provider':'codex','model':'gpt-6.1-sol','reasoning_effort':'medium'}}
        self.assertTrue(db.set_job_role_models(jid,roles));db.init()
        self.assertEqual(settings.resolve_role(db.job_row(jid),'planner'),roles['planner'])
        self.assertEqual(settings.resolve_role(db.job_row(jid),'coder'),roles['coder'])
        self.assertEqual(db.job_row(jid)['status'],'USER_PAUSED')
        db.update(jid,status='RUNNING');self.assertFalse(db.set_job_role_models(jid,roles))

    def test_provider_key_only_reaches_gateway_in_compose(self):
        import re
        path=Path('compose.yaml')
        if not path.is_file():self.skipTest('Compose source is not packaged in service image')
        text=path.read_text(encoding='utf-8')
        parts=re.split(r'^  ([a-z][a-z0-9-]+):\s*$',text,flags=re.MULTILINE)
        found=[]
        for i in range(1,len(parts),2):
            if 'MINIMAX_API_KEY:' in parts[i+1]:found.append(parts[i])
        self.assertEqual(found,['provider-gateway'])
