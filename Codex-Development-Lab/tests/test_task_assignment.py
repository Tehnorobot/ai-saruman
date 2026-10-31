"""Task focus persistence, regression gates and independent file-backed proof."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from common import acceptance, acceptance_progress as progress, acceptance_tasks
from common import task_assignment as assignment


class TaskAssignmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = acceptance.build_catalog('# Mod\n\n## Combat\n\n- Fight\n\n- Shield\n\n## Movement\n\n- Walk\n\n## Food\n\n- Eat')
        self.plan = {'requirements': [], 'scenarios': [], 'notes': []}
        for index, requirement in enumerate(self.catalog['requirements']):
            sid = 'scenario-%02d' % index
            self.plan['requirements'].append({'id': requirement['id'], 'title': 'Rule',
                'expected': 'Expected', 'kind': 'unit', 'scenario_ids': [sid]})
            self.plan['scenarios'].append({'id': sid, 'kind': 'unit', 'task': 'checkRule',
                'description': 'Rule %d' % index, 'requirement_ids': [requirement['id']],
                'expected_tests': [{'classname': 'RuleTest', 'name': 'rule%d' % index}]})
        self.source = 'a' * 64
        self.ledger = progress.load_progress(self.root, self.catalog, self.plan)
        self.groups = acceptance_tasks.build_tasks(self.catalog)
        self.assertEqual([len(g['requirement_ids']) for g in self.groups], [2, 1, 1])

    def record(self, indexes, number=1, status='TEST_PASSED', source=None):
        results = []
        for index in indexes:
            scenario = self.plan['scenarios'][index]
            relative = 'acceptance/%s/assertions/report.xml' % scenario['id']
            file = self.root / ('round-%02d' % number) / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'<testsuite/>')
            result = {'id': scenario['id'], 'requirement_ids': scenario['requirement_ids'],
                      'status': status, 'test_status': ('PASS' if status == 'TEST_PASSED'
                                                       else 'FAIL' if status == 'FAIL' else 'NOT_TESTED'),
                      'detail': 'Independent result', 'command_tail': 'Actual failed assertion',
                      'assertion_evidence': {'reports': [{'file': relative,
                          'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}],
                          'expected_tests': scenario['expected_tests'], 'missing_tests': []}}
            if scenario['kind'] == 'gameplay':
                video = 'acceptance/%s/minecraft_client_capture.mp4' % scenario['id']
                (self.root / ('round-%02d' % number) / video).write_bytes(b'video')
                result.update(video=video, video_capture={'status': 'PASS', 'scenario_window_complete': True})
            results.append(result)
        source = source or self.source
        state = acceptance.build_state(self.catalog, self.plan,
            {'spec_sha256': self.catalog['spec_sha256'], 'scenarios': results}, source, number)
        self.ledger = progress.record_results(self.ledger, self.catalog, self.plan, state, number, source)

    def update(self, source=None):
        return assignment.update(self.root, self.catalog, self.plan, self.ledger, source or self.source)

    def request(self, current, source=None):
        return assignment.request(self.catalog, self.plan, self.ledger, source or self.source,
                                  current, job_root=self.root)['scenarios']

    def test_current_persists_over_restart_source_change_and_unrelated_new_scenario(self):
        first = self.update()
        self.assertEqual(first['current_task_id'], self.groups[0]['id'])
        self.record([0])
        second = self.update('b' * 64)
        self.assertEqual(second['current_task_id'], first['current_task_id'])
        self.assertEqual(len(second['current_task']['unresolved_requirement_ids']), 2)
        self.assertEqual(assignment.update(self.root, self.catalog, self.plan, self.ledger,
                         'b' * 64)['current_task_id'], first['current_task_id'])

    def test_partial_task_cannot_advance_and_agent_claims_have_no_effect(self):
        first = self.update()
        self.record([0])
        (self.root / 'handoff.json').write_text(json.dumps({'all_passed': True}))
        second = self.update()
        self.assertEqual(second['current_task_id'], first['current_task_id'])
        self.assertEqual(len(second['current_task']['unresolved_requirement_ids']), 1)
        self.assertEqual(second['completed_task_ids'], [])

    def test_full_independently_verified_task_advances(self):
        first = self.update()
        self.record([0, 1])
        second = self.update()
        self.assertNotEqual(second['current_task_id'], first['current_task_id'])
        self.assertIn(first['current_task_id'], second['completed_task_ids'])
        self.assertEqual(second['regression_requirement_ids'], self.groups[0]['requirement_ids'])

    def test_previous_stale_task_blocks_advance_until_regression_verified(self):
        self.update()
        self.record([0, 1])
        second = self.update()
        self.source = 'b' * 64
        self.record([2], 2)
        blocked = self.update()
        self.assertEqual(blocked['current_task_id'], second['current_task_id'])
        self.assertEqual(blocked['status'], 'regression_verification')
        self.assertEqual({s['id'] for s in self.request(blocked)}, {'scenario-00', 'scenario-01'})
        self.record([0, 1], 3)
        next_task = self.update()
        self.assertEqual(next_task['current_task_id'], self.groups[2]['id'])

    def test_focused_queue_ignores_unrelated_unseen_tasks(self):
        first = self.update()
        self.assertEqual({s['id'] for s in self.request(first)}, {'scenario-00', 'scenario-01'})
        self.record([0], status='FAIL')
        self.assertEqual(self.request(self.update())[0]['id'], 'scenario-01')

    def test_focus_with_room_adds_one_previous_regression(self):
        self.update()
        self.record([0, 1])
        self.update()
        next_task = self.update('b' * 64)
        selected = self.request(next_task, 'b' * 64)
        self.assertEqual(selected[0]['id'], 'scenario-02')
        self.assertEqual(len(selected), 2)
        self.assertIn(selected[1]['id'], ('scenario-00', 'scenario-01'))

    def test_changed_definition_revokes_planning_checkpoint(self):
        self.update()
        self.record([0, 1])
        self.update()
        self.plan['scenarios'][0]['expected_tests'][0]['name'] = 'changed'
        changed = self.update()
        self.assertNotIn(self.groups[0]['id'], changed['completed_task_ids'])

    def test_changed_requirement_mapping_revokes_checkpoint(self):
        self.update()
        self.record([0, 1])
        self.update()
        self.plan['scenarios'][0]['requirement_ids'] = self.groups[1]['requirement_ids']
        self.assertNotIn(self.groups[0]['id'], self.update()['completed_task_ids'])

    def test_missing_and_modified_xml_cannot_advance_or_skip_execution(self):
        first = self.update()
        self.record([0, 1])
        path = self.root / 'round-01/acceptance/scenario-00/assertions/report.xml'
        path.write_bytes(b'tampered')
        current = self.update()
        self.assertEqual(current['current_task_id'], first['current_task_id'])
        self.assertEqual([s['id'] for s in self.request(current)], ['scenario-00'])
        path.unlink()
        self.assertEqual([s['id'] for s in self.request(self.update())], ['scenario-00'])
        self.assertEqual(self.ledger['scenarios']['scenario-00']['latest']['result']['status'], 'TEST_PASSED')

    def test_synthetic_string_reports_cannot_advance(self):
        self.update()
        self.record([0, 1])
        self.ledger['scenarios']['scenario-00']['latest']['result']['assertion_evidence']['reports'] = ['fake.xml']
        self.assertEqual(self.update()['current_task_id'], self.groups[0]['id'])

    def test_missing_gameplay_video_prevents_completion(self):
        self.plan['scenarios'][0]['kind'] = 'gameplay'
        self.plan['requirements'][0]['kind'] = 'gameplay'
        self.update()
        self.record([0, 1])
        (self.root / 'round-01/acceptance/scenario-00/minecraft_client_capture.mp4').unlink()
        self.assertEqual(self.update()['current_task_id'], self.groups[0]['id'])

    def test_failure_survives_source_change_and_nonexecution_until_verified_pass(self):
        self.record([0], 1, 'FAIL')
        self.source = 'b' * 64
        failed = self.update()['unresolved_failures']
        self.assertEqual(failed[0]['id'], 'scenario-00')
        self.assertTrue(failed[0]['needs_recheck'])
        self.assertEqual(failed[0]['command_tail'], 'Actual failed assertion')
        self.record([0], 2, 'NOT_TESTED')
        self.assertEqual(len(self.update()['unresolved_failures']), 1)
        self.record([0], 3)
        self.assertEqual(self.update()['unresolved_failures'], [])

    def test_all_verified_is_review_required_never_ready_for_production(self):
        self.update()
        self.record([0, 1, 2, 3])
        result = self.update()
        self.assertEqual(result['status'], 'all_verified_requires_review')
        self.assertIsNone(result['current_task'])
        self.assertEqual(self.request(result), [])
        self.assertEqual(result['pending_task_count'], 0)

    def test_failure_memory_survives_more_than_sixteen_nonexecuted_rechecks(self):
        self.record([0], 1, 'FAIL')
        self.update()
        for number in range(2, 21):
            self.record([0], number, 'NOT_TESTED')
            result = self.update()
        self.assertEqual(result['unresolved_failure_count'], 1)
        self.assertEqual(result['unresolved_failures'][0]['round'], 1)
        self.record([0], 21)
        self.assertEqual(self.update()['unresolved_failure_count'], 0)

    def test_same_round_retry_can_resolve_failure_but_later_failure_reopens_it(self):
        self.record([0], 1, 'FAIL')
        self.update()
        self.record([0], 1)
        self.assertEqual(self.update()['unresolved_failure_count'], 0)
        self.record([0], 1, 'FAIL')
        self.assertEqual(self.update()['unresolved_failure_count'], 1)

    def test_public_read_never_changes_artifact(self):
        expected = self.update()
        path = self.root / assignment.FILENAME
        before = path.read_bytes()
        self.assertEqual(assignment.read(self.root, self.catalog), expected)
        self.assertEqual(path.read_bytes(), before)

    def test_uncovered_requirements_still_receive_focus(self):
        self.plan = {'requirements': [], 'scenarios': [], 'notes': []}
        result = self.update()
        self.assertEqual(result['current_task_id'], self.groups[0]['id'])
        self.assertEqual(len(result['current_task']['unresolved_requirement_ids']), 2)
        self.assertEqual(self.request(result), [])

    def make_manual(self, requirement_index, explicit=True):
        requirement = self.plan['requirements'][requirement_index]
        sid = set(requirement['scenario_ids'])
        self.plan['scenarios'] = [s for s in self.plan['scenarios'] if s['id'] not in sid]
        requirement.update(kind='manual', scenario_ids=[])
        if explicit:
            requirement['manual_review'] = {
                'category':'physical_device',
                'reason':'Check the vibration of the physical controller attached by the user.',
                'automation_blocker':'The isolated test client has no access to the physical user controller.',
                'steps':['Connect the controller and activate defense.'],
                'expected_result':'The controller vibrates once.'}
        return requirement['id']

    def test_explicit_manual_condition_does_not_hold_mixed_task_forever(self):
        manual_id = self.make_manual(1)
        first = self.update()
        self.assertEqual(first['current_task']['unresolved_requirement_ids'],
                         [self.catalog['requirements'][0]['id']])
        self.record([0])
        second = self.update()
        self.assertEqual(second['current_task_id'], self.groups[1]['id'])
        self.assertNotIn(self.groups[0]['id'], second['completed_task_ids'])
        self.assertIn(self.groups[0]['id'], second['automatable_bindings'])
        self.assertEqual(second['manual_review_requirement_ids'], [manual_id])
        state = acceptance.build_state(self.catalog, self.plan,
            progress.current_results(self.ledger,self.catalog,self.plan,self.source),self.source)
        self.assertEqual(next(r for r in state['requirements'] if r['id']==manual_id)['status'],'NOT_TESTED')

    def test_mixed_previous_task_automatic_part_requires_real_regression(self):
        self.make_manual(1)
        self.update()
        self.record([0])
        movement = self.update()
        self.source = 'b' * 64
        self.record([1],2)
        blocked = self.update()
        self.assertEqual(blocked['current_task_id'], movement['current_task_id'])
        self.assertEqual(blocked['status'],'regression_verification')
        self.assertEqual([s['id'] for s in self.request(blocked)],['scenario-00'])
        self.record([0],3)
        self.assertEqual(self.update()['current_task_id'],self.groups[2]['id'])

    def test_all_manual_has_no_autonomous_task_and_no_fake_completion(self):
        for index in range(len(self.plan['requirements'])):self.make_manual(index)
        result = self.update()
        self.assertEqual(result['status'],'all_automatable_verified_requires_review')
        self.assertIsNone(result['current_task'])
        self.assertEqual(result['pending_task_count'],0)
        self.assertEqual(result['manual_review_requirement_count'],4)
        self.assertEqual(result['completed_task_ids'],[])
        self.assertEqual(result['completed_bindings'],{})
        self.assertEqual(self.request(result),[])

    def test_all_automatic_passed_requires_human_review_without_task_pass(self):
        self.make_manual(1)
        self.update()
        self.record([0,1,2])
        result = self.update()
        self.assertEqual(result['status'],'all_automatable_verified_requires_review')
        self.assertIsNone(result['current_task'])
        self.assertNotIn(self.groups[0]['id'],result['completed_task_ids'])
        self.assertEqual(self.request(result),[])
        self.assertEqual(result['pending_task_count'],0)

    def test_default_manual_still_blocks_until_explicit_review_instructions(self):
        self.make_manual(1,explicit=False)
        first = self.update()
        self.record([0])
        result = self.update()
        self.assertEqual(result['current_task_id'],first['current_task_id'])
        self.assertIn(self.catalog['requirements'][1]['id'],result['current_task']['unresolved_requirement_ids'])
        self.assertEqual(result['manual_review_requirement_count'],0)

    def test_all_manual_group_is_skipped_for_an_automatic_group(self):
        self.make_manual(0)
        self.make_manual(1)
        result = self.update()
        self.assertEqual(result['current_task_id'],self.groups[1]['id'])
        self.assertNotIn(self.groups[0]['id'],result['completed_task_ids'])
        self.assertEqual(result['pending_task_count'],2)

    def test_new_manual_annotation_does_not_hide_mapped_failed_test(self):
        self.record([0],1,'FAIL')
        self.plan['requirements'][0]['manual_review'] = {
            'category':'user_environment','reason':'Check the installed user environment and its controls.',
            'automation_blocker':'Only the user has access to the external environment and its controls.',
            'steps':['Open the installed client.'],'expected_result':'Controls work correctly.'}
        result = self.update()
        self.assertEqual(result['manual_review_requirement_count'],0)
        self.assertEqual(result['unresolved_failure_count'],1)
        self.assertIn(self.catalog['requirements'][0]['id'],result['current_task']['unresolved_requirement_ids'])

    def test_invalid_or_other_spec_artifact_does_not_control_selection(self):
        (self.root / assignment.FILENAME).write_text(json.dumps({'schema_version': 1,
            'spec_sha256': 'f' * 64, 'current_task_id': self.groups[2]['id'], 'completed_bindings': {}}))
        self.assertEqual(self.update()['current_task_id'], self.groups[0]['id'])

    def test_symlink_artifacts_cannot_forge_completion(self):
        self.update()
        self.record([0, 1])
        path = self.root / 'round-01/acceptance/scenario-00/assertions/report.xml'
        destination = self.root / 'outside.xml'
        destination.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(destination)
        except OSError:
            self.skipTest('Symlinks unavailable')
        self.assertEqual(self.update()['current_task_id'], self.groups[0]['id'])


if __name__ == '__main__':
    unittest.main()
