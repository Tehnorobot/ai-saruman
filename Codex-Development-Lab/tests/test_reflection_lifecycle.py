"""Reflection changes scheduling, never user control or external-failure boundaries."""
import copy
import unittest
from unittest.mock import patch

from common import acceptance,db,planning
from worker import engine,planner
from tests import test_timeout_recovery as fixture
from tests import test_planner_workflow as workflow


class ReflectionRunnerTests(unittest.TestCase):
    setUp=fixture.TimeoutRecoveryTests.setUp
    complete=fixture.TimeoutRecoveryTests.complete
    verify=fixture.TimeoutRecoveryTests.verify

    def report(self):
        return {'status':'FAIL','checks':[{'label':'gradle_build','status':'PASS'}],
                'task_assignment':{'current_task_id':'same-task'},
                'acceptance':{'source_sha256':'a'*64}}

    def develop(self,jid,pid,n,ws,previous,stage,generation):
        self.calls.append(n)
        (stage/'planner').mkdir(exist_ok=True)
        engine.write_json(stage/'planner/plan.json',{'mode':'deep_diagnosis'})
        return {'status':'PASS','tail':''}

    def run_rounds(self,limit=5,report=None):
        self.calls=[]
        with db.connect() as con:
            con.execute('UPDATE jobs SET max_rounds=? WHERE id=?',(limit,self.jid))
        with patch.object(engine,'develop_round',side_effect=self.develop), \
             patch.object(engine,'checks',side_effect=lambda *args:copy.deepcopy(report or self.report())):
            engine.replay_legacy(self.jid)

    def test_stalled_deep_diagnosis_continues_until_user_round_limit(self):
        self.run_rounds()
        self.assertEqual(self.calls,[1,2,3,4,5])
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase']),('FINISHED','max_rounds_reached'))
        final=acceptance.read_json(self.stage.parent,'latest_verification.json')
        self.assertTrue(final['progress_guard']['stalled'])
        self.assertEqual(final['status'],'FAIL')

    def test_recovered_stalled_completed_checkpoint_does_not_pause_or_repeat_coder(self):
        self.stage.mkdir(parents=True)
        previous=self.report()
        previous['progress_guard']={'stalled':True,'streak':4}
        engine.write_json(self.stage/'verification_complete.json',previous)
        (self.stage/'planner').mkdir()
        engine.write_json(self.stage/'planner/plan.json',{'mode':'deep_diagnosis'})
        self.run_rounds(limit=2)
        self.assertEqual(self.calls,[2])
        self.assertEqual(db.job_row(self.jid)['phase'],'max_rounds_reached')

    def test_explicit_user_pause_still_prevents_model_dispatch(self):
        db.control_job(self.jid,'pause')
        self.run_rounds()
        self.assertEqual(self.calls,[])
        self.assertEqual(db.job_row(self.jid)['status'],'USER_PAUSED')

    def test_authentication_failure_still_stops_without_verification(self):
        with patch.object(engine,'develop_round',return_value={
                'status':'FAIL','tail':'not logged in'}) as develop, \
             patch.object(engine,'checks') as checks:
            engine.replay_legacy(self.jid)
        develop.assert_called_once()
        checks.assert_not_called()
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase']),('USER_PAUSED','auth_required'))

    def test_provider_budget_failure_is_not_retried_by_reflection(self):
        with db.connect() as con:
            con.execute('UPDATE jobs SET provider=?,model=? WHERE id=?',
                        ('litellm','deepseek-ai/DeepSeek-V4-Flash-0731',self.jid))
        with patch.object(engine,'develop_round',return_value={
                'status':'FAIL','tail':'HTTP 429: Insufficient remaining task token budget'}) as develop, \
             patch.object(engine,'checks') as checks:
            engine.replay_legacy(self.jid)
        develop.assert_called_once()
        checks.assert_not_called()
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase']),('USER_PAUSED','provider_error'))

    def test_broken_verifier_still_pauses_without_new_paid_round(self):
        report={'status':'FAIL','checks':[{'label':'verification_session','status':'ERROR',
                                        'detail':'Runner could not establish isolation'}]}
        self.run_rounds(report=report)
        self.assertEqual(self.calls,[1])
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase']),('USER_PAUSED','verification_error'))

    def test_final_acceptance_review_is_not_bypassed_by_reflection(self):
        report={'status':'IN_PROGRESS','checks':[{'label':'gradle_build','status':'PASS'}],
                'next_task_assignment':{'status':'all_automatable_verified_requires_review'}}
        self.run_rounds(report=report)
        self.assertEqual(self.calls,[1])
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase']),('USER_PAUSED','acceptance_review_required'))


class ReflectionPlanContractTests(unittest.TestCase):
    setUp=workflow.PlannerWorkflowTests.setUp
    complete=fixture.TimeoutRecoveryTests.complete
    verify=fixture.TimeoutRecoveryTests.verify

    def proposal(self,directive):
        self.context['scheduling_directive']={'kind':directive,'required_target':'broken'}
        self.plan={'scenarios':[{'id':'broken','requirement_ids':[self.ids[0]]}],
                   'requirements':[]}
        self.value['action'].update(kind='diagnose_failure',target='broken',
            diagnostic='The first damage event has no weapon; capture the event and synchronized inventory before choosing a repair.')
        self.value['hypotheses']=['The selected slot differs between client and server at the attack event']
        return self.value

    def validate(self):
        return planning.validate(self.value,self.catalog,self.plan,self.context,1,'normal')

    def test_reflection_requires_exact_failure_and_diagnostic_action(self):
        self.proposal('reflect_failure')
        self.assertEqual(self.validate()['action']['kind'],'diagnose_failure')
        for changes in ({'target':'another-failure'},{'kind':'repair_scenario'}):
            self.proposal('reflect_failure');self.value['action'].update(changes)
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'requires diagnose_failure'):
                self.validate()

    def test_reflection_requires_hypotheses_and_bounded_investigation(self):
        self.proposal('reflect_failure');self.value['hypotheses']=[]
        with self.assertRaisesRegex(ValueError,'Reflection needs'):self.validate()
        self.proposal('reflect_failure');self.value['action']['diagnostic']='Try again'
        with self.assertRaisesRegex(ValueError,'Reflection needs'):self.validate()

    def test_after_reflection_requires_repair_of_same_failure(self):
        self.proposal('repair_after_reflection');self.value['action']['kind']='repair_scenario'
        self.assertEqual(self.validate()['action']['target'],'broken')
        self.value['action']['kind']='diagnose_failure'
        with self.assertRaisesRegex(ValueError,'requires repair_scenario'):self.validate()

    def test_mode_uses_new_observations_instead_of_perpetual_deep_diagnosis(self):
        previous={'progress_guard':{'streak':10},'round_progress_outcome':{
            'actions':[{'expected_progress':{'kind':'diagnosis'}}]}}
        self.assertEqual(planner.mode(previous),'normal')
        self.assertEqual(planner.mode(previous,{'scheduling_directive':{'kind':'reflect_failure'}}),'deep_diagnosis')
        self.assertEqual(planner.mode({'progress_guard':{'streak':10}},
            {'scheduling_directive':{'kind':'repair_after_reflection'}}),'normal')
