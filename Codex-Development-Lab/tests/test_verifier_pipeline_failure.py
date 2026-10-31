"""A broken image-owned verifier must not dispatch another paid coding round."""
import copy
import unittest
from unittest.mock import patch

from common import acceptance,db
from common.verification import infrastructure_failure
from worker import engine
from tests import test_timeout_recovery as fixture


def pipeline_report(status='ERROR'):
    return {'status':'FAIL','checks':[
        {'label':'gradle_build','status':'PASS'},
        {'label':'verification_session','status':'FAIL','exit_code':1},
        {'label':'verification_pipeline','status':status,
         'detail':"ModuleNotFoundError: No module named 'common'"}]}


class VerifierPipelineClassificationTests(unittest.TestCase):
    def test_pipeline_error_fail_and_timeout_are_infrastructure_even_with_session_exit_one(self):
        for status in ('ERROR','FAIL','TIMEOUT'):
            report=pipeline_report(status)
            before=copy.deepcopy(report)
            with self.subTest(status=status):
                self.assertEqual(infrastructure_failure(report)['label'],'verification_pipeline')
                self.assertEqual(report,before)

    def test_project_compile_and_game_assertion_failure_remain_ordinary(self):
        for label in ('gradle_build','acceptance_scenarios','acceptance_plan'):
            report={'status':'FAIL','checks':[{'label':'verification_session','status':'FAIL','exit_code':1},
                                            {'label':label,'status':'FAIL'}]}
            with self.subTest(label=label):self.assertIsNone(infrastructure_failure(report))

    def test_pipeline_error_is_not_masked_by_an_additional_project_failure(self):
        report=pipeline_report();report['checks'].append({'label':'gradle_build','status':'FAIL'})
        self.assertEqual(infrastructure_failure(report)['label'],'verification_pipeline')


class VerifierPipelineLifecycleTests(unittest.TestCase):
    setUp=fixture.TimeoutRecoveryTests.setUp
    complete=fixture.TimeoutRecoveryTests.complete
    verify=fixture.TimeoutRecoveryTests.verify

    def limit(self,n):
        with db.connect() as connection:
            connection.execute('UPDATE jobs SET max_rounds=? WHERE id=?',(n,self.jid))

    def test_broken_pipeline_pauses_after_one_development_without_another_model_call(self):
        self.limit(4)
        with patch.object(engine,'develop_round',return_value={'status':'PASS','tail':''}) as develop, \
                patch.object(engine,'checks',side_effect=lambda *args:copy.deepcopy(pipeline_report())) as checks:
            engine.replay_legacy(self.jid)
        develop.assert_called_once();checks.assert_called_once()
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase'],row['round']),('USER_PAUSED','verification_error',1))
        self.assertIn('ModuleNotFoundError',row['last_error'])
        completed=acceptance.read_json(self.stage,'verification_complete.json')
        self.assertEqual(completed['checks'][-1]['label'],'verification_pipeline')

    def test_resume_rechecks_saved_code_before_any_new_model_call(self):
        self.limit(4)
        with patch.object(engine,'develop_round',return_value={'status':'PASS','tail':''}), \
                patch.object(engine,'checks',return_value=pipeline_report()):
            engine.replay_legacy(self.jid)
        self.assertEqual(db.control_job(self.jid,'resume')['status'],'QUEUED')
        db.claim('pipeline-recovery')
        fixed={'status':'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',
               'checks':[{'label':'gradle_build','status':'PASS'}],
               'next_task_assignment':{'status':'all_verified_requires_review'}}
        with patch.object(engine,'develop_round') as develop, \
                patch.object(engine,'checks',return_value=fixed) as checks:
            engine.replay_legacy(self.jid)
        develop.assert_not_called();checks.assert_called_once()
        self.assertEqual(db.job_row(self.jid)['phase'],'acceptance_review_required')
        self.assertTrue(list((self.stage/'verification-retries').glob('*/verification_complete.json')))

    def test_persistent_pipeline_error_on_resume_pauses_again_without_another_model_call(self):
        self.limit(4)
        with patch.object(engine,'develop_round',return_value={'status':'PASS','tail':''}), \
                patch.object(engine,'checks',return_value=pipeline_report()):
            engine.replay_legacy(self.jid)
        db.control_job(self.jid,'resume');db.claim('still-broken')
        with patch.object(engine,'develop_round') as develop, \
                patch.object(engine,'checks',return_value=pipeline_report()) as checks:
            engine.replay_legacy(self.jid)
        develop.assert_not_called();checks.assert_called_once()
        self.assertEqual(db.job_row(self.jid)['phase'],'verification_error')
