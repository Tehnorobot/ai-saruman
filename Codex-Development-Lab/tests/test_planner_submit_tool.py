"""Structured planner submissions use the image contract and preserve read-only boundaries."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent import dslab
from common.output_contracts import load_planner_schema


class PlannerSubmitToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = {'role': 'planner', 'planner_output_contract': 'planner_output_v1',
                     'model': 'fixture-model', 'provider': 'litellm',
                     'dslab_capability': 'fixture', 'dslab_max_calls': 10}
        self.plan = {'summary': 'Inspect the camera failure — план',
                     'action': {'kind': 'diagnose_failure', 'target': 'camera',
                                'reason': 'Read diagnostics before scheduling a repair'}}
        self.requests = []
        self.output = io.StringIO()

    def use(self, name, args, call_id='submit'):
        return {'type': 'tool_use', 'id': call_id, 'name': name, 'input': args}

    def submit(self, plan=None):
        return ([self.use('submit_planner_plan', copy.deepcopy(self.plan if plan is None else plan))], 'tool_use')

    def final(self, text=None):
        return ([{'type': 'text', 'text': json.dumps(self.plan) if text is None else text}], 'end_turn')

    def run_loop(self, answers, agent='planner', readonly=True, turns=None):
        iterator = iter(answers)

        def send(request, capability, agent):
            self.requests.append(copy.deepcopy(request))
            content, reason = next(iterator)
            # The real stream publishes public text before the executor accepts a plan.
            dslab.public_message(content, agent)
            return content, reason

        self.executor = dslab.Executor(self.task, self.root, send=send)
        with redirect_stdout(self.output):
            return self.executor.run('Plan the next action', agent=agent, readonly=readonly, turns=turns)

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def test_selected_format_example_is_available_on_demand_without_workspace_write(self):
        self.run_loop([([self.use('read_planner_format',{'name':'repair'})],'tool_use'),self.submit()])
        result=self.requests[1]['messages'][2]['content'][0]['content']
        self.assertEqual(json.loads(result)['action']['kind'],'repair_scenario')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_format_example_cannot_read_arbitrary_image_or_workspace_paths(self):
        self.executor=dslab.Executor(self.task,self.root)
        with self.assertRaises(ValueError):
            self.executor.tool('read_planner_format',{'name':'../../secret'},'planner',True)
        with self.assertRaisesRegex(ValueError,'restricted'):
            self.executor.tool('read_planner_format',{'name':'repair'},'main',False)

    def test_valid_direct_plan_is_last_canonical_public_message_without_verdict(self):
        content, reason = self.submit()
        content.insert(0, {'type': 'thinking', 'thinking': 'private', 'signature': 'signed'})
        content.insert(1, {'type': 'text', 'text': 'I have a scheduling proposal.'})
        result = self.run_loop([(content, reason)])
        self.assertEqual(result, json.dumps(self.plan, ensure_ascii=False))
        events = self.events()
        messages = [event['item']['text'] for event in events
                    if event.get('item', {}).get('type') == 'agent_message']
        self.assertEqual(messages[-1], result)
        self.assertEqual(json.loads(messages[-1]), self.plan)
        self.assertNotIn('private', self.output.getvalue())
        self.assertNotIn('PASS', self.output.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])
        tools = self.requests[0]['tools']
        self.assertEqual({tool['name'] for tool in tools}, dslab.READ_TOOL_NAMES | {'submit_planner_plan','read_planner_format'})
        submission = next(tool for tool in tools if tool['name'] == 'submit_planner_plan')
        self.assertEqual(submission['input_schema'], load_planner_schema())

    def test_malformed_nested_action_fields_receive_exact_error_then_can_be_corrected(self):
        for field, value in [('evidence_refs', [{'path': 'x', 'start_line': 'one'}]),
                             ('expected_progress', {'kind': ['invalid']}),
                             ('requirement_ids', [False]), ('unexpected', 'bad')]:
            with self.subTest(field=field):
                malformed = copy.deepcopy(self.plan)
                malformed['action'][field] = value
                self.requests = []
                self.assertEqual(json.loads(self.run_loop([self.submit(malformed), self.submit()])), self.plan)
                result = self.requests[1]['messages'][-1]['content'][0]
                self.assertEqual(result['type'], 'tool_result')
                self.assertTrue(result['is_error'])
                self.assertEqual(result['tool_use_id'], 'submit')
                self.assertLessEqual(len(result['content'].encode('utf-8')), 1600)
                self.assertIn('action', result['content'])
                if field != 'unexpected':
                    self.assertIn(field, result['content'])

    def test_invalid_submissions_use_budget_without_coder_or_writes(self):
        malformed = copy.deepcopy(self.plan)
        malformed['action']['target'] = {'not': 'an identifier'}
        with self.assertRaisesRegex(RuntimeError, r'step limit reached'):
            self.run_loop([self.submit(malformed), self.submit(malformed)],turns=2)
        self.assertEqual(self.executor.calls, 2)
        self.assertEqual(self.executor.delegations, 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_json_string_and_wrapped_plan_are_not_direct_plan_objects(self):
        for malformed in (json.dumps(self.plan), {'plan': self.plan}):
            with self.subTest(malformed=malformed):
                self.requests = []
                self.assertEqual(json.loads(self.run_loop([self.submit(malformed), self.submit()])), self.plan)
                self.assertTrue(self.requests[1]['messages'][-1]['content'][0]['is_error'])

    def test_plain_json_final_gets_one_reminder_then_requires_tool(self):
        self.assertEqual(json.loads(self.run_loop([self.final(), self.submit()])), self.plan)
        reminder = self.requests[1]['messages'][-1]['content']
        self.assertIn('submit_planner_plan', reminder)
        self.assertIn('read_planner_format', reminder)

    def test_repeated_plain_json_and_malformed_prose_are_never_repaired(self):
        with self.assertRaisesRegex(RuntimeError, 'step limit reached'):
            self.run_loop([self.final(), self.final('{"action":')],turns=2)
        self.assertEqual(self.executor.calls, 2)

    def test_missing_diagnostic_read_cannot_submit_even_with_refs(self):
        self.task['planner_evidence_required'] = True
        folder = self.root / '.lab-feedback'
        folder.mkdir()
        path = '.lab-feedback/scenario-0-log.txt'
        quote = 'AssertionError: camera did not move'
        (self.root / path).write_text(quote, encoding='utf-8')
        (folder / 'index.json').write_text(json.dumps({'files': [{'path': path}]}), encoding='utf-8')
        self.plan['action']['evidence_refs'] = [
            {'path': path, 'start_line': 1, 'end_line': 1, 'excerpt': quote}]
        with self.assertRaisesRegex(RuntimeError, 'step limit reached'):
            self.run_loop([self.submit(), self.submit()],turns=2)
        self.assertTrue(all(request['tool_choice'] == {'type': 'tool', 'name': 'read_lines'}
                            for request in self.requests))
        self.assertIn('Read the actual named diagnostic file',
                      self.requests[1]['messages'][-1]['content'][0]['content'])

    def test_diagnostic_read_and_citation_gate_precede_acceptance(self):
        self.task['planner_evidence_required'] = True
        folder = self.root / '.lab-feedback'
        folder.mkdir()
        path = '.lab-feedback/scenario-0-log.txt'
        quote = 'AssertionError: camera did not move'
        (self.root / path).write_text(quote, encoding='utf-8')
        (folder / 'index.json').write_text(json.dumps({'files': [{'path': path}]}), encoding='utf-8')
        read = ([self.use('read_lines', {'path': path, 'start_line': 1}, 'read')], 'tool_use')
        cited = copy.deepcopy(self.plan)
        cited['action']['evidence_refs'] = [{'path': path, 'start_line': 1, 'end_line': 1, 'excerpt': quote}]
        self.assertEqual(json.loads(self.run_loop([read, self.submit(), self.submit(cited)])), cited)
        self.assertNotIn('tool_choice', self.requests[-1])

    def test_multiple_tool_batch_is_rejected_before_any_action_runs(self):
        batch = ([self.use('read_lines', {'path': 'missing', 'start_line': 1}, 'read'),
                  self.use('write_file', {'path': 'must-not-exist', 'content': 'bad'}, 'write'),
                  self.use('submit_planner_plan', self.plan)], 'tool_use')
        self.run_loop([batch, self.submit()])
        results = self.requests[1]['messages'][-1]['content']
        self.assertEqual({result['tool_use_id'] for result in results}, {'read', 'write', 'submit'})
        self.assertTrue(all(result['is_error'] for result in results))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(any(event.get('item', {}).get('type') == 'tool_result' for event in self.events()))

    def test_coder_delegate_and_legacy_planner_cannot_use_submission_tool(self):
        for role, agent, readonly, contract in [('coder', 'main', False, 'planner_output_v1'),
                                               ('planner', 'reviewer-1', True, 'planner_output_v1'),
                                               ('planner', 'planner', False, 'planner_output_v1'),
                                               ('planner', 'planner', True, None)]:
            with self.subTest(role=role, agent=agent, readonly=readonly, contract=contract):
                self.task['role'] = role
                self.task['planner_output_contract'] = contract
                self.requests = []
                self.run_loop([self.submit(), self.final('legacy response')], agent=agent, readonly=readonly)
                self.assertNotIn('submit_planner_plan', {tool['name'] for tool in self.requests[0]['tools']})
                self.assertTrue(self.requests[1]['messages'][-1]['content'][0]['is_error'])
                with self.assertRaisesRegex(ValueError, 'restricted'):
                    self.executor.tool('submit_planner_plan', self.plan, agent, readonly)
        self.assertNotIn('submit_planner_plan', dslab.READ_TOOL_NAMES)
        self.assertNotIn('submit_planner_plan', {tool['name'] for tool in dslab.TOOLS})

    def test_shared_schema_cannot_be_overridden_by_workspace(self):
        folder = self.root / 'schemas'
        folder.mkdir()
        (folder / 'planner_output.schema.json').write_text('{"type":"string"}', encoding='utf-8')
        self.run_loop([self.submit()])
        submission = next(tool for tool in self.requests[0]['tools'] if tool['name']=='submit_planner_plan')
        self.assertEqual(submission['input_schema'], load_planner_schema())

    def test_reminders_stay_inside_existing_request_budget(self):
        self.task['dslab_max_calls'] = 1
        with self.assertRaisesRegex(RuntimeError, 'request limit'):
            self.run_loop([self.final(), self.submit()])
        self.assertEqual(len(self.requests), 1)

    def test_output_limit_cannot_submit_or_trigger_correction(self):
        content, _ = self.submit()
        with self.assertRaisesRegex(RuntimeError, 'max_tokens'):
            self.run_loop([(content, 'max_tokens')])
        self.assertEqual(len(self.requests), 1)

    def test_signed_thinking_and_tool_results_remain_paired_on_correction(self):
        malformed = copy.deepcopy(self.plan)
        malformed['action']['target'] = False
        content, reason = self.submit(malformed)
        signed = {'type': 'thinking', 'thinking': 'private reasoning', 'signature': 'unchanged-signature'}
        content.insert(0, signed)
        self.run_loop([(content, reason), self.submit()])
        history = self.requests[1]['messages']
        self.assertEqual(history[-2], {'role': 'assistant', 'content': content})
        self.assertEqual(history[-2]['content'][0], signed)
        self.assertEqual(history[-1]['content'][0]['tool_use_id'], 'submit')
        self.assertTrue(history[-1]['content'][0]['is_error'])
        self.assertNotIn('private reasoning', self.output.getvalue())
