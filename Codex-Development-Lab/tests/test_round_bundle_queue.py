"""A round package verifies its repaired/new scenarios before unrelated checks."""
import unittest

from common import task_assignment
from tests import test_task_assignment as fixture


class RoundBundleQueueTests(unittest.TestCase):
    setUp = fixture.TaskAssignmentTests.setUp
    record = fixture.TaskAssignmentTests.record
    update = fixture.TaskAssignmentTests.update

    def add_focus_scenario(self, sid):
        index = len(self.plan['scenarios'])
        requirement = self.plan['requirements'][0]
        requirement['scenario_ids'].append(sid)
        self.plan['scenarios'].append({
            'id': sid, 'kind': 'unit', 'task': 'checkRule', 'description': sid,
            'requirement_ids': [requirement['id']],
            'expected_tests': [{'classname': 'RuleTest', 'name': 'rule%d' % index}],
        })
        return index

    def queue(self, actions, *, max_scenarios=4, source=None, assignment=None):
        return task_assignment.request(
            self.catalog, self.plan, self.ledger, source or self.source,
            assignment or self.update(source), max_scenarios=max_scenarios,
            job_root=self.root, round_actions=actions)

    def test_recent_repair_runs_before_older_unrelated_scenarios(self):
        index = self.add_focus_scenario('water-recovery')
        self.record([index], number=20, status='FAIL')
        plain = self.queue([])
        self.assertNotEqual(plain['scenarios'][0]['id'], 'water-recovery')
        selected = self.queue([{'kind': 'repair_scenario', 'target': 'water-recovery'}])
        self.assertEqual(selected['scenarios'][0]['id'], 'water-recovery')
        self.assertEqual(selected['prioritized_scenario_ids'], ['water-recovery'])

    def test_exact_behavior_condition_is_checked_before_bootstrap_in_same_requirement(self):
        self.add_focus_scenario('real-behavior')
        requirement=self.plan['requirements'][0]
        requirement['obligation_scenarios']={'core-condition':['real-behavior'],
                                            'bootstrap-condition':['scenario-00']}
        selected=self.queue([{'kind':'implement_and_map_requirement','target':requirement['id'],
                             'condition_ids':['core-condition']}],max_scenarios=1)
        self.assertEqual([s['id'] for s in selected['scenarios']],['real-behavior'])

    def test_related_requirement_actions_each_get_their_checks_in_package_order(self):
        first, second = self.plan['requirements'][:2]
        result = self.queue([
            {'kind': 'implement_and_map_requirement', 'target': second['id']},
            {'kind': 'implement_and_map_requirement', 'target': first['id']},
        ])
        self.assertEqual([s['id'] for s in result['scenarios']], ['scenario-01', 'scenario-00'])
        self.assertEqual(result['prioritized_scenario_ids'], ['scenario-01', 'scenario-00'])

    def test_large_primary_requirement_does_not_starve_second_package_member(self):
        for sid in ('primary-a', 'primary-b', 'primary-c', 'primary-d'):
            self.add_focus_scenario(sid)
        first, second = self.plan['requirements'][:2]
        result = self.queue([
            {'kind': 'implement_and_map_requirement', 'target': first['id']},
            {'kind': 'implement_and_map_requirement', 'target': second['id']},
        ], max_scenarios=2)
        self.assertEqual([s['id'] for s in result['scenarios']], ['primary-a', 'scenario-01'])
        self.assertEqual(len(result['omitted_priority_scenario_ids']), 4)

    def test_explicit_requirement_check_precedes_older_generic_mappings(self):
        self.add_focus_scenario('last-by-sort')
        requirement = self.plan['requirements'][0]
        result = self.queue([{'kind': 'implement_and_map_requirement',
                              'target': requirement['id'],
                              'verification_scenario_ids': ['scenario-00']}], max_scenarios=1)
        self.assertEqual([s['id'] for s in result['scenarios']], ['scenario-00'])
        self.assertEqual(result['omitted_priority_scenario_ids'], ['last-by-sort'])

    def test_exact_repair_target_precedes_explicit_additional_checks(self):
        result = self.queue([{'kind': 'repair_scenario', 'target': 'scenario-01',
                              'verification_scenario_ids': ['scenario-00']}], max_scenarios=1)
        self.assertEqual([s['id'] for s in result['scenarios']], ['scenario-01'])
        self.assertEqual(result['omitted_priority_scenario_ids'], ['scenario-00'])

    def test_explicit_checks_are_deduplicated_and_cannot_pull_unrelated_work(self):
        result = self.queue([
            {'kind': 'repair_scenario', 'target': 'scenario-01',
             'verification_scenario_ids': ['scenario-01', 'scenario-00', 'scenario-02', 'unknown']},
            {'kind': 'implement_or_verify_scenario', 'target': 'scenario-00'},
        ])
        self.assertEqual([s['id'] for s in result['scenarios']], ['scenario-01', 'scenario-00'])
        self.assertEqual(result['prioritized_scenario_ids'], ['scenario-01', 'scenario-00'])

    def test_bound_and_omission_metadata_keep_all_unselected_work_pending(self):
        for sid in ('new-a', 'new-b', 'new-c', 'new-d'):
            self.add_focus_scenario(sid)
        actions = [{'kind': 'implement_and_map_requirement',
                    'target': self.plan['requirements'][0]['id']}]
        before = len(self.ledger['scenarios'])
        selected = self.queue(actions, max_scenarios=2)
        self.assertEqual(len(selected['scenarios']), 2)
        self.assertEqual(len(selected['prioritized_scenario_ids']), 2)
        self.assertEqual(len(selected['omitted_priority_scenario_ids']), 3)
        self.assertEqual(len(self.ledger['scenarios']), before)
        larger = self.queue(actions, max_scenarios=100)
        self.assertEqual(len(larger['scenarios']), 4)
        self.assertEqual(len(larger['omitted_priority_scenario_ids']), 1)

    def test_current_pass_is_not_rerun_but_stale_source_is_prioritized(self):
        self.record([0])
        actions = [{'kind': 'implement_or_verify_scenario', 'target': 'scenario-00'}]
        current = self.queue(actions)
        self.assertNotIn('scenario-00', [s['id'] for s in current['scenarios']])
        self.assertEqual(current['prioritized_scenario_ids'], [])
        stale = self.queue(actions, source='b' * 64)
        self.assertEqual(stale['scenarios'][0]['id'], 'scenario-00')
        self.assertEqual(stale['prioritized_scenario_ids'], ['scenario-00'])

    def test_tampered_pass_evidence_cannot_skip_a_priority_check(self):
        self.record([0])
        evidence = self.root / 'round-01/acceptance/scenario-00/assertions/report.xml'
        evidence.write_bytes(b'tampered report')
        selected = self.queue([{'kind': 'implement_or_verify_scenario', 'target': 'scenario-00'}])
        self.assertEqual(selected['scenarios'][0]['id'], 'scenario-00')
        self.assertEqual(selected['prioritized_scenario_ids'], ['scenario-00'])

    def test_previous_task_regression_is_retained_after_priority_work(self):
        self.update()
        self.record([0, 1])
        self.update()
        assignment = self.update('b' * 64)
        action = {'kind': 'implement_and_map_requirement',
                  'target': self.plan['requirements'][2]['id']}
        selected = self.queue([action], source='b' * 64, assignment=assignment)
        self.assertEqual(selected['scenarios'][0]['id'], 'scenario-02')
        self.assertEqual(len(selected['scenarios']), 2)
        self.assertIn(selected['scenarios'][1]['id'], ('scenario-00', 'scenario-01'))

    def test_observed_failure_outside_current_task_can_be_repaired_first(self):
        current = self.update()
        current['unresolved_failures'] = [{'id': 'scenario-02',
                                            'requirement_ids': self.plan['scenarios'][2]['requirement_ids']}]
        selected = self.queue([{'kind': 'repair_scenario', 'target': 'scenario-02'}], assignment=current)
        self.assertEqual(selected['scenarios'][0]['id'], 'scenario-02')
        self.assertEqual(selected['prioritized_scenario_ids'], ['scenario-02'])

    def test_zero_verification_limit_returns_no_checks_and_reports_priority_omissions(self):
        result = self.queue([{'kind': 'repair_scenario', 'target': 'scenario-01'}], max_scenarios=0)
        self.assertEqual(result['scenarios'], [])
        self.assertEqual(result['prioritized_scenario_ids'], [])
        self.assertEqual(result['omitted_priority_scenario_ids'], ['scenario-01'])


if __name__ == '__main__':
    unittest.main()
