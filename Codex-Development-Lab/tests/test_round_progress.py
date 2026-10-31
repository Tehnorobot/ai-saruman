"""Planned progress needs scoped evidence; claims never create a success verdict."""
import copy,unittest
from common import acceptance,acceptance_progress,coverage,planning,round_progress
from tests import test_planner_workflow as workflow


class RoundProgressContractTests(unittest.TestCase):
    setUp=workflow.PlannerWorkflowTests.setUp
    complete=workflow.PlannerWorkflowTests.complete
    verify=workflow.PlannerWorkflowTests.verify

    def enable(self):
        self.condition=coverage.obligations(self.catalog)[self.ids[0]][0]['id']
        self.context['round_progress_policy']={'enabled':True,'version':1,
            'pending_conditions':[{'requirement_id':self.ids[0],'condition_id':self.condition}],
            'coverage_review_requirement_ids':[self.ids[0]],'failed_scenario_ids':['broken'],
            'verification_scenario_ids':['stale']}
        self.value['action'].update(condition_ids=[self.condition],verification_kind='gameplay',
            verification_objective='Observe actual target damage and input release after stopping',
            expected_progress={'kind':'new_behavior','observable_result':'A hostile target is attacked through the real task controller'})
        self.plan={'requirements':[],'scenarios':[{'id':'broken','requirement_ids':[self.ids[0]]},
                                                 {'id':'stale','requirement_ids':[self.ids[0]]}]}

    def validate(self):return planning.validate(self.value,self.catalog,self.plan,self.context,1,'normal')

    def test_new_behavior_requires_a_pending_condition_and_concrete_check(self):
        self.enable();decision=self.validate()
        self.assertEqual(decision['action']['expected_progress']['kind'],'new_behavior')
        for changes in ({'condition_ids':[]},{'verification_objective':'write docs'},
                        {'expected_progress':{'kind':'verification','observable_result':'Repeat a successful startup without new assertions'}}):
            self.enable();self.value['action'].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.validate()

    def test_missing_or_vague_expected_result_is_rejected(self):
        self.enable();self.value['action'].pop('expected_progress')
        with self.assertRaisesRegex(ValueError,'expected_progress'):self.validate()
        self.enable();self.value['action']['expected_progress']['observable_result']='Done'
        with self.assertRaisesRegex(ValueError,'observable_result'):self.validate()

    def test_actual_repair_verification_and_review_remain_valid(self):
        for kind,target,purpose in [('repair_scenario','broken','repair'),
                                    ('implement_or_verify_scenario','stale','verification'),
                                    ('review_coverage',self.ids[0],'coverage')]:
            self.enable();self.value['action'].update(kind=kind,target=target,
                expected_progress={'kind':purpose,'observable_result':'Observe the independently assigned result for this exact target'})
            if kind=='review_coverage':self.value['coverage_reviews']=[{'requirement_id':self.ids[0]}]
            self.assertEqual(self.validate()['action']['expected_progress']['kind'],purpose)

    def test_unobserved_repair_and_repeated_coverage_are_rejected(self):
        self.enable();self.value['action'].update(kind='repair_scenario',target='stale',
            expected_progress={'kind':'repair','observable_result':'Fix a failure that has not actually been observed'})
        with self.assertRaisesRegex(ValueError,'specific independently'):self.validate()
        self.enable();self.context['round_progress_policy']['coverage_review_requirement_ids']=[]
        self.value['action'].update(kind='review_coverage',target=self.ids[0],expected_progress={
            'kind':'coverage','observable_result':'Review the same unchanged conditions for another round'})
        self.value['coverage_reviews']=[{'requirement_id':self.ids[0]}]
        with self.assertRaisesRegex(ValueError,'pending mapped'):self.validate()

    def test_unjustified_infrastructure_and_schema_work_are_rejected(self):
        for kind in ('diagnose_failure','repair_verification_failure','repair_acceptance_plan'):
            self.enable();self.context['round_progress_policy']['failed_scenario_ids']=[]
            self.value['action'].update(kind=kind,target='build',expected_progress={
                'kind':round_progress.KINDS[kind],'observable_result':'Fix the build or schema despite no independently observed failure'})
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.validate()

    def test_real_build_or_schema_error_can_be_fixed_without_adding_a_feature(self):
        self.enable();self.context['round_progress_policy']['failed_scenario_ids']=[]
        self.context['previous_independent_verification']={'checks':[{'label':'gradle_build','status':'FAIL'}]}
        self.value['action'].update(kind='repair_verification_failure',target='gradle_build',
            expected_progress={'kind':'infrastructure','observable_result':'The failing build completes and enables the assigned gameplay test'})
        self.assertEqual(self.validate()['action']['kind'],'repair_verification_failure')
        self.context['acceptance_plan_notes']=['Expected text is empty']
        self.value['action'].update(kind='repair_acceptance_plan',target='acceptance-plan.json')
        self.assertEqual(self.validate()['action']['kind'],'repair_acceptance_plan')

    def test_empty_manual_only_round_is_not_a_progress_substitute(self):
        self.enable();self.value['action'].update(kind='await_user_review',target=self.ids[0],
            expected_progress={'kind':'manual','observable_result':'Ask the user to check an unspecified behavior without instructions'})
        with self.assertRaisesRegex(ValueError,'genuine validated'):self.validate()

    def test_each_additional_package_member_requires_its_own_result(self):
        self.enable();self.context['current_task']['requirement_ids']=self.ids[:]
        other=coverage.obligations(self.catalog)[self.ids[1]][0]['id']
        self.context['round_progress_policy']['pending_conditions'].append({'requirement_id':self.ids[1],'condition_id':other})
        self.value.update(bundle_reason='These behaviors share the same controller and fixture',
            additional_actions=[{'kind':'implement_and_map_requirement','target':self.ids[1],
                'requirement_ids':[self.ids[1]],'reason':'Related second behavior',
                'condition_ids':[other],'verification_kind':'gameplay',
                'verification_objective':'Observe the second behavior and assert its real result'}])
        with self.assertRaisesRegex(ValueError,'expected_progress'):self.validate()
        self.value['additional_actions'][0]['expected_progress']={
            'kind':'new_behavior','observable_result':'Observe the second behavior through executable independent assertions'}
        self.assertEqual(self.validate()['action_count'],2)

    def test_bootstrap_legacy_context_does_not_require_a_progress_claim(self):
        plan=acceptance.load_plan(self.workspace,self.catalog)
        self.assertEqual(planning.validate(self.value,self.catalog,plan,self.context,1,'normal')['action']['target'],self.ids[0])


class PolicyAndOutcomeTests(unittest.TestCase):
    def fixture(self):
        catalog=acceptance.build_catalog('# Mod\n\n## Behavior\n\n1. Attack enemies.\n')
        rid=catalog['requirements'][0]['id'];condition=coverage.obligations(catalog)[rid][-1]['id']
        scenario={'id':'boot','kind':'gameplay','task':'acceptBoot','requirement_ids':[rid],
                  'expected_tests':[{'classname':'Boot','name':'loaded'}]}
        plan={'scenarios':[scenario],'requirements':[{'id':rid,'obligation_scenarios':{condition:['boot']}}]}
        assignment={'current_task':{'requirement_ids':[rid]}}
        ledger={'scenarios':{'boot':{'last_pass':{'definition_sha256':acceptance_progress.definition_digest(scenario),
                                                'result':{'test_status':'PASS'}}}}}
        return catalog,rid,condition,plan,assignment,ledger

    def test_policy_requires_real_baseline_and_skips_bare_list_numbers(self):
        catalog,rid,condition,plan,assignment,ledger=self.fixture()
        previous={'checks':[{'label':'gradle_build','status':'PASS'}]}
        self.assertFalse(round_progress.policy(catalog,plan,assignment,{},previous,{})['enabled'])
        policy=round_progress.policy(catalog,plan,assignment,ledger,previous,{})
        self.assertTrue(policy['enabled'])
        self.assertEqual(policy['pending_conditions'],[{'requirement_id':rid,'condition_id':condition}])

    def test_progress_claim_without_execution_never_counts_as_observed_success(self):
        catalog,rid,condition,plan,assignment,ledger=self.fixture()
        decision={'action':{'kind':'implement_and_map_requirement','target':rid,'condition_ids':[condition],
            'expected_progress':{'kind':'new_behavior','observable_result':'The model claims that everything is now fully implemented'}}}
        missing=round_progress.outcome(decision,plan,{'scenarios':[]})
        self.assertFalse(missing['actions'][0]['execution_observed'])
        self.assertFalse(missing['establishes_requirement_pass'])
        executed=round_progress.outcome(decision,plan,{'scenarios':[{'id':'boot','test_status':'FAIL','status':'FAIL'}]})
        self.assertTrue(executed['actions'][0]['execution_observed'])
        self.assertEqual(executed['actions'][0]['executed_scenarios'][0]['test_status'],'FAIL')
        self.assertFalse(executed['establishes_requirement_pass'])

    def test_scenario_diagnosis_records_an_executed_failure_without_claiming_pass(self):
        _,_,_,plan,_,_=self.fixture()
        decision={'action':{'kind':'diagnose_failure','target':'boot',
            'expected_progress':{'kind':'diagnosis','observable_result':'Capture the actual first damage event and selected weapon'}}}
        result=round_progress.outcome(decision,plan,{'scenarios':[
            {'id':'boot','test_status':'FAIL','status':'FAIL'}]})
        row=result['actions'][0]
        self.assertTrue(row['execution_applicable'])
        self.assertTrue(row['execution_observed'])
        self.assertEqual(row['executed_scenarios'],[{'id':'boot','status':'FAIL','test_status':'FAIL'}])
        self.assertFalse(result['establishes_requirement_pass'])

    def test_scenario_diagnosis_does_not_invent_execution_from_a_placeholder(self):
        _,_,_,plan,_,_=self.fixture()
        decision={'action':{'kind':'diagnose_failure','target':'boot',
            'expected_progress':{'kind':'diagnosis','observable_result':'Capture the actual first damage event and selected weapon'}}}
        for rows in ([],[{'id':'boot','test_status':'NOT_TESTED','status':'NOT_TESTED'}]):
            with self.subTest(rows=rows):
                result=round_progress.outcome(decision,plan,{'scenarios':rows})
                self.assertTrue(result['actions'][0]['execution_applicable'])
                self.assertFalse(result['actions'][0]['execution_observed'])
                self.assertEqual(result['actions'][0]['executed_scenarios'],[])

    def test_infrastructure_diagnosis_does_not_attribute_unrelated_scenario_execution(self):
        _,_,_,plan,_,_=self.fixture()
        decision={'action':{'kind':'diagnose_failure','target':'gradle_build',
            'expected_progress':{'kind':'diagnosis','observable_result':'Inspect the failing compiler diagnostic and dependency signature'}}}
        # Even an unexpected row matching the check label is not a known scenario.
        result=round_progress.outcome(decision,plan,{'scenarios':[
            {'id':'boot','test_status':'PASS','status':'TEST_PASSED'},
            {'id':'gradle_build','test_status':'FAIL','status':'FAIL'}]})
        self.assertFalse(result['actions'][0]['execution_applicable'])
        self.assertFalse(result['actions'][0]['execution_observed'])
        self.assertEqual(result['actions'][0]['executed_scenarios'],[])
