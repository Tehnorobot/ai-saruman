"""Reflection routes real failures without erasing proof or changing task scope."""
import copy
import json
import unittest

from common import work_routing
from tests import test_work_routing as routing_fixture


class ReflectionRoutingTests(unittest.TestCase):
    setUp=routing_fixture.WorkRoutingTests.setUp
    completed=routing_fixture.WorkRoutingTests.completed
    choose=routing_fixture.WorkRoutingTests.choose

    def reflect(self):
        self.previous['progress_guard']={'streak':2}

    def test_partial_implementation_failure_becomes_bounded_reflection(self):
        self.completed(1,target='failed',kind='implement_and_map_requirement');self.reflect()
        before=copy.deepcopy(self.ledger)
        directive=self.choose(2)
        self.assertEqual((directive['kind'],directive['required_target']),('reflect_failure','combat'))
        self.assertEqual(self.ledger,before)
        self.assertEqual(work_routing.read_directive(self.root,self.catalog,2),directive)

    def test_latest_diagnosis_gets_repair_before_rotation_or_more_tracing(self):
        self.completed(1);self.completed(2,kind='diagnose_failure');self.reflect()
        directive=self.choose(3)
        self.assertEqual((directive['kind'],directive['required_target']),('repair_after_reflection','combat'))
        self.assertIsNone(json.loads((self.root/work_routing.NAME).read_text())['obligation'])

    def test_historical_diagnosis_survives_added_assertions_but_current_proof_is_strict(self):
        self.completed(1,kind='diagnose_failure')
        self.scenario['expected_tests'].append({'classname':'CombatTest','name':'damageSnapshot'})
        self.completed(2,model=False);self.reflect()
        self.assertEqual(self.choose(3)['kind'],'repair_after_reflection')
        self.scenario['description']='unverified changed definition'
        self.assertEqual(self.choose(4)['kind'],'normal')

    def test_failed_repair_after_diagnosis_rotates_despite_diagnostic_definition_change(self):
        self.completed(1,kind='diagnose_failure')
        self.scenario['expected_tests'].append({'classname':'CombatTest','name':'damageSnapshot'})
        self.completed(2);self.reflect()
        directive=self.choose(3)
        self.assertEqual((directive['kind'],directive['required_target']),('rotate_requirement','new-one'))
        self.completed(3,'new-one','implement_and_map_requirement')
        self.assertEqual(self.choose(4)['kind'],'return_to_failure')

    def test_without_independent_work_reflection_and_repair_alternate(self):
        self.assignment['current_task']['requirement_ids']=['failed']
        self.completed(1,kind='diagnose_failure');self.reflect()
        self.assertEqual(self.choose(2)['kind'],'repair_after_reflection')
        self.completed(2)
        self.assertEqual(self.choose(3)['kind'],'reflect_failure')
        self.completed(3,kind='diagnose_failure')
        self.assertEqual(self.choose(4)['kind'],'repair_after_reflection')

    def test_real_pending_return_obligation_keeps_priority(self):
        self.completed(1);self.completed(2);self.choose(3)
        self.completed(3,'new-one','implement_and_map_requirement');self.reflect()
        self.assertEqual(self.choose(4)['kind'],'return_to_failure')

    def test_build_and_infrastructure_blockers_do_not_route_around_failure(self):
        self.completed(1);self.reflect()
        for number,label in enumerate(('gradle_build','production_jar','verification_session','acceptance_plan'),2):
            self.previous['checks']=[{'label':label,'status':'ERROR'}]
            self.assertEqual(self.choose(number)['kind'],'normal')

    def test_missing_actual_assertion_evidence_or_pass_cannot_trigger_reflection(self):
        self.completed(1);self.reflect()
        self.ledger['scenarios']['combat']['latest']['result'].pop('assertion_evidence')
        self.assertEqual(self.choose(2)['kind'],'normal')
        self.completed(2,status='TEST_PASSED')
        self.assertEqual(self.choose(3)['kind'],'normal')

    def test_reflection_reserves_actual_scenario_before_other_checks(self):
        self.completed(1,target='failed',kind='implement_and_map_requirement');self.reflect()
        directive=self.choose(2);request={'scenarios':[]}
        selected=work_routing.reserve_regression(request,self.plan,directive)
        self.assertEqual([s['id'] for s in selected['scenarios']],['combat'])
        self.assertEqual(request,{'scenarios':[]})

    def test_policy_upgrade_refreshes_future_decision_preserving_return_state(self):
        self.completed(1);self.completed(2);self.choose(3)
        path=self.root/work_routing.NAME;state=json.loads(path.read_text())
        state['policy_revision']='old';state['directives']['4']={'kind':'normal'}
        obligation=copy.deepcopy(state['obligation']);used=state['used_requirement_ids'][:]
        path.write_text(json.dumps(state))
        self.assertEqual(self.choose(4)['kind'],'return_to_failure')
        after=json.loads(path.read_text())
        self.assertEqual(after['obligation'],obligation);self.assertEqual(after['used_requirement_ids'],used)
        before=path.read_bytes();self.choose(4);self.assertEqual(path.read_bytes(),before)

    def test_policy_upgrade_replaces_prepared_normal_with_reflection(self):
        self.completed(1,target='failed',kind='implement_and_map_requirement')
        self.assertEqual(self.choose(2)['kind'],'normal')
        path=self.root/work_routing.NAME;state=json.loads(path.read_text());state.pop('policy_revision')
        path.write_text(json.dumps(state));self.reflect()
        self.assertEqual(self.choose(2)['kind'],'reflect_failure')


if __name__=='__main__':unittest.main()
