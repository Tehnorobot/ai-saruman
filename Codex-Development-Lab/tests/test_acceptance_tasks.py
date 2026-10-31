"""Structural task grouping cannot turn partial or historical proof into PASS."""
import copy
from pathlib import Path
import unittest

from common import acceptance
from common import acceptance_tasks as tasks


class AcceptanceTasksTests(unittest.TestCase):
    def catalog(self):
        return acceptance.build_catalog('# Mod\n\nIntro\n\n## Combat\n\n- Attack\n- Defend\n\n## API\n\n- Integrate\n')

    def state(self, catalog, statuses):
        return {'spec_sha256': catalog['spec_sha256'], 'summary': {'untouched': True},
            'requirements': [{**copy.deepcopy(item), 'status': status}
                for item, status in zip(catalog['requirements'], statuses)]}

    def test_exact_partition_deterministic_ids_and_no_catalog_mutation(self):
        catalog = self.catalog()
        before = copy.deepcopy(catalog)
        grouped = tasks.build_tasks(catalog)
        self.assertEqual(grouped, tasks.build_tasks(copy.deepcopy(catalog)))
        self.assertEqual([rid for group in grouped for rid in group['requirement_ids']],
                         [item['id'] for item in catalog['requirements']])
        self.assertEqual(len({group['id'] for group in grouped}), len(grouped))
        self.assertEqual(before, catalog)

    def test_only_all_members_pass_completes_task(self):
        catalog = self.catalog()
        state = self.state(catalog, ['TEST_PASSED', 'TEST_PASSED', 'NOT_TESTED', 'TEST_PASSED'])
        previous = copy.deepcopy(state)
        tasks.annotate(state, catalog)
        self.assertEqual(state['tasks'][1]['status'], 'IN_PROGRESS')
        self.assertEqual(state['task_summary']['test_passed'], 2)
        self.assertEqual(previous['requirements'], state['requirements'])
        self.assertEqual(previous['summary'], state['summary'])

    def test_failure_is_visible_even_if_other_members_pass(self):
        catalog = self.catalog()
        state = self.state(catalog, ['NOT_TESTED', 'TEST_PASSED', 'FAIL', 'NOT_TESTED'])
        tasks.annotate(state, catalog)
        self.assertEqual(state['tasks'][1]['status'], 'FAIL')
        self.assertEqual(state['task_summary']['failed'], 1)

    def test_stale_completion_requires_reverification(self):
        catalog = self.catalog()
        state = self.state(catalog, ['NOT_TESTED', 'TEST_PASSED', 'STALE', 'NOT_TESTED'])
        state['history_summary'] = {'test_passed': 240}
        tasks.annotate(state, catalog)
        self.assertEqual(state['tasks'][1]['status'], 'STALE')
        self.assertEqual(state['task_summary']['test_passed'], 0)
        state['requirements'][1]['status'] = 'NOT_TESTED'
        tasks.annotate(state, catalog)
        self.assertEqual(state['tasks'][1]['status'], 'IN_PROGRESS')

    def test_unknown_or_missing_leaf_cannot_promote_task(self):
        catalog = self.catalog()
        state = self.state(catalog, ['TEST_PASSED'] * 4)
        state['requirements'][2]['id'] = 'REQ-invented'
        tasks.annotate(state, catalog)
        self.assertEqual(state['tasks'][1]['status'], 'IN_PROGRESS')

    def test_duplicate_leaf_is_unverified(self):
        catalog = self.catalog()
        state = self.state(catalog, ['TEST_PASSED'] * 4)
        state['requirements'].append(copy.deepcopy(state['requirements'][1]))
        tasks.annotate(state, catalog)
        self.assertEqual(state['tasks'][1]['status'], 'IN_PROGRESS')

    def test_changed_spec_does_not_reuse_previous_verdicts(self):
        catalog = self.catalog()
        state = self.state(catalog, ['TEST_PASSED'] * 4)
        changed = acceptance.build_catalog('# Mod\n\nIntro\n\n## Combat\n\n- Attack better\n- Defend\n\n## API\n\n- Integrate\n')
        tasks.annotate(state, changed)
        self.assertEqual(state['task_summary']['test_passed'], 0)
        self.assertEqual(state['task_summary']['not_tested'], 3)
        self.assertEqual([item['id'] for item in tasks.build_tasks(catalog)],
                         [item['id'] for item in tasks.build_tasks(changed)])

    def test_large_sections_split_only_at_existing_h3_boundaries(self):
        spec = '# Mod\n\n## Large\n\nParent rule\n\n'
        spec += '\n\n'.join('### Part %d\n\n%s' % (i, '\n'.join('- Rule %d' % n for n in range(20))) for i in range(2))
        catalog = acceptance.build_catalog(spec)
        grouped = tasks.build_tasks(catalog)
        self.assertEqual([len(task['requirement_ids']) for task in grouped], [1, 20, 20])
        unsplittable = acceptance.build_catalog('# Mod\n\n## Flat\n\n' + '\n'.join('- Rule %d' % n for n in range(60)))
        self.assertEqual(len(tasks.build_tasks(unsplittable)), 1)
        self.assertEqual(len(tasks.build_tasks(unsplittable)[0]['requirement_ids']), 60)

    def test_scenarios_are_unique_and_not_claimed_as_fewer_executions(self):
        catalog = self.catalog()
        state = self.state(catalog, ['NOT_TESTED'] * 4)
        state['requirements'][1]['scenarios'] = [{'id': 'shared', 'status': 'FAIL'}]
        state['requirements'][2]['scenario_ids'] = ['shared', 'other']
        tasks.annotate(state, catalog)
        self.assertEqual(state['tasks'][1]['scenario_ids'], ['other', 'shared'])
        self.assertEqual(state['tasks'][1]['summary']['not_tested'], 2)

    def test_empty_catalog_has_no_completed_task(self):
        state = {'spec_sha256': 'empty', 'requirements': []}
        tasks.annotate(state, {'spec_sha256': 'empty', 'requirements': []})
        self.assertEqual(state['tasks'], [])
        self.assertEqual(state['task_summary']['test_passed'], 0)

    def test_actual_spec_is_reduced_without_losing_any_source_requirement(self):
        project = Path(__file__).resolve().parents[1] / 'projects' / '94a2c07a7a5b44'
        if not (project / 'ТЗ.md').exists():
            self.skipTest('Local Auto Defense fixture not packaged')
        catalog = acceptance.build_catalog(acceptance.read_spec(project))
        grouped = tasks.build_tasks(catalog)
        self.assertEqual(len(catalog['requirements']), 240)
        self.assertEqual(len(grouped), 29)
        source_ids = [item['id'] for item in catalog['requirements']]
        grouped_ids = [rid for task in grouped for rid in task['requirement_ids']]
        self.assertCountEqual(source_ids, grouped_ids)
        self.assertEqual(len(grouped_ids), len(set(grouped_ids)))


if __name__ == '__main__':
    unittest.main()
