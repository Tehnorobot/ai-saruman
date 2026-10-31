import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import db, provider_settings as settings


class AgentModelTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        for context in (patch.object(db,'DB',root/'db.sqlite'),
                        patch.dict(os.environ,{'LAB_PROJECTS':str(root/'projects')})):
            context.start();self.addCleanup(context.stop)
        self.roles={'planner':{'provider':'litellm','model':settings.LITELLM_MODELS[2],
            'reasoning_effort':'default'},'coder':{'provider':'codex','model':'gpt-6-sol','reasoning_effort':'medium'}}

    def create(self,**kwargs):
        return db.create_project('Role models','spec.md',b'# Requirements\nBuild the specified mod.',**kwargs)[1]

    def test_legacy_jobs_inherit_without_changing_global_selection(self):
        jid=self.create(model='gpt-6-sol',reasoning_effort='high')
        job=db.job_row(jid)
        self.assertEqual(json.loads(job['role_models']),{})
        self.assertEqual(settings.resolve_role(job,'planner'),
            {'provider':'codex','model':'gpt-6-sol','reasoning_effort':'high'})
        self.assertEqual(settings.resolve_role(job,'coder'),settings.resolve_role(job,'planner'))

    def test_roles_persist_across_init_and_old_settings_updates(self):
        jid=self.create(role_models=self.roles)
        db.init()
        self.assertEqual(settings.resolve_role(db.job_row(jid),'planner'),self.roles['planner'])
        self.assertTrue(db.set_job_codex_settings(jid,'gpt-6-sol','high'))
        self.assertEqual(json.loads(db.job_row(jid)['role_models']),self.roles)
        self.assertEqual(settings.resolve_role(db.job_row(jid),'coder'),self.roles['coder'])

    def test_setter_requires_idle_state_and_never_resumes(self):
        jid=self.create()
        for status in ('RUNNING','FINISHED','FAILED','STOPPED'):
            with db.connect() as con:con.execute('UPDATE jobs SET status=? WHERE id=?',(status,jid))
            self.assertFalse(db.set_job_role_models(jid,self.roles))
            self.assertEqual(json.loads(db.job_row(jid)['role_models']),{})
        for status in ('QUEUED','USER_PAUSED','QUOTA_PAUSED'):
            with db.connect() as con:con.execute('UPDATE jobs SET status=? WHERE id=?',(status,jid))
            self.assertTrue(db.set_job_role_models(jid,self.roles))
            self.assertEqual(db.job_row(jid)['status'],status)
        with db.connect() as con:con.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?',(jid,))
        self.assertFalse(db.set_job_role_models(jid,{}))
        self.assertFalse(db.set_job_role_models('unknown',self.roles))

    def test_clear_override_inherits_legacy_and_partial_mapping_is_allowed(self):
        jid=self.create(role_models={'planner':self.roles['planner']})
        self.assertEqual(settings.resolve_role(db.job_row(jid),'coder')['provider'],'codex')
        self.assertTrue(db.set_job_role_models(jid,{}))
        self.assertEqual(settings.resolve_role(db.job_row(jid),'planner')['provider'],'codex')

    def test_exact_remote_names_and_rejected_unsupported_custom_model(self):
        for model in settings.LITELLM_MODELS:
            value=settings.validate_role_models({'planner':{'provider':'litellm','model':model}})
            self.assertEqual(value['planner']['model'],model)
        for model in ('organization/custom-model-v2','DeepSeek-V4-Flash-0731','Qwen3-Next-80B-A3B-Instruct-FP8',
                      'https://api.example/model','../secret','model --flag','a//b','a/../b','', 'x'*129):
            with self.subTest(model=model),self.assertRaises(ValueError):settings.validate_litellm_model(model)

    def test_invalid_roles_providers_fields_types_and_corrupt_storage_rejected(self):
        invalid=[{'unregistered':{}},{'planner':{'provider':'unknown'}},{'planner':{'token':'secret'}},
                 {'planner':{'model':3}},{'coder':{'provider':'dslab','model':'not-claude'}},
                 {'planner':{'provider':'litellm','model':'safe','reasoning_effort':'unlimited'}},[]]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(ValueError):settings.validate_role_models(value)
        with self.assertRaises(ValueError):settings.resolve_role({'role_models':'{bad'},'planner')
        with self.assertRaises(ValueError):settings.resolve_role({},'unregistered')

    def test_reviewer_inherits_coder_with_explicit_origin_until_overridden(self):
        jid=self.create(provider='dslab',model='claude-sonnet-5-5',role_models=self.roles)
        job=db.job_row(jid)
        self.assertEqual(settings.resolve_role(job,'reviewer'),self.roles['coder'])
        details=settings.resolve_role_details(job,'reviewer')
        self.assertTrue(details['inherited'])
        self.assertEqual(details['inherited_from'],'coder')
        selection={'provider':'minimax','model':'MiniMax-M3.1-Flash','reasoning_effort':'low'}
        self.assertTrue(db.set_job_role_models(jid,{**self.roles,'reviewer':selection}))
        job=db.job_row(jid)
        self.assertEqual(settings.resolve_role(job,'reviewer'),selection)
        self.assertFalse(settings.resolve_role_details(job,'reviewer')['inherited'])
        self.assertIsNone(settings.resolve_role_details(job,'reviewer')['inherited_from'])
        self.assertEqual(settings.resolve_role(job,'coder'),self.roles['coder'])

    def test_reviewer_configuration_error_never_falls_back_to_coder(self):
        job={'role_models':{'coder':self.roles['coder'],'reviewer':{
            'provider':'minimax','model':'invalid-model','reasoning_effort':'low'}}}
        with self.assertRaises(ValueError):settings.resolve_role(job,'reviewer')

    def test_reset_reviewer_override_restores_documented_coder_inheritance(self):
        jid=self.create(role_models={**self.roles,'reviewer':self.roles['planner']})
        self.assertTrue(db.set_job_role_models(jid,self.roles))
        self.assertEqual(settings.resolve_role(db.job_row(jid),'reviewer'),self.roles['coder'])
        self.assertEqual(settings.AGENT_ROLES,('planner','coder','reviewer'))

    def test_model_specific_efforts_and_public_catalogue(self):
        for model,efforts in settings.LITELLM_EFFORTS.items():
            for effort in efforts:
                self.assertEqual(settings.validate_role_models({'coder':{'provider':'litellm',
                    'model':model,'reasoning_effort':effort}})['coder']['reasoning_effort'],effort)
        with self.assertRaises(ValueError):
            settings.validate_role_models({'coder':{'provider':'litellm','model':settings.LITELLM_MODELS[1],
                'reasoning_effort':'high'}})
        self.assertEqual(len(settings.model_catalogue()['litellm']),5)

    def test_codex_live_catalogue_and_legacy_reasoning_compatibility(self):
        catalogue=settings.model_catalogue()['codex']
        self.assertEqual(len(catalogue),7)
        for item in catalogue:
            self.assertIn('max',item['efforts'])
            for effort in item['efforts']:
                settings.validate_settings('codex',item['id'],effort)
        for effort in ('none','minimal'):
            with self.assertRaises(ValueError):settings.validate_settings('codex','gpt-6-sol',effort)
            self.assertEqual(settings.resolve_role({'model':'gpt-6-sol','reasoning_effort':effort},'coder')['reasoning_effort'],effort)
        with self.assertRaises(ValueError):settings.validate_settings('codex','gpt-6-luna','ultra')
        self.assertEqual(settings.validate_settings('codex','custom-model','minimal')[2],'minimal')

    def test_litellm_legacy_effort_normalizes_and_claude_model_defaults_resolve(self):
        for effort in ('none','high','max'):
            with self.assertRaises(ValueError):settings.validate_settings('litellm',settings.LITELLM_MODELS[0],effort)
            job={'role_models':{'coder':{'provider':'litellm','model':settings.LITELLM_MODELS[0],'reasoning_effort':effort}}}
            self.assertEqual(settings.resolve_role(job,'coder')['reasoning_effort'],'default')
        for model,expected in settings.DSLAB_DEFAULT_EFFORTS.items():
            self.assertEqual(settings.resolve_role({'provider':'dslab','model':model,'reasoning_effort':'default'},'planner')['reasoning_effort'],expected)


if __name__=='__main__':unittest.main()
