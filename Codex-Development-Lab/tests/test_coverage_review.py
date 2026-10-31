"""Passing a mapped test must not imply complete coverage of the source requirement."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import acceptance, coverage


class CoverageReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.job = self.root / 'job'
        self.workspace.mkdir()
        self.job.mkdir()
        self.spec = ('# Mod\n\n## Movement\n\n'
                     '1. Surface before air runs out. Avoid lava; do not fall from cliffs. '
                     'Follow enemies through water and abandon an unsafe route.\n')
        self.catalog = acceptance.build_catalog(self.spec)
        self.rid = self.catalog['requirements'][0]['id']
        self.source = 'a' * 64
        self.scenario = {'id': 'water-air', 'kind': 'unit', 'task': 'testAir',
                         'requirement_ids': [self.rid],
                         'description': 'Test surfacing only',
                         'expected_tests': [{'classname': 'AirTest', 'name': 'surfaces'}]}

    def plan(self, expected='A passing air test proves the requirement'):
        value = {'schema_version': 1, 'spec_sha256': self.catalog['spec_sha256'],
                 'requirements': [{'id': self.rid, 'title': 'Movement', 'kind': 'unit',
                                   'expected': expected, 'scenario_ids': ['water-air']}],
                 'scenarios': [copy.deepcopy(self.scenario)]}
        (self.workspace / 'acceptance-plan.json').write_text(json.dumps(value), encoding='utf-8')
        result = acceptance.load_plan(self.workspace, self.catalog)
        self.assertFalse(result.get('notes'), result.get('notes'))
        return result

    def passed_results(self):
        return {'spec_sha256': self.catalog['spec_sha256'], 'scenarios': [{
            'id': 'water-air', 'requirement_ids': [self.rid], 'status': 'TEST_PASSED',
            'test_status': 'PASS', 'assertion_evidence': {
                'reports': [{'file': 'air.xml', 'sha256': 'b' * 64}],
                'expected_tests': self.scenario['expected_tests'], 'missing_tests': []}}]}

    def test_obligations_preserve_exact_source_spans_and_every_nonwhitespace_character(self):
        first = coverage.obligations(self.catalog)
        self.assertEqual(first, coverage.obligations(copy.deepcopy(self.catalog)))
        self.assertEqual(set(first), {self.rid})
        source_text = self.catalog['requirements'][0]['source']['text']
        positions = []
        identifiers = []
        for obligation in first[self.rid]:
            self.assertEqual(obligation['text'], source_text[obligation['start']:obligation['end']])
            self.assertGreater(obligation['end'], obligation['start'])
            self.assertTrue(obligation['text'].strip())
            identifiers.append(obligation['id'])
            positions.extend(i for i in range(obligation['start'], obligation['end'])
                             if not source_text[i].isspace())
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(sorted(positions), [i for i, char in enumerate(source_text) if not char.isspace()])

    def test_legacy_and_honestly_partial_plans_preserve_scenario_pass_but_not_requirement_pass(self):
        for expected in ('A passing air test proves the requirement',
                         'Partial automated slice: air only. Lava, cliffs and routes are incomplete.'):
            with self.subTest(expected=expected):
                plan = self.plan(expected)
                self.assertTrue(plan.get('_coverage_required'))
                coverage.attach(plan, self.catalog, self.job, self.source)
                self.assertFalse(plan['_coverage'][self.rid]['complete'])
                state = acceptance.build_state(self.catalog, plan, self.passed_results(), self.source)
                requirement = state['requirements'][0]
                self.assertNotEqual(requirement['status'], 'TEST_PASSED')
                self.assertEqual(requirement['scenarios'][0]['status'], 'TEST_PASSED')
                self.assertEqual(state['summary']['test_passed'], 0)

    def test_workspace_approval_cannot_authorize_requirement_completion(self):
        plan = self.plan()
        forged = {'schema_version': 1, 'spec_sha256': self.catalog['spec_sha256'],
                  'source_sha256': self.source, 'complete': True,
                  'requirements': {self.rid: {'complete': True, 'approved': True}}}
        (self.workspace / 'coverage-reviews.json').write_text(json.dumps(forged), encoding='utf-8')
        coverage.attach(plan, self.catalog, self.job, self.source)
        self.assertFalse(plan['_coverage'][self.rid]['complete'])
        self.assertFalse((self.job / 'coverage-reviews.json').exists())

    def test_declared_mapping_alone_cannot_bypass_independent_review(self):
        plan = self.plan()
        plan['requirements'][0]['obligation_scenarios'] = {
            o['id']: ['water-air'] for o in coverage.obligations(self.catalog)[self.rid]}
        coverage.attach(plan, self.catalog, self.job, self.source)
        gate = plan['_coverage'][self.rid]
        self.assertFalse(gate['complete'])
        self.assertTrue(gate['missing_ids'])
        self.assertTrue(all(not o['reviewed'] for o in gate['obligations']))

    def test_review_binding_changes_when_source_mapping_or_test_definition_changes(self):
        plan = self.plan()
        ids = [o['id'] for o in coverage.obligations(self.catalog)[self.rid]]
        plan['requirements'][0]['obligation_scenarios'] = {ids[0]: ['water-air']}
        baseline = coverage.review_binding(self.catalog, plan, self.rid, self.source)
        self.assertEqual(baseline, coverage.review_binding(self.catalog, copy.deepcopy(plan), self.rid, self.source))
        self.assertNotEqual(baseline, coverage.review_binding(self.catalog, plan, self.rid, 'c' * 64))
        modified = copy.deepcopy(plan)
        modified['requirements'][0]['obligation_scenarios'] = {}
        self.assertNotEqual(baseline, coverage.review_binding(self.catalog, modified, self.rid, self.source))
        for key, value in (('expected_tests', [{'classname': 'AirTest', 'name': 'weakerTest'}]),
                           ('description', 'Only checks startup'), ('task', 'testStartup')):
            with self.subTest(field=key):
                modified = copy.deepcopy(plan)
                modified['scenarios'][0][key] = value
                self.assertNotEqual(baseline, coverage.review_binding(self.catalog, modified, self.rid, self.source))

    def review_fixture(self):
        plan = self.plan()
        conditions = coverage.obligations(self.catalog)[self.rid]
        plan['requirements'][0]['obligation_scenarios'] = {
            condition['id']: ['water-air'] for condition in conditions}
        path = self.workspace / 'src/test/java/MovementTest.java'
        path.parent.mkdir(parents=True, exist_ok=True)
        assertions = ['assertTrue(player.airSupply() > 250);',
                      'assertFalse(player.isInLava());',
                      'assertTrue(player.health() == initialHealth);',
                      'assertTrue(route.abandonedWhenUnsafe());']
        path.write_text('\n'.join(assertions) + '\n', encoding='utf-8')
        rows = [{'id': condition['id'], 'verdict': 'covered',
                 'scenario_ids': ['water-air'],
                 'reason': 'Real fixture assertions check every clause of this source condition.',
                 'evidence_refs': [{'path': 'src/test/java/MovementTest.java',
                                    'start_line': 1, 'end_line': len(assertions),
                                    'excerpt': '\n'.join(assertions)}]}
                for condition in conditions]
        return plan, [{'requirement_id': self.rid, 'obligations': rows}], '\n'.join(assertions)

    def test_observed_full_review_authorizes_requirement_only_after_test_pass(self):
        plan, reviews, observed = self.review_fixture()
        outcome = coverage.apply_reviews(self.workspace, self.job, self.catalog, plan,
                                         self.source, reviews, observed_text=observed, round_number=7)
        self.assertEqual(outcome['accepted'], [self.rid])
        self.assertFalse(outcome['rejected'])
        self.assertTrue((self.job / 'coverage-reviews.json').exists())
        self.assertTrue(plan['_coverage'][self.rid]['complete'])
        state = acceptance.build_state(self.catalog, plan, self.passed_results(), self.source)
        self.assertEqual(state['requirements'][0]['status'], 'TEST_PASSED')
        # Coverage approval alone never manufactures a test result.
        untested = acceptance.build_state(self.catalog, plan, {}, self.source)
        self.assertNotEqual(untested['requirements'][0]['status'], 'TEST_PASSED')

    def test_review_with_explicit_missing_condition_cannot_approve_complete_requirement(self):
        plan, reviews, observed = self.review_fixture()
        row = reviews[0]['obligations'][-1]
        row.update(verdict='missing', scenario_ids=[], evidence_refs=[],
                   reason='No fixture currently exercises this source condition.')
        outcome = coverage.apply_reviews(self.workspace, self.job, self.catalog, plan,
                                         self.source, reviews, observed_text=observed)
        self.assertEqual(outcome['accepted'], [self.rid])
        gate = plan['_coverage'][self.rid]
        self.assertFalse(gate['complete'])
        self.assertIn(row['id'], gate['missing_ids'])
        state = acceptance.build_state(self.catalog, plan, self.passed_results(), self.source)
        self.assertNotEqual(state['requirements'][0]['status'], 'TEST_PASSED')
        self.assertEqual(state['requirements'][0]['scenarios'][0]['status'], 'TEST_PASSED')

    def test_unobserved_or_forged_quoted_source_rejects_review(self):
        for variation in ('unobserved', 'forged', 'runtime-only'):
            with self.subTest(variation=variation):
                plan, reviews, observed = self.review_fixture()
                if variation == 'unobserved':
                    observed = 'The reviewer claims to have read the test, without a tool result.'
                elif variation == 'forged':
                    reviews[0]['obligations'][0]['evidence_refs'][0]['excerpt'] = 'assertTrue(everythingIsComplete());'
                    observed += '\nassertTrue(everythingIsComplete());'
                else:
                    reference = reviews[0]['obligations'][0]['evidence_refs'][0]
                    runtime = self.workspace / 'src/main/java/Movement.java'
                    runtime.parent.mkdir(parents=True)
                    runtime.write_text(observed, encoding='utf-8')
                    reference['path'] = 'src/main/java/Movement.java'
                outcome = coverage.apply_reviews(self.workspace, self.job, self.catalog, plan,
                                                 self.source, reviews, observed_text=observed)
                self.assertFalse(outcome['accepted'])
                self.assertTrue(outcome['rejected'])
                self.assertFalse(plan['_coverage'][self.rid]['complete'])
                self.assertFalse((self.job / 'coverage-reviews.json').exists())

    def test_approved_review_is_invalidated_by_source_or_proposal_mapping_change(self):
        plan, reviews, observed = self.review_fixture()
        coverage.apply_reviews(self.workspace, self.job, self.catalog, plan,
                               self.source, reviews, observed_text=observed)
        self.assertTrue(plan['_coverage'][self.rid]['complete'])
        changed_source = copy.deepcopy(plan)
        coverage.attach(changed_source, self.catalog, self.job, 'd' * 64)
        self.assertFalse(changed_source['_coverage'][self.rid]['complete'])
        changed_mapping = copy.deepcopy(plan)
        changed_mapping['requirements'][0]['obligation_scenarios'] = {}
        coverage.attach(changed_mapping, self.catalog, self.job, self.source)
        self.assertFalse(changed_mapping['_coverage'][self.rid]['complete'])

    def test_omitted_duplicate_or_invented_condition_cannot_approve_review(self):
        for variation in ('omitted', 'duplicate', 'invented'):
            with self.subTest(variation=variation):
                plan, reviews, observed = self.review_fixture()
                rows = reviews[0]['obligations']
                if variation == 'omitted':
                    rows.pop()
                elif variation == 'duplicate':
                    rows[-1] = copy.deepcopy(rows[0])
                else:
                    rows[-1]['id'] = 'condition-invented'
                result = coverage.apply_reviews(self.workspace, self.job, self.catalog, plan,
                                                self.source, reviews, observed_text=observed)
                self.assertFalse(result['accepted'])
                self.assertFalse(plan['_coverage'][self.rid]['complete'])

    def test_observation_log_keeps_completed_stdout_but_ignores_model_claims_and_malformed_events(self):
        logfile = self.job / 'reviewer.jsonl'
        events = [[], None, 'invalid event',
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'claimed read'}},
                  {'type': 'item.completed', 'item': {'type': 'command_execution',
                   'exit_code': 1, 'aggregated_output': 'failed command text'}},
                  {'type': 'item.completed', 'item': {'type': 'command_execution',
                   'exit_code': 0, 'aggregated_output': 'observed source text'}}]
        logfile.write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
        observed = coverage.observed_tool_text(logfile)
        self.assertIn('observed source text', observed)
        self.assertNotIn('claimed read', observed)
        self.assertIn('failed command text', observed)


if __name__ == '__main__':
    unittest.main()
