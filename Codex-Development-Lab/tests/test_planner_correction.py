"""Bad typed targets are corrected iteratively inside the original allowance."""
import copy,json,unittest
from unittest.mock import patch
from common import acceptance,planning
from worker import engine,planner
from tests import test_planner_workflow as fixture


class TargetContractTests(unittest.TestCase):
    setUp=fixture.PlannerWorkflowTests.setUp
    complete=fixture.PlannerWorkflowTests.complete
    verify=fixture.PlannerWorkflowTests.verify

    def scenario_plan(self):
        return {'scenarios':[{'id':'birch-cycle','requirement_ids':[self.ids[0]]}], 'requirements':[]}

    def test_scenario_cannot_be_used_as_requirement_target(self):
        self.value['action']['target']='birch-cycle'
        with self.assertRaisesRegex(ValueError,'is a scenario ID.*requires a REQ ID'):
            planning.validate(self.value,self.catalog,self.scenario_plan(),self.context,1,'normal')

    def test_unrelated_failure_does_not_allow_repeating_a_passed_scenario(self):
        self.context['scenario_facts']={'items':[{'id':'birch-cycle','status':'TEST_PASSED'}]}
        self.context['unresolved_scenario_failures']=[{'id':'other-failure','requirement_ids':[self.ids[0]]}]
        for kind in ('repair_scenario','implement_or_verify_scenario'):
            self.value['action'].update(kind=kind,target='birch-cycle')
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'already-passed scenario'):
                planning.validate(self.value,self.catalog,self.scenario_plan(),self.context,1,'normal')

    def test_stale_or_unchecked_scenario_can_be_verified(self):
        self.value['action'].update(kind='implement_or_verify_scenario',target='birch-cycle')
        for status in ('STALE','NOT_TESTED','FAIL'):
            self.context['scenario_facts']={'items':[{'id':'birch-cycle','status':status}]}
            self.assertEqual(planning.validate(self.value,self.catalog,self.scenario_plan(),self.context,1,'normal')['action']['target'],'birch-cycle')

    def test_prompt_targets_distinguish_requirements_and_current_scenario_results(self):
        self.context['scenario_facts']={'items':[{'id':'birch-cycle','status':'TEST_PASSED'}]}
        options=planning.target_options(self.catalog,self.scenario_plan(),self.context)
        self.assertIn(self.ids[0],options['requirement_targets'])
        self.assertNotIn('birch-cycle',options['requirement_targets'])
        self.assertEqual(options['scenario_targets']['birch-cycle'],'TEST_PASSED')


class CorrectionWorkflowTests(unittest.TestCase):
    setUp=fixture.PlannerWorkflowTests.setUp
    response=fixture.PlannerWorkflowTests.response
    plan=fixture.PlannerWorkflowTests.plan
    complete=fixture.PlannerWorkflowTests.complete
    verify=fixture.PlannerWorkflowTests.verify

    def test_invalid_then_valid_uses_two_planner_calls_and_saves_initial_error(self):
        valid=copy.deepcopy(self.value)
        self.value['action']['target']='unknown-target'
        def respond(*args,**kwargs):
            result=self.response(*args,**kwargs)
            self.value=valid
            return result
        with patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(request.call_count,2)
        self.assertIn('planner_validation_feedback',request.call_args.args[3])
        folder=self.stage/'planner'
        self.assertEqual(len(list(folder.glob('plan-invalid-*.json'))),1)
        self.assertEqual(len(list(folder.glob('plan-invalid-*.jsonl'))),1)
        self.assertTrue(json.loads((folder/'plan.json').read_text())['validation_retry_used'])

    def test_multiple_invalid_answers_are_corrected_without_fixed_retry_count(self):
        valid=copy.deepcopy(self.value)
        self.value['action']['target']='unknown-target'
        calls=0
        def respond(*args,**kwargs):
            nonlocal calls
            calls+=1
            result=self.response(*args,**kwargs)
            if calls==3:self.value=valid
            return result
        with patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(request.call_count,4)
        self.assertTrue((self.stage/'planner/plan.json').exists())
        self.assertEqual(len(list((self.stage/'planner').glob('plan-invalid-*.jsonl'))),3)

    def test_provider_failure_does_not_trigger_output_correction(self):
        with patch.object(engine,'request_agent',return_value={'status':'FAIL','tail':'capacity'}) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(request.call_count,1)

    def test_provider_prose_cannot_trigger_checkpoint_replanning(self):
        calls=0
        def respond(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:return {'status':'FAIL','tail':'Invalid planner checkpoint: quoted provider body'}
            return self.response(*args,**kwargs)
        with patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(request.call_count,1)

    def test_api_correction_is_skipped_when_trusted_usage_is_unavailable(self):
        self.value['action']['target']='unknown-target'
        with patch('common.provider_settings.resolve_role',return_value={
                'provider':'litellm','model':'test-model','reasoning_effort':'default'}), \
                patch.object(engine,'provider_control',side_effect=engine.GatewayError('unavailable')), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'ERROR')
        self.assertEqual(request.call_count,1)

    def test_api_correction_receives_only_the_remaining_allowance(self):
        valid=copy.deepcopy(self.value)
        self.value['action']['target']='unknown-target'
        before=dict.fromkeys(('requests','input_tokens','output_tokens','cache_creation_input_tokens',
                              'cache_read_input_tokens','billing_unknown_requests'),0)
        after={**before,'requests':1,'input_tokens':1000,'output_tokens':100}
        def respond(*args,**kwargs):
            result=self.response(*args,**kwargs)
            self.value=valid
            return result
        with patch('common.provider_settings.resolve_role',return_value={
                'provider':'litellm','model':'test-model','reasoning_effort':'default'}), \
                patch.object(engine,'provider_control',side_effect=[{'totals':before},{'totals':after}]), \
                patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(request.call_count,2)
        expected=planner.correction_limits(engine.db.job_row(self.jid),before,after)
        self.assertEqual(request.call_args.kwargs['planner_limits'],expected)

    def test_user_control_prevents_the_correction_call(self):
        self.value['action']['target']='unknown-target'
        with patch.object(engine,'poll_job',return_value='USER_PAUSED'), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'USER_PAUSED')
        self.assertEqual(request.call_count,0)

    def test_repeated_api_corrections_do_not_reset_the_original_allowance(self):
        self.value['action']['target']='unknown-target'
        before=dict.fromkeys(('requests','input_tokens','output_tokens','cache_creation_input_tokens',
                              'cache_read_input_tokens','billing_unknown_requests'),0)
        after1={**before,'requests':1,'input_tokens':1000}
        after2={**before,'requests':2,'input_tokens':2000}
        exhausted={**before,'requests':engine.db.job_row(self.jid)['dslab_max_calls']//5}
        with patch('common.provider_settings.resolve_role',return_value={
                'provider':'litellm','model':'test-model','reasoning_effort':'default'}), \
                patch.object(engine,'provider_control',side_effect=[{'totals':v} for v in (before,after1,after2,exhausted)]), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'ERROR')
        self.assertEqual(request.call_count,3)
        self.assertEqual([c.kwargs['planner_limits'] for c in request.call_args_list[1:]],
            [planner.correction_limits(engine.db.job_row(self.jid),before,v) for v in (after1,after2)])
        self.assertEqual(json.loads((self.stage/'planner/validation-budget.json').read_text())['usage_before'],before)


class CoverageCorrectionTests(unittest.TestCase):
    setUp=fixture.PlannerWorkflowTests.setUp
    response=fixture.PlannerWorkflowTests.response
    plan=fixture.PlannerWorkflowTests.plan
    complete=fixture.PlannerWorkflowTests.complete
    verify=fixture.PlannerWorkflowTests.verify

    def coverage(self):
        self.value['action'].update(kind='review_coverage')
        self.value['coverage_reviews']=[{'requirement_id':self.ids[0],'obligations':[]}]
        return {'accepted':[], 'rejected':[{'requirement_id':self.ids[0],
            'reason':'Referenced evidence was not observed through tools'}]}

    def test_coverage_rejection_can_be_corrected_before_any_coder_call(self):
        rejected=self.coverage()
        with patch('common.coverage.apply_reviews',side_effect=[rejected,{'accepted':[self.ids[0]],'rejected':[]}]), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(request.call_count,2)
        self.assertTrue(all(c.kwargs['role']=='planner' for c in request.call_args_list))
        self.assertIn('Referenced evidence was not observed',request.call_args.args[3])
        self.assertTrue((self.stage/'planner/plan-coverage-rejected.json').exists())
        self.assertTrue(json.loads((self.stage/'planner/plan.json').read_text())['validation_retry_used'])

    def test_repeated_coverage_rejection_can_be_corrected_without_publishing_invalid_plan(self):
        rejected=self.coverage()
        with patch('common.coverage.apply_reviews',side_effect=[rejected,rejected,rejected,{'accepted':[self.ids[0]],'rejected':[]}]), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(request.call_count,4)
        self.assertTrue((self.stage/'planner/plan.json').exists())
        self.assertEqual(len(list((self.stage/'planner').glob('plan-invalid-*.jsonl'))),3)

    def test_coverage_correction_respects_user_pause(self):
        rejected=self.coverage()
        with patch('common.coverage.apply_reviews',return_value=rejected), \
                patch.object(engine,'poll_job',return_value='USER_PAUSED'), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'USER_PAUSED')
        self.assertEqual(request.call_count,0)

    def test_api_coverage_correction_does_not_reset_unknown_budget(self):
        rejected=self.coverage()
        with patch('common.coverage.apply_reviews',return_value=rejected), \
                patch('common.provider_settings.resolve_role',return_value={
                    'provider':'litellm','model':'test-model','reasoning_effort':'default'}), \
                patch.object(engine,'provider_control',side_effect=engine.GatewayError('unavailable')), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'ERROR')
        self.assertEqual(request.call_count,1)


class CorrectionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.job={'dslab_token_budget':100000,'dslab_max_calls':100}
        self.before=dict.fromkeys(('requests','input_tokens','output_tokens',
            'cache_creation_input_tokens','cache_read_input_tokens','billing_unknown_requests'),0)

    def test_correction_uses_only_remaining_original_planner_budget(self):
        after={**self.before,'requests':3,'input_tokens':7000,'output_tokens':1000,
               'cache_read_input_tokens':2000,'cache_creation_input_tokens':500}
        self.assertEqual(planner.correction_limits(self.job,self.before,after),{'tokens':9500,'calls':17})

    def test_exhausted_or_unknown_usage_cannot_reset_the_allowance(self):
        for after in ({**self.before,'input_tokens':20000},
                      {**self.before,'requests':20},
                      {**self.before,'billing_unknown_requests':1},
                      {**self.before,'requests':-1},{}):
            self.assertIsNone(planner.correction_limits(self.job,self.before,after))
