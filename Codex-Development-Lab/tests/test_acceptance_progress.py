"""Acceptance fairness, source isolation and durable controller-owned history."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import acceptance
from common import acceptance_progress as progress


class AcceptanceProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = acceptance.build_catalog('\n\n'.join('- Requirement %d' % i for i in range(17)))
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

    def results(self, scenarios, status='TEST_PASSED'):
        return {'spec_sha256': self.catalog['spec_sha256'], 'scenarios': [
            {'id': s['id'], 'requirement_ids': s['requirement_ids'], 'status': status,
             'test_status': 'PASS' if status == 'TEST_PASSED' else 'FAIL',
             'assertion_evidence': {'reports': ['junit/report.xml'],
                'expected_tests': s['expected_tests'], 'missing_tests': []},
             'detail': 'Independent execution'} for s in scenarios]}

    def record(self, scenarios, number, source=None, status='TEST_PASSED'):
        source = source or self.source
        state = acceptance.build_state(self.catalog, self.plan, self.results(scenarios, status), source, number)
        self.ledger = progress.record_results(self.ledger, self.catalog, self.plan, state, number, source)
        return state

    def select(self, source=None):
        return progress.select_request(self.catalog, self.plan, self.ledger, source or self.source)['scenarios']

    def state(self, source=None):
        source = source or self.source
        current = acceptance.build_state(self.catalog, self.plan,
            progress.current_results(self.ledger, self.catalog, self.plan, source), source)
        return progress.decorate_state(current, self.catalog, self.plan, self.ledger, source)

    def test_changing_source_each_round_does_not_starve_scenarios_nine_to_seventeen(self):
        seen = []
        for number in range(1, 6):
            source = '%064x' % number
            selected = self.select(source)
            seen.extend(s['id'] for s in selected)
            self.record(selected, number, source)
        self.assertEqual(len(set(seen)), 17)
        self.assertEqual(len(seen[:16]), len(set(seen[:16])))

    def test_same_source_accumulates_all_seventeen_and_then_queue_finishes(self):
        for number in range(1, 6):
            self.record(self.select(), number)
            progress.save_progress(self.root, self.ledger)
            self.ledger = progress.load_progress(self.root, self.catalog, self.plan)
        self.assertEqual(self.select(), [])
        state = self.state()
        self.assertEqual(state['summary']['test_passed'], 17)
        self.assertEqual(state['queue_summary']['pending_current'], 0)
        self.assertEqual(state['history_summary']['test_passed'], 17)

    def test_new_source_keeps_historical_pass_separate_from_current_confirmation(self):
        self.record(self.select(), 1)
        changed = self.state('b' * 64)
        self.assertEqual(changed['summary']['test_passed'], 0)
        self.assertEqual(changed['summary']['stale'], 4)
        self.assertEqual(changed['history_summary']['test_passed'], 4)
        self.assertEqual(changed['queue_summary']['pending_current'], 17)
        stale = [r for r in changed['requirements'] if r['status'] == 'STALE']
        self.assertEqual(stale[0]['scenarios'][0]['artifact_round'], 'round-01')

    def test_changed_expected_assertion_invalidates_same_source_proof_and_has_priority(self):
        for number in range(1, 6):
            self.record(self.select(), number)
        self.plan['scenarios'][0]['expected_tests'][0]['name'] = 'anotherAssertion'
        selected = self.select()
        self.assertEqual(selected[0]['id'], 'scenario-00')
        state = self.state()
        self.assertEqual(state['summary']['test_passed'], 16)
        self.assertEqual(state['requirements'][0]['status'], 'NOT_TESTED')

    def test_failures_cannot_monopolize_queue_and_current_fail_overrides_old_pass(self):
        selected = self.select()
        self.record(selected, 1)
        self.record(selected, 2, status='FAIL')
        self.assertTrue(all(s['id'] not in {s['id'] for s in selected} for s in self.select()))
        state = self.state()
        self.assertEqual(state['summary']['failed'], 4)
        self.assertEqual(state['summary']['test_passed'], 0)
        self.assertEqual(state['history_summary']['test_passed'], 4)
        self.assertEqual(state['queue_summary']['pending_current'], 13)

    def test_legacy_reports_backfill_all_rounds_without_promoting_undefined_proof(self):
        for number, start in ((28, 0), (29, 4)):
            state = acceptance.build_state(self.catalog, self.plan,
                self.results(self.plan['scenarios'][start:start + 4]), self.source, number)
            directory = self.root / ('round-%02d' % number)
            directory.mkdir()
            (directory / 'acceptance.json').write_text(json.dumps(state), encoding='utf-8')
        self.ledger = progress.load_progress(self.root, self.catalog, self.plan)
        self.assertEqual(self.select()[0]['id'], 'scenario-08')
        self.assertEqual(self.state()['summary']['test_passed'], 0)
        self.assertEqual(self.state()['history_summary']['test_passed'], 8)
        progress.save_progress(self.root, self.ledger)
        again = progress.load_progress(self.root, self.catalog, self.plan)
        self.assertEqual(again, self.ledger)

    def test_backfill_does_not_treat_carried_evidence_as_new_execution(self):
        state = self.record(self.plan['scenarios'][:1], 1)
        state['round'] = 2
        directory = self.root / 'round-02'
        directory.mkdir()
        (directory / 'acceptance.json').write_text(json.dumps(state), encoding='utf-8')
        legacy = progress.load_progress(self.root, self.catalog, self.plan)
        entry = legacy['scenarios']['scenario-00']
        self.assertEqual(entry['last_attempt_round'], 1)
        self.assertEqual(len(entry['history']), 1)

    def test_corrupted_ledger_is_ignored_and_wrong_spec_cannot_reuse_proof(self):
        self.record(self.select(), 1)
        progress.save_progress(self.root, self.ledger)
        path = self.root / progress.FILENAME
        corrupt = copy.deepcopy(self.ledger)
        corrupt['scenarios']['scenario-00']['latest']['source_sha256'] = '../../outside'
        path.write_text(json.dumps(corrupt), encoding='utf-8')
        self.assertEqual(progress.load_progress(self.root, self.catalog, self.plan)['scenarios'], {})
        path.write_text(json.dumps({**self.ledger, 'spec_sha256': 'c' * 64}), encoding='utf-8')
        self.assertEqual(progress.load_progress(self.root, self.catalog, self.plan)['scenarios'], {})

    def test_missing_assertions_do_not_create_pass_but_video_is_supplemental(self):
        scenarios = self.plan['scenarios'][:1]
        evidence = self.results(scenarios)
        evidence['scenarios'][0].pop('assertion_evidence')
        state = acceptance.build_state(self.catalog, self.plan, evidence, self.source, 1)
        self.ledger = progress.record_results(self.ledger, self.catalog, self.plan, state, 1, self.source)
        self.assertEqual(self.state()['history_summary']['test_passed'], 0)
        self.plan['scenarios'][0]['kind'] = 'gameplay'
        self.plan['requirements'][0]['kind'] = 'gameplay'
        self.record(scenarios, 2)
        self.assertEqual(self.state()['requirements'][0]['scenarios'][0]['status'],'TEST_PASSED')

    def test_history_is_bounded_deduplicated_and_current_artifact_origin_survives(self):
        scenario = self.plan['scenarios'][:1]
        for number in range(1, 21):
            self.record(scenario, number)
        self.record(scenario, 20)
        entry = self.ledger['scenarios'][scenario[0]['id']]
        self.assertEqual(len(entry['history']), progress.MAX_HISTORY)
        self.assertEqual(self.state()['requirements'][0]['scenarios'][0]['artifact_round'], 'round-20')

    def test_retired_scenarios_do_not_fill_current_queue(self):
        self.record(self.select(), 1)
        self.plan['scenarios'] = self.plan['scenarios'][4:]
        self.plan['requirements'] = self.plan['requirements'][4:]
        progress.save_progress(self.root, self.ledger)
        self.ledger = progress.load_progress(self.root, self.catalog, self.plan)
        self.assertEqual(self.ledger['scenarios'], {})

    def test_reusing_a_scenario_id_for_another_requirement_does_not_transfer_history(self):
        self.record(self.plan['scenarios'][:1], 1)
        old = self.plan['requirements'][0]
        new = self.plan['requirements'][1]
        self.plan['scenarios'][0]['requirement_ids'] = [new['id']]
        old['scenario_ids'] = []
        new['scenario_ids'] = ['scenario-00']
        summary = progress.progress_summary(self.ledger, self.catalog, self.plan, self.source)
        self.assertEqual(summary['requirements_ever_passed'], 0)
        self.assertEqual(summary['requirements_current_passed'], 0)

    def test_artifact_symlink_is_not_followed(self):
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps(self.ledger), encoding='utf-8')
        try:
            (self.root / progress.FILENAME).symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable')
        self.assertEqual(progress.load_progress(self.root, self.catalog, self.plan)['scenarios'], {})
        # Atomic replace removes the link itself, never writes its target.
        progress.save_progress(self.root, self.ledger)
        self.assertFalse((self.root / progress.FILENAME).is_symlink())
        self.assertEqual(json.loads(outside.read_text(encoding='utf-8')), self.ledger)


if __name__ == '__main__':
    unittest.main()
