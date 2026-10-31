"""Assigned diagnostics execute their known scenario before generic fair-queue work."""
import unittest
from common import acceptance,planning
from tests import test_round_bundle_queue as queue_fixture
from tests import test_planner_workflow as planning_fixture


class DiagnosisQueueTests(unittest.TestCase):
    setUp=queue_fixture.RoundBundleQueueTests.setUp
    record=queue_fixture.RoundBundleQueueTests.record
    update=queue_fixture.RoundBundleQueueTests.update
    queue=queue_fixture.RoundBundleQueueTests.queue
    add_focus_scenario=queue_fixture.RoundBundleQueueTests.add_focus_scenario

    def test_diagnosis_target_precedes_unrelated_older_scenarios_without_explicit_ids(self):
        index=self.add_focus_scenario('diagnostic-target')
        self.record([index],number=20,status='FAIL')
        plain=self.queue([],max_scenarios=1)
        self.assertNotEqual(plain['scenarios'][0]['id'],'diagnostic-target')
        selected=self.queue([{'kind':'diagnose_failure','target':'diagnostic-target'}],max_scenarios=1)
        self.assertEqual([s['id'] for s in selected['scenarios']],['diagnostic-target'])
        self.assertEqual(selected['prioritized_scenario_ids'],['diagnostic-target'])

    def test_infrastructure_diagnosis_cannot_invent_a_scenario(self):
        selected=self.queue([{'kind':'diagnose_failure','target':'verification_session'}],max_scenarios=1)
        self.assertEqual(selected['prioritized_scenario_ids'],[])
        self.assertNotIn('verification_session',[s['id'] for s in selected['scenarios']])

    def test_current_pass_is_not_unchanged_rerun_by_diagnostic_priority(self):
        self.record([0])
        selected=self.queue([{'kind':'diagnose_failure','target':'scenario-00'}],max_scenarios=1)
        self.assertNotIn('scenario-00',[s['id'] for s in selected['scenarios']])
        self.assertEqual(selected['prioritized_scenario_ids'],[])


class DiagnosisPlanningTests(unittest.TestCase):
    setUp=planning_fixture.PlannerWorkflowTests.setUp
    complete=planning_fixture.PlannerWorkflowTests.complete
    verify=planning_fixture.PlannerWorkflowTests.verify

    def test_known_diagnostic_target_consumes_a_verification_slot(self):
        plan={'scenarios':[{'id':'broken','requirement_ids':[self.ids[0]]},
                           {'id':'other','requirement_ids':[self.ids[0]]}], 'requirements':[]}
        self.value['action'].update(kind='diagnose_failure',target='broken',
            verification_scenario_ids=['other'])
        self.value['verification_limit']=1
        with self.assertRaisesRegex(ValueError,'exceed verification_limit'):
            planning.validate(self.value,self.catalog,plan,self.context,1,'normal')
        self.value['verification_limit']=2
        self.assertEqual(planning.validate(self.value,self.catalog,plan,self.context,1,'normal')['verification_limit'],2)

    def test_infrastructure_diagnostic_target_consumes_no_fabricated_slot(self):
        plan={'scenarios':[{'id':'other','requirement_ids':[self.ids[0]]}], 'requirements':[]}
        self.value['action'].update(kind='diagnose_failure',target='verification_session',
            verification_scenario_ids=['other'])
        self.value['verification_limit']=1
        self.assertEqual(planning.validate(self.value,self.catalog,plan,self.context,1,'normal')['verification_limit'],1)
