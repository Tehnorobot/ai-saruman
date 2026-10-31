"""Plan repair may cite project metadata without weakening coverage approval."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import coverage
from worker import engine
from tests import test_planner_workflow as fixture


class DiagnosticEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.quote = '"expected": ""'

    def reference(self, path='acceptance-plan.json'):
        source = self.workspace / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(self.quote, encoding='utf-8')
        return {'path': path, 'start_line': 1, 'end_line': 1, 'excerpt': self.quote}

    def test_actual_plan_repair_paths_are_diagnostics_but_not_coverage(self):
        for path in ('round-memory.json', 'acceptance-plan.json',
                     'tools/validate_acceptance_plan.py', '.lab-feedback/index.json'):
            with self.subTest(path=path):
                ref = self.reference(path)
                self.assertEqual(coverage.read_evidence(self.workspace, ref, self.quote,
                                                        diagnostic=True)['path'], path)
                if not path.startswith('.lab-feedback/'):
                    with self.assertRaisesRegex(ValueError, 'outside coverage'):
                        coverage.read_evidence(self.workspace, ref, self.quote)

    def test_numbered_multiline_tool_output_keeps_exact_source_check(self):
        ref = self.reference()
        excerpt = '"expected": "",\n"scenario_ids": []'
        (self.workspace / ref['path']).write_text(excerpt, encoding='utf-8')
        ref.update(end_line=2, excerpt=excerpt)
        for observed in ('     1\t"expected": "",\n     2\t"scenario_ids": []',
                         '1:"expected": "",\n2:"scenario_ids": []'):
            self.assertEqual(coverage.read_evidence(self.workspace, ref, observed,
                                                    diagnostic=True)['excerpt'], excerpt)
        ref['excerpt'] = '"expected": "forged"'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            coverage.read_evidence(self.workspace, ref, ref['excerpt'], diagnostic=True)

    def test_traversal_absolute_secret_and_windows_stream_paths_stay_blocked(self):
        ref = self.reference()
        for path in ('../acceptance-plan.json', '/acceptance-plan.json',
                     'C:/acceptance-plan.json', 'tools/validator.py:secret',
                     'tools/../acceptance-plan.json', 'tools//validator.py',
                     'tools\\validator.py', '.env', 'secrets.json', 'tools/key.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                coverage.read_evidence(self.workspace, {**ref, 'path': path}, self.quote,
                                       diagnostic=True)

    def test_unobserved_quotes_stay_blocked(self):
        with self.assertRaisesRegex(ValueError, 'not observed through tools'):
            coverage.read_evidence(self.workspace, self.reference(), 'unrelated output',
                                   diagnostic=True)

    def test_bad_line_and_excerpt_fields_have_specific_errors(self):
        ref = self.reference()
        for values in ({'start_line': True}, {'end_line': 0}, {'end_line': 122}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, 'start_line/end_line'):
                coverage.read_evidence(self.workspace, {**ref, **values}, self.quote, diagnostic=True)
        with self.assertRaisesRegex(ValueError, 'Invalid evidence excerpt'):
            coverage.read_evidence(self.workspace, {**ref, 'excerpt': 'short'}, self.quote,
                                   diagnostic=True)

    def test_linked_diagnostic_source_stays_blocked(self):
        target = self.workspace / 'other.json'
        target.write_text(self.quote, encoding='utf-8')
        try:
            (self.workspace / 'acceptance-plan.json').symlink_to(target)
        except OSError:
            self.skipTest('Symlinks unavailable')
        ref = {'path': 'acceptance-plan.json', 'start_line': 1, 'end_line': 1,
               'excerpt': self.quote}
        with self.assertRaises((OSError, ValueError)):
            coverage.read_evidence(self.workspace, ref, self.quote, diagnostic=True)


class PlannerDiagnosticWorkflowTests(unittest.TestCase):
    setUp = fixture.PlannerWorkflowTests.setUp
    plan = fixture.PlannerWorkflowTests.plan
    complete = fixture.PlannerWorkflowTests.complete
    verify = fixture.PlannerWorkflowTests.verify

    def response(self, jid, pid, number, prompt, logfile, **kwargs):
        logfile.write_text('\n'.join(json.dumps(row) for row in [
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 0,
                                               'aggregated_output': self.observed}},
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                                               'text': json.dumps(self.value)}}]), encoding='utf-8')
        return {'status': 'PASS', 'tail': ''}

    def test_observed_acceptance_plan_can_reach_coder_repair(self):
        self.context['acceptance_plan_notes'] = ['Invalid acceptance text']
        self.value['action'].update(kind='repair_acceptance_plan', target='acceptance-plan.json')
        source = (self.workspace / 'acceptance-plan.json').read_text(encoding='utf-8')
        lines = source.splitlines()
        line = next(i for i, text in enumerate(lines, 1) if 'expected' in text)
        self.observed = '\n'.join(f'{i:6}\t{text}' for i, text in enumerate(lines, 1))
        self.value['action']['evidence_refs'] = [{'path': 'acceptance-plan.json', 'start_line': line,
                                                  'end_line': line, 'excerpt': lines[line-1]}]
        with patch.object(engine, 'request_agent', side_effect=self.response):
            result, context = self.plan()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(context['round_action']['kind'], 'repair_acceptance_plan')

    def test_error_names_the_offending_reference(self):
        self.observed = 'some observation'
        self.value['action']['evidence_refs'] = [{'path': '.env', 'start_line': 1,
                                                  'end_line': 1, 'excerpt': 'some observation'}]
        with patch.object(engine,'poll_job',side_effect=[None,None,'USER_PAUSED']), \
                patch.object(engine, 'request_agent', side_effect=self.response):
            result, _ = self.plan()
        self.assertEqual(result['status'], 'USER_PAUSED')
        rejected=list((self.stage/'planner').glob('plan-invalid-*.json'))
        self.assertEqual(len(rejected),1)
        error=json.loads(rejected[0].read_text())['error']
        self.assertIn('action.evidence_refs[0]', error)
        self.assertIn('outside diagnostic', error)
        self.assertFalse((self.stage / 'planner/plan.json').exists())
