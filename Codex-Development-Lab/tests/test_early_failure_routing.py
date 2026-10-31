"""Early real failures remain repairable even when later checks never execute."""
import copy,json,unittest
from common import work_routing
from tests import test_work_routing as fixture


class EarlyFailureRoutingTests(unittest.TestCase):
    setUp=fixture.WorkRoutingTests.setUp
    completed=fixture.WorkRoutingTests.completed
    choose=fixture.WorkRoutingTests.choose

    def early_failure(self,number=1,kind='diagnose_failure'):
        self.scenario['expected_tests']=[{'classname':'CombatTest','name':'fight'},
                                         {'classname':'CombatTest','name':'stop'}]
        folder=self.completed(number,kind=kind)
        result=self.ledger['scenarios']['combat']['latest']['result']
        result['assertion_evidence'].update(
            missing_tests=[{'classname':'CombatTest','name':'stop'}],
            failures=[{'classname':'CombatTest','name':'fight','message':'No sword hit'}])
        raw={'scenarios':[result]}
        (folder/'acceptance_results.json').write_text(json.dumps(raw))
        return result

    def test_named_failure_is_observed_despite_missing_later_checks(self):
        result=self.early_failure()
        original=copy.deepcopy(result)
        self.assertTrue(work_routing._failed(result,self.scenario))
        self.assertTrue(work_routing._current_failure(self.ledger,self.scenario))
        self.assertEqual(result,original)
        self.assertEqual(result['status'],'FAIL')

    def test_early_diagnosis_failure_gets_a_concrete_repair(self):
        self.early_failure();self.previous['progress_guard']={'streak':7}
        directive=self.choose(2)
        self.assertEqual((directive['kind'],directive['required_target']),
                         ('repair_after_reflection','combat'))

    def test_absent_or_unrelated_failure_is_not_invented_from_missing_tests(self):
        result=self.early_failure()
        for failures in ([],None,[{'classname':'OtherTest','name':'fail'}],
                         [{'classname':'CombatTest','name':'clientLifecycle'}]):
            result['assertion_evidence']['failures']=failures
            with self.subTest(failures=failures):
                self.assertFalse(work_routing._failed(result,self.scenario))

    def test_report_and_definition_binding_stay_required(self):
        result=self.early_failure()
        result['assertion_evidence']['reports']=[]
        self.assertFalse(work_routing._failed(result,self.scenario))
        result['assertion_evidence']['reports']=[{'file':'actual.xml'}]
        result['assertion_evidence']['expected_tests']=[{'classname':'OtherTest','name':'fight'}]
        self.assertFalse(work_routing._failed(result,self.scenario))

    def test_early_failure_never_turns_a_pass_or_nonexecuted_result_into_failure(self):
        result=self.early_failure()
        for status,test_status in (('TEST_PASSED','PASS'),('NOT_TESTED','NOT_TESTED'),('FAIL','NOT_TESTED')):
            result.update(status=status,test_status=test_status)
            with self.subTest(status=status,test_status=test_status):
                self.assertFalse(work_routing._failed(result,self.scenario))

    def test_policy_upgrade_replaces_frozen_normal_after_early_failure(self):
        self.early_failure();self.previous['progress_guard']={'streak':7}
        state={'schema_version':1,'spec_sha256':self.catalog['spec_sha256'],
            'policy_revision':'reflection-routing-v1','used_requirement_ids':[],
            'obligation':None,'directives':{'2':{'kind':'normal'}}}
        (self.root/work_routing.NAME).write_text(json.dumps(state))
        self.assertEqual(self.choose(2)['kind'],'repair_after_reflection')
        self.assertEqual(json.loads((self.root/work_routing.NAME).read_text())['policy_revision'],
                         work_routing.POLICY_REVISION)
