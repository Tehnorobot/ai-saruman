"""Restart checkpoints must retain work while rechecking current artifact refs.

All model requests use the existing deterministic fake engine.
"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from common import acceptance, pipeline_memory, preparation, task_graph
from common.pipeline_contracts import validate
from worker import pipeline
from tests import test_pipeline_cycle as cycle_fixture


class CheckpointRecoveryTests(unittest.TestCase):
    setUp = cycle_fixture.CycleTest.setUp
    verified = cycle_fixture.CycleTest.verified

    def rejected_checkpoints(self, label):
        records = pipeline_memory.query(self.out, page_size=100)['records']
        payloads = [pipeline_memory.read_payload(self.out, record['record_id']) for record in records]
        return [value for value in payloads if value.get('checkpoint') == label]

    def test_invalid_cached_catalog_reuses_valid_phase_checkpoints(self):
        cycle = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        catalog, graph = cycle.decomposition()
        stale = deepcopy(catalog)
        stale['requirements'][0]['source_refs'][0]['artifact']['sha256'] = '0' * 64
        pipeline.write_json(self.out, 'pipeline/catalog.json', stale)
        count = len(self.engine.requests)

        resumed = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        result, result_graph = resumed.decomposition()

        self.assertEqual(result, catalog)
        self.assertEqual(result_graph, graph)
        self.assertEqual(len(self.engine.requests), count)
        self.assertEqual(self.rejected_checkpoints('catalog')[0]['original'], stale)

    def test_malformed_session_reuses_valid_decomposition_and_plan(self):
        cycle = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        catalog, graph = cycle.decomposition()
        decision, graph = cycle.plan(catalog, graph, {})
        pipeline.write_json(cycle.stage, 'pipeline/session.json', {
            'scope': cycle.scope, 'catalog': catalog, 'graph': graph,
            'decision': {'malformed': True}})

        with patch('worker.pipeline_verification.verify', side_effect=self.verified):
            pipeline.run(self.engine, self.job, self.ws, self.out, {})

        for contract in ('RequirementsCatalog', 'TaskGraph', 'PlannerDecision', 'CoderReport'):
            self.assertEqual(sum(name == contract for name, _, _ in self.engine.requests), 1, contract)
        replacement = pipeline.read_json(cycle.stage, 'pipeline/session.json')
        self.assertEqual(replacement['decision'], decision)
        self.assertTrue(pipeline.read_json(self.out, 'pipeline/public.json')['status']['ready'])
        self.assertEqual(self.rejected_checkpoints('session')[0]['original']['decision'], {'malformed': True})

    def test_schema_valid_session_with_unknown_task_returns_to_planner(self):
        cycle = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        catalog, graph = cycle.decomposition()
        decision, graph = cycle.plan(catalog, graph, {})
        decision = deepcopy(decision)
        decision['actions'][0]['task_ids'].append('T404')
        validate('PlannerDecision', decision)
        session = {'scope': cycle.scope, 'catalog': catalog, 'graph': graph, 'decision': decision}
        count = len(self.engine.requests)

        resumed = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        self.assertIsNone(resumed.restore_session(session))

        self.assertEqual(len(self.engine.requests), count)
        rejected = self.rejected_checkpoints('session')[0]
        self.assertEqual(rejected['original'], session)
        self.assertIn('unknown_task', rejected['error'])

    def test_replacement_graph_routes_new_external_dependency_to_package_replan(self):
        cycle = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        catalog, graph = cycle.decomposition()
        decision, _ = cycle.plan(catalog, graph, {})
        graph = deepcopy(graph)
        for task in graph['tasks']:
            task['depends_on'] = []
        cycle.ensure_handoff(catalog, graph, cycle.state.get('last_preparation_review'))
        handoff = cycle.state['preparation_handoff']
        handoff['tasks'][0].update(status='external', reason='User must grant access to their private environment',
                                   return_when='User grants access', experiment=None)
        decision = deepcopy(decision)
        decision['actions'][0].update(kind='diagnose', task_ids=['T2'], condition_ids=['C2'])
        replacement = deepcopy(graph)
        replacement['tasks'][1]['depends_on'] = ['T1']
        decision['replacement_graph_ref'] = pipeline.export_json(self.ws, 'replacement-proposal.json', replacement)
        preparation.validate_package(decision, handoff, graph, catalog)
        # Diagnostics meet the graph rules, but cannot bypass actual external access.
        task_graph.validate_decision(decision, replacement, catalog, cycle.task_states(catalog, replacement))

        def answer(name, role, inputs, label, *, validate_extra=None, **kwargs):
            value = deepcopy(decision if name == 'PlannerDecision' else replacement)
            if validate_extra:
                validate_extra(value, '')
            return value

        with patch.object(cycle, 'ask', side_effect=answer) as ask:
            with self.assertRaises(pipeline.PackageReplan) as raised:
                cycle.plan_package(catalog, graph, {}, 'plan-with-replacement', None)

        self.assertEqual([call.args[0] for call in ask.call_args_list], ['PlannerDecision', 'TaskGraph'])
        self.assertEqual(raised.exception.graph, replacement)
        self.assertIn('external', str(raised.exception.error))
        self.assertEqual(pipeline.read_json(self.out, 'pipeline/graph.json'), replacement)

    def prepared_coder(self):
        cycle = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        catalog, graph = cycle.decomposition()
        decision, graph = cycle.plan(catalog, graph, {})
        report, manifest, _ = cycle.code_and_review(catalog, graph, decision)
        return cycle, catalog, graph, decision, report, manifest

    def test_invalid_completed_contract_is_replaced_after_restart(self):
        cycle = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        accepted = cycle.ask('RequirementsCatalog', 'planner', {}, 'requirements')
        checkpoint = pipeline.read_json(cycle.stage, 'pipeline/requirements/complete.json')
        checkpoint['value']['requirements'] = 'invalid saved array'
        pipeline.write_json(cycle.stage, 'pipeline/requirements/complete.json', checkpoint)
        count = len(self.engine.requests)

        resumed = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        result = resumed.ask('RequirementsCatalog', 'planner', {}, 'requirements')

        self.assertEqual(result, accepted)
        self.assertEqual(len(self.engine.requests), count + 1)
        replacement = pipeline.read_json(cycle.stage, 'pipeline/requirements/complete.json')
        self.assertEqual(replacement['value'], accepted)
        self.assertEqual(self.rejected_checkpoints('requirements')[0]['original'], checkpoint)

    def test_invalid_saved_report_reuses_valid_phase_checkpoint(self):
        cycle, catalog, graph, decision, report, _ = self.prepared_coder()
        saved = pipeline.read_json(cycle.stage, 'pipeline/coding-state.json')
        saved['report']['changed_files'][0]['sha256'] = '0' * 64
        pipeline.write_json(cycle.stage, 'pipeline/coding-state.json', saved)
        count = len(self.engine.requests)

        resumed = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        result, _, _ = resumed.code_and_review(catalog, graph, decision)

        self.assertEqual(result, report)
        self.assertEqual(len(self.engine.requests), count)
        pipeline.source_bytes(self.ws, result['changed_files'][0])
        replacement = pipeline.read_json(cycle.stage, 'pipeline/coding-state.json')
        self.assertEqual(replacement['report'], report)
        self.assertEqual(self.rejected_checkpoints('coder-report-0')[0]['original'], saved)

    def test_malformed_saved_assignment_preserves_code_and_recovers(self):
        cycle, catalog, graph, decision, report, _ = self.prepared_coder()
        saved = pipeline.read_json(cycle.stage, 'pipeline/coding-state.json')
        saved['assignment']['scope'] = {'invalid': 'saved assignment'}
        pipeline.write_json(cycle.stage, 'pipeline/coding-state.json', saved)
        source = acceptance.source_fingerprint(self.ws)
        test_source = (self.ws / report['changed_files'][0]['path']).read_text()

        resumed = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        result, manifest, _ = resumed.code_and_review(catalog, graph, decision)

        validate('CoderReport', result)
        self.assertEqual(json.loads(pipeline.source_bytes(self.ws, result['scenario_manifest_ref'])), manifest)
        self.assertEqual(acceptance.source_fingerprint(self.ws), source)
        self.assertEqual((self.ws / report['changed_files'][0]['path']).read_text(), test_source)
        self.assertEqual(self.rejected_checkpoints('coding-state')[0]['original'], saved)

    def test_saved_report_with_corrupt_plan_ref_reuses_valid_phase_report(self):
        cycle, catalog, graph, decision, report, _ = self.prepared_coder()
        saved = pipeline.read_json(cycle.stage, 'pipeline/coding-state.json')
        saved['report']['plan_ref']['sha256'] = '0' * 64
        pipeline.write_json(cycle.stage, 'pipeline/coding-state.json', saved)
        count = len(self.engine.requests)

        resumed = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        result, _, _ = resumed.code_and_review(catalog, graph, decision)

        self.assertEqual(result, report)
        self.assertEqual(len(self.engine.requests), count)
        pipeline.source_bytes(self.ws, result['plan_ref'])
        self.assertEqual(self.rejected_checkpoints('coder-report-0')[0]['original'], saved)

    def test_changed_manifest_revalidates_both_report_checkpoints(self):
        cycle, catalog, graph, decision, report, manifest = self.prepared_coder()
        source = acceptance.source_fingerprint(self.ws)
        changed = deepcopy(manifest)
        changed['scenarios'][0]['title'] = 'Changed after the saved report'
        path = self.ws / report['scenario_manifest_ref']['path']
        path.write_text(json.dumps(changed), encoding='utf-8')
        self.assertEqual(acceptance.source_fingerprint(self.ws), source)
        count = sum(name == 'CoderReport' for name, _, _ in self.engine.requests)

        resumed = pipeline.Cycle(self.engine, self.job, self.ws, self.out, 1)
        result, result_manifest, _ = resumed.code_and_review(catalog, graph, decision)

        self.assertEqual(sum(name == 'CoderReport' for name, _, _ in self.engine.requests), count + 1)
        self.assertEqual(json.loads(pipeline.source_bytes(self.ws, result['scenario_manifest_ref'])), result_manifest)
        self.assertEqual((self.ws / 'existing.java').read_text(), 'preserve existing source')
        self.assertTrue(self.rejected_checkpoints('coder-report-0'))
        self.assertTrue(self.rejected_checkpoints('coder-0'))


if __name__ == '__main__':
    unittest.main()
