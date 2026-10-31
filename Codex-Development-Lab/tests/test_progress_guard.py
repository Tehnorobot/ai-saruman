"""Regression checks for paid-loop stalls, independent evidence and explicit resume."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from worker.progress_guard import record_round


class ProgressGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = {'checks': [{'label': 'gradle_build', 'status': 'PASS', 'exit_code': 0}]}

    def record(self, n, **kwargs):
        return record_round(self.root, n, kwargs.pop('sourcehash', 'a' * 64),
                            kwargs.pop('report', self.report), **kwargs)

    def scenario(self, number, sid, status='PASS'):
        folder = self.root / f'round-{number:02d}'
        folder.mkdir()
        (folder / 'acceptance_results.json').write_text(json.dumps({'scenarios': [
            {'id': sid, 'test_status': status,
             'status': 'TEST_PASSED' if status == 'PASS' else 'FAIL', 'attempt_round': number}
        ]}), encoding='utf-8')

    def test_three_unchanged_paid_rounds_stop_after_baseline(self):
        self.assertFalse(self.record(20)['stalled'])
        self.assertEqual(self.record(21)['streak'], 1)
        self.assertEqual(self.record(22)['streak'], 2)
        state = self.record(23)
        self.assertTrue(state['stalled'])
        self.assertEqual(state['streak'], 3)
        self.assertEqual(json.loads((self.root/'progress-guard.json').read_text())['last_round'], 23)

    def test_controller_replay_does_not_spend_extra_streak(self):
        self.record(20)
        one = self.record(21)
        self.assertEqual(one, self.record(21))
        self.assertEqual(one, self.record(19))
        self.assertEqual(self.record(22)['streak'], 2)

    def test_verification_only_rounds_do_not_increment_streak(self):
        self.record(20)
        self.record(21)
        self.assertEqual(self.record(22, model_ran=False)['streak'], 1)
        self.assertEqual(self.record(23, model_ran=False)['streak'], 1)

    def test_new_independent_scenario_counts_even_if_it_fails(self):
        self.record(20)
        self.record(21)
        self.scenario(22, 'new-actual-check', 'FAIL')
        result = self.record(22)
        self.assertEqual(result['streak'], 0)
        self.assertIn('new_scenarios_executed', result['reasons'])
        self.scenario(23, 'new-actual-check', 'FAIL')
        self.assertEqual(self.record(23)['streak'], 1)

    def test_rotating_old_checks_do_not_create_progress_forever(self):
        for n, sid in ((20, 'a'), (21, 'b'), (22, 'a'), (23, 'b'), (24, 'a')):
            self.scenario(n, sid)
            result = self.record(n)
        self.assertTrue(result['stalled'])

    def test_actual_source_change_resets_streak(self):
        self.record(20)
        self.record(21)
        result = self.record(22, sourcehash='b' * 64)
        self.assertEqual(result['streak'], 0)
        self.assertIn('source_changed', result['reasons'])

    def test_assigned_task_source_edits_do_not_hide_coverage_stall(self):
        report={**self.report,'task_assignment':{'current_task_id':'task-one'}}
        for n in range(20,24):
            state=self.record(n,sourcehash=format(n,'064x'),report=report)
        self.assertTrue(state['stalled'])
        self.assertNotIn('source_changed',state['reasons'])

    def test_assigned_task_exception_churn_does_not_count_as_coverage(self):
        for n in range(20,24):
            report={'task_assignment':{'current_task_id':'task-one'},
                'checks':[{'label':'build','status':'FAIL','tail':'Different exception '+str(n)}]}
            state=self.record(n,sourcehash=format(n,'064x'),report=report)
        self.assertTrue(state['stalled'])

    def test_correction_after_failure_is_new_evidence(self):
        bad = {'checks': [{'label': 'build', 'status': 'FAIL', 'exit_code': 1,
                           'tail': 'PermissionError: blocked'}]}
        self.record(20, report=bad)
        self.record(21, report=bad)
        self.assertEqual(self.record(22, model_ran=False)['streak'], 0)

    def test_claims_and_placeholders_cannot_reset_streak(self):
        self.record(20)
        self.record(21)
        fake = dict(self.report, agent_summary='All done', acceptance={
            'summary': {'test_passed': 999},
            'requirements': [{'id': 'req', 'status': 'NOT_TESTED',
                              'scenarios': [{'id': 'placeholder', 'status': 'NOT_TESTED',
                                             'attempt_round': 22}]}]})
        self.assertEqual(self.record(22, report=fake)['streak'], 2)

    def test_explicit_resume_grants_three_fresh_paid_rounds(self):
        for n in range(20, 24):
            self.record(n)
        self.assertEqual(self.record(24, generation=1)['streak'], 1)
        self.assertEqual(self.record(25, generation=1)['streak'], 2)
        self.assertTrue(self.record(26, generation=1)['stalled'])

    def test_verification_only_progress_resets_streak(self):
        self.record(20)
        self.record(21)
        self.scenario(22, 'next-batch')
        state = self.record(22, model_ran=False)
        self.assertEqual(state['streak'], 0)
        self.assertFalse(state['model_ran'])

    def test_timing_and_stack_line_noise_do_not_count_as_error_progress(self):
        def failed(n):
            return {'checks': [{'label': 'build', 'status': 'FAIL', 'exit_code': 1,
                'tail': f'PermissionError: blocked at line {n}, failed after {n}.25 seconds'}]}
        for n in range(20, 24):
            result = self.record(n, report=failed(n))
        self.assertTrue(result['stalled'])

    def test_symlink_state_cannot_read_external_file(self):
        outside = self.root/'other.json'
        outside.write_text('{}')
        try:
            (self.root/'progress-guard.json').symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable')
        with self.assertRaises((OSError, ValueError)):
            self.record(20)
        self.assertEqual(outside.read_text(), '{}')

    def test_oversized_state_is_not_silently_reset(self):
        (self.root/'progress-guard.json').write_text(' ' * (1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            self.record(20)


if __name__ == '__main__':
    unittest.main()
