"""Planner point repairs retain trusted scheduling context in both protocols."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from common import agent_protocol, task_graph
from common.pipeline_contracts import load_example
from worker import pipeline
from tests import test_pipeline_cycle as cycle_fixture


class PatchTaskContextTests(unittest.TestCase):
    setUp = cycle_fixture.CycleTest.setUp

    def exercise_planner_repair(self, compact):
        cycle = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        catalog, graph = cycle.decomposition()
        # Use actual accepted IDs distinct from schema examples. The second task
        # depends on the first, so an ID-only inventory would be insufficient.
        graph = deepcopy(graph)
        graph['tasks'][0].update(id='WORK-17', title='Actual first task')
        graph['tasks'][1].update(id='WORK-93', title='Actual dependent task', depends_on=['WORK-17'])
        catalog = deepcopy(catalog)
        catalog['open_questions'] = ['Which observed API supports growth?']
        catalog['question_resolutions'] = [{
            'question': catalog['open_questions'][0], 'kind': 'experiment',
            'status': 'open', 'owner': 'coder', 'condition_ids': ['C2'],
            'answer': None, 'reason': 'Requires an implementation observation',
            'source_refs': deepcopy(catalog['requirements'][0]['source_refs']),
        }]
        inputs = {
            'catalog_ref': pipeline.export_json(self.ws, 'accepted-repair-catalog.json', catalog),
            'graph_ref': pipeline.export_json(self.ws, 'accepted-repair-graph.json', graph),
            'task_states': cycle.task_states(catalog, graph),
            'budget': {'remaining_tokens': self.job['dslab_token_budget'],
                       'remaining_seconds': cycle.request_seconds('planner')},
        }
        # Mutable display exports contain different tasks; the referenced
        # accepted artifacts are the authority for this particular request.
        pipeline.export_json(self.ws, 'graph.json', load_example('TaskGraph'))
        saved_inputs, saved_job = deepcopy(inputs), deepcopy(self.job)
        expected_index = [
            {'id': 'WORK-17', 'title': 'Actual first task', 'depends_on': [],
             'condition_ids': ['C1'], 'open_questions': []},
            {'id': 'WORK-93', 'title': 'Actual dependent task', 'depends_on': ['WORK-17'],
             'condition_ids': ['C2'], 'open_questions': [
                 {'question': catalog['open_questions'][0], 'kind': 'experiment'}]},
        ]
        self.assertNotIn('task_index', inputs)
        original = self.engine.request_agent
        requested = []
        self.engine.COMPACT_PROTOCOL = compact

        def request(*args, **kwargs):
            context = json.loads(args[3].split('Input JSON:\n', 1)[1])
            name = kwargs['output_contract']
            requested.append((name, deepcopy(context)))
            if name == 'SubmissionPatch':
                repair = context['input']
                self.assertEqual(repair['task_index'], expected_index)
                self.assertEqual(repair['trusted_references']['graph_ref'], inputs['graph_ref'])
                self.assertEqual(repair['trusted_references']['catalog_ref'], inputs['catalog_ref'])
                self.assertEqual(repair['allowed_paths'], ['$.actions[0].task_ids'])
            result = original(*args, **kwargs)
            logfile = args[4]
            events = [json.loads(line) for line in logfile.read_text(encoding='utf-8').splitlines()]
            if name == 'SubmissionPatch':
                obj = json.loads(events[-1]['item']['text'])
                obj['changes'][0]['new_json'] = json.dumps([row['id'] for row in context['input']['task_index']])
            else:
                self.assertIn(name, ('PlannerDecision', 'AgentPlan'))
                self.assertEqual(context['task_index'], expected_index)
                obj = load_example('PlannerDecision')
                obj.update(scope=deepcopy(cycle.scope), strategy='implement',
                           rationale='Implement the real related tasks together', hypotheses=[],
                           deferred=[], manual_checks=[], regression_condition_ids=[], replacement_graph_ref=None)
                obj['actions'] = [{
                    'id': 'A1', 'kind': 'implement', 'task_ids': ['WORK-17', 'WORK-93'],
                    'condition_ids': ['C1', 'C2'], 'goal': 'Implement both accepted requirements',
                    'expected_observation': 'Both independent conditions pass', 'depends_on_action_ids': [],
                }]
                if compact:
                    obj = agent_protocol.to_wire(obj)
                obj['actions'][0]['task_ids'] = 'WORK-17'
            events[-1]['item']['text'] = json.dumps(obj)
            logfile.write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
            return result

        self.engine.request_agent = request
        with patch.object(cycle, 'event', wraps=cycle.event) as events:
            decision = cycle.ask(
                'PlannerDecision', 'planner', inputs, 'trusted-task-context',
                validate_extra=lambda value, observed: task_graph.validate_decision(
                    value, graph, catalog, inputs['task_states']))
        self.assertEqual([name for name, _ in requested],
                         ['AgentPlan' if compact else 'PlannerDecision', 'SubmissionPatch'])
        self.assertEqual(decision['actions'][0]['task_ids'], ['WORK-17', 'WORK-93'])
        self.assertEqual(inputs, saved_inputs)
        self.assertEqual(self.job, saved_job)
        self.assertNotIn('task_index', inputs)
        self.assertEqual(json.loads(pipeline.source_bytes(self.ws, inputs['graph_ref'])), graph)
        self.assertEqual(json.loads(pipeline.source_bytes(self.ws, inputs['catalog_ref'])), catalog)
        gateways = [call.args[1]['gateway'] for call in events.call_args_list
                    if call.args[0] == 'usage' and call.args[1].get('direction') == 'request']
        self.assertEqual(len(gateways), 2)
        for gateway in gateways:
            self.assertEqual(gateway['role_config'], {
                'role': 'planner', 'provider': 'codex', 'model': 'codex_default', 'reasoning': 'default'})
            self.assertEqual(gateway['budget'], {
                'remaining_tokens': saved_job['dslab_token_budget'],
                'remaining_seconds': inputs['budget']['remaining_seconds']})

    def test_legacy_planner_repair_retains_real_task_context(self):
        self.exercise_planner_repair(False)

    def test_compact_planner_repair_retains_real_task_context(self):
        self.exercise_planner_repair(True)
