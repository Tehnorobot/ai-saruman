"""Explicit fixture prerequisites never certify product behavior or pause siblings."""
from copy import deepcopy
import hashlib
import json
import unittest

from common import agent_protocol, condition_progress, pipeline_memory
from common.pipeline_contracts import load_schema, validate, load_submission_schema
from tests import test_pipeline_verification as fixtures


class FixturePreconditionTests(unittest.TestCase):
    refresh_source = fixtures.PipelineVerificationTests.refresh_source
    run_verification = fixtures.PipelineVerificationTests.run_verification
    statuses = fixtures.PipelineVerificationTests.statuses

    def setUp(self):
        fixtures.PipelineVerificationTests.setUp(self)
        self.scenario = self.manifest['scenarios'][0]
        self.scenario['execution']['precondition_tests'] = [
            {'classname':'dev.example.PlantTests', 'name':'siteFound'}]

    def fail_fixture(self):
        self.sandbox.xml = self.sandbox.xml.replace(b'name="siteFound"/>',
            b'name="siteFound"><failure message="Expected platform is absent"/></testcase>')

    def test_failed_fixture_does_not_waive_an_observed_product_failure(self):
        self.fail_fixture()
        self.sandbox.xml = self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="No block placed"/></testcase>')
        result, report = self.run_verification()
        scenario = result['scenario_results'][0]
        self.assertEqual(scenario['status'], 'FAIL')
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED', 'FAIL', 'NOT_EXECUTED'])
        fixture = scenario['fixture_result']
        self.assertEqual(fixture['status'], 'FAIL')
        self.assertEqual(fixture['reason_code'], 'invalid_fixture')
        self.assertEqual([r['status'] for r in fixture['raw_checkpoints']], ['FAIL', 'FAIL', 'PASS'])
        self.assertIn('No block placed', fixture['raw_checkpoints'][1]['actual'])
        self.assertEqual(result['infrastructure_errors'], [])
        self.assertEqual(report['checks'][0]['status'], 'PASS')
        rows = condition_progress.observations(self.manifest, result)
        self.assertEqual({r['status'] for r in rows}, {'FAIL', 'NOT_EXECUTED'})
        failed_product = next(r for r in rows if r['condition_id'] == 'C1')
        self.assertEqual(failed_product['raw_checkpoint_status'], 'FAIL')
        self.assertEqual(failed_product['fixture_status'], 'FAIL')
        self.assertTrue(fixture['checks'][0]['report_refs'])

    def test_valid_fixture_does_not_excuse_a_real_product_failure(self):
        self.sandbox.xml = self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="No block placed"/></testcase>')
        result, _ = self.run_verification()
        self.assertEqual(result['scenario_results'][0]['fixture_result']['status'], 'PASS')
        self.assertEqual(self.statuses(result), ['PASS', 'FAIL', 'PASS'])
        self.assertEqual(result['scenario_results'][0]['status'], 'FAIL')

    def test_later_fixture_failure_preserves_earlier_independent_product_pass(self):
        self.scenario['execution']['precondition_tests'][0]['checkpoint_ids'] = ['CP3']
        self.fail_fixture()
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED', 'PASS', 'NOT_EXECUTED'])
        self.assertEqual(result['scenario_results'][0]['fixture_result']['affected_checkpoint_ids'], ['CP1', 'CP3'])
        rows = condition_progress.observations(self.manifest, result)
        first = next(row for row in rows if row['condition_id'] == 'C1')
        later = next(row for row in rows if row['condition_id'] == 'C2')
        self.assertEqual(first['status'], 'PASS')
        self.assertEqual(first['fixture_status'], 'PASS')
        self.assertEqual(later['status'], 'NOT_EXECUTED')
        self.assertEqual(later['raw_checkpoint_status'], 'PASS')

    def test_later_fixture_failure_does_not_hide_an_independent_product_failure(self):
        self.scenario['execution']['precondition_tests'][0]['checkpoint_ids'] = ['CP3']
        self.fail_fixture()
        self.sandbox.xml = self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="Actual planting failed"/></testcase>')
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED', 'FAIL', 'NOT_EXECUTED'])
        self.assertEqual(result['scenario_results'][0]['status'], 'FAIL')
        rows = condition_progress.observations(self.manifest, result)
        self.assertEqual(next(row for row in rows if row['condition_id'] == 'C1')['status'], 'FAIL')

    def test_scoped_fixture_never_proves_unaffected_checks_from_corrupt_evidence(self):
        self.scenario['execution']['precondition_tests'][0]['checkpoint_ids'] = ['CP3']
        self.sandbox.bad_hash = True
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED'] * 3)
        self.assertEqual(result['scenario_results'][0]['fixture_result']['affected_checkpoint_ids'], ['CP1', 'CP2', 'CP3'])
        self.sandbox.bad_hash = False
        self.sandbox.xml = self.sandbox.xml.replace(b'</testsuite>',
            b'<testcase classname="dev.example.PlantTests" name="siteFound"/></testsuite>')
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED'] * 3)

    def test_absent_later_prerequisite_cannot_erase_an_observed_product_failure(self):
        self.scenario['execution']['precondition_tests'] = [
            {'classname':'Fixture', 'name':'laterSetup', 'checkpoint_ids':['CP2', 'CP3']}]
        self.scenario['execution']['expected_tests'].append({'classname':'Fixture', 'name':'laterSetup'})
        self.sandbox.xml = self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="Product failed before later setup"/></testcase>')
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['PASS', 'FAIL', 'NOT_EXECUTED'])
        scenario = result['scenario_results'][0]
        self.assertEqual(scenario['status'], 'FAIL')
        self.assertEqual(scenario['fixture_result']['reason_code'], 'fixture_unconfirmed')
        self.assertIn('Product failed before later setup', scenario['checkpoints'][1]['actual'])

    def test_invalid_fixture_does_not_close_an_earlier_missing_observation_gap(self):
        self.fail_fixture()
        result, _ = self.run_verification()
        archive = self.root / 'history'
        archive.mkdir()
        for number in [1, 2]:
            scope = {**result['scope'], 'round':number}
            plan = {'contract':'PlannerDecision', 'scope':scope, 'rationale':'Measure actual product behavior',
                    'hypotheses':[], 'regression_condition_ids':[], 'actions':[{'kind':'diagnose',
                        'condition_ids':['C1'], 'goal':'Observe planting', 'expected_observation':'Placed block'}]}
            plan_record = pipeline_memory.append_event(archive, None, 'plan', plan, scope=scope, producer='planner')
            current = deepcopy(result)
            current.update(scope=scope, runner_run_id='fixture-' + str(number))
            if number == 1:
                current['scenario_results'] = []
            record = pipeline_memory.append_event(archive, None, 'verification', current,
                scope=scope, producer='runner', links=[{'relation':'tests_hypothesis',
                    'record_id':plan_record['record_id']}])
            manifest = deepcopy(self.manifest)
            manifest['scope'] = scope
            pipeline_memory.append_event(archive, None, 'reflection', {
                'artifact_type':'ConditionObservationMapping', 'verification_record_id':record['record_id'],
                'manifest':manifest}, scope=scope, producer='controller')
        profiles = condition_progress.reconstruct(archive, self.catalog, result['scope'], lambda _:None)
        profile = next(p for p in profiles['conditions'] if p['condition_id'] == 'C1')
        gap = next(c for c in profile['checkpoints'] if c['scenario_id'] is None)
        self.assertEqual(gap['unresolved_observations'], 0)
        self.assertGreater(gap['unresolved_gaps'], 0)
        self.assertNotEqual(gap.get('last_status'), 'OBSERVATION_AVAILABLE')

    def test_missing_fixture_cannot_be_inferred_from_passing_product_cases(self):
        self.sandbox.xml = self.sandbox.xml.replace(
            b'<testcase classname="dev.example.PlantTests" name="siteFound"/>', b'')
        result, _ = self.run_verification()
        fixture = result['scenario_results'][0]['fixture_result']
        self.assertEqual(fixture['status'], 'NOT_EXECUTED')
        self.assertEqual(fixture['reason_code'], 'fixture_unconfirmed')
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED'] * 3)
        self.assertEqual(fixture['raw_checkpoints'][1]['status'], 'PASS')

    def test_skipped_or_duplicate_fixture_is_unconfirmed_without_acceptance(self):
        for fixture_case in (
            b'<testcase classname="dev.example.PlantTests" name="siteFound"><skipped/></testcase>',
            b'<testcase classname="dev.example.PlantTests" name="siteFound"/>'
            b'<testcase classname="dev.example.PlantTests" name="siteFound"/>'):
            with self.subTest(case=fixture_case):
                self.sandbox.xml = (b'<testsuite>' + fixture_case
                    + b'<testcase classname="dev.example.PlantTests" name="saplingPlaced"/>'
                    + b'<testcase classname="dev.example.PlantTests" name="treeGrown"/></testsuite>')
                result, _ = self.run_verification()
                self.assertNotEqual(result['scenario_results'][0]['fixture_result']['status'], 'PASS')
                self.assertEqual(result['scenario_results'][0]['status'], 'NOT_EXECUTED')
                self.assertEqual(self.statuses(result), ['NOT_EXECUTED'] * 3)

    def test_hash_mismatch_cannot_establish_a_valid_fixture_or_product(self):
        self.sandbox.bad_hash = True
        result, _ = self.run_verification()
        fixture = result['scenario_results'][0]['fixture_result']
        self.assertEqual(fixture['status'], 'ERROR')
        self.assertIn('hash mismatch', fixture['checks'][0]['actual'])
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED'] * 3)
        self.assertEqual([r['status'] for r in fixture['raw_checkpoints']], ['ERROR'] * 3)

    def test_unknown_duplicate_or_product_bound_prerequisite_is_rejected_before_execution(self):
        for tests in (
            [{'classname':'Fixture', 'name':'notExpected'}],
            [{'classname':'dev.example.PlantTests', 'name':'siteFound'}] * 2,
            [{'classname':'dev.example.PlantTests', 'name':'saplingPlaced'}],
            [{'classname':'dev.example.PlantTests', 'name':'siteFound', 'checkpoint_ids':['UNKNOWN']}],
            [{'classname':'dev.example.PlantTests', 'name':'siteFound', 'checkpoint_ids':['CP3','CP3']}]):
            with self.subTest(tests=tests):
                self.scenario['execution']['precondition_tests'] = tests
                with self.assertRaises(ValueError):
                    self.run_verification()
                self.assertEqual(self.sandbox.calls, [])

    def test_invalid_fixture_does_not_skip_an_independent_scenario(self):
        self.fail_fixture()
        first = self.scenario
        second = deepcopy(first)
        second.update(id='independent', condition_ids=['C2'], task_ids=['T2'])
        second['checkpoints'] = second['checkpoints'][2:]
        second['execution']['checkpoint_tests'] = second['execution']['checkpoint_tests'][2:]
        second['execution']['expected_tests'] = second['execution']['expected_tests'][2:]
        second['execution']['precondition_tests'] = []
        first.update(condition_ids=['C1'], task_ids=['T1'])
        first['checkpoints'] = first['checkpoints'][:2]
        first['execution']['checkpoint_tests'] = first['execution']['checkpoint_tests'][:2]
        first['execution']['expected_tests'] = first['execution']['expected_tests'][:2]
        self.manifest['scenarios'].append(second)
        self.decision['regression_condition_ids'] = ['C2']
        original = self.sandbox.verify

        def execute(ws, stage, *args, **kwargs):
            report = original(ws, stage, *args, **kwargs)
            path = stage / 'acceptance_results.json'
            legacy = json.loads(path.read_text(encoding='utf-8'))
            row = next(s for s in legacy['scenarios'] if s['id'] == 'independent')
            xml = b'<testsuite><testcase classname="dev.example.PlantTests" name="treeGrown"/></testsuite>'
            artifact = row['assertion_evidence']['reports'][0]
            (stage / artifact['file']).write_bytes(xml)
            artifact['sha256'] = hashlib.sha256(xml).hexdigest()
            path.write_text(json.dumps(legacy), encoding='utf-8')
            return report

        self.sandbox.verify = execute
        result, _ = self.run_verification()
        rows = {s['scenario_id']:s for s in result['scenario_results']}
        self.assertEqual(rows['s1']['status'], 'NOT_EXECUTED')
        self.assertEqual(rows['independent']['status'], 'PASS')
        self.assertEqual(result['infrastructure_errors'], [])

    def test_historical_manifest_without_declaration_keeps_its_existing_meaning(self):
        self.scenario['execution'].pop('precondition_tests')
        self.fail_fixture()
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['FAIL', 'PASS', 'PASS'])
        self.assertEqual(result['scenario_results'][0]['status'], 'FAIL')
        self.assertNotIn('fixture_result', result['scenario_results'][0])

    def test_optional_contract_and_generated_wire_schema_remain_consistent(self):
        validate('ScenarioManifest', self.manifest)
        expected = agent_protocol.compact_schema('AgentScenarios', load_schema('ScenarioManifest'))
        self.assertEqual(expected, load_schema('AgentScenarios'))
        for name in ['ScenarioManifest', 'AgentScenarios', 'VerificationResult']:
            load_submission_schema(name)
        wire = agent_protocol.to_wire(self.manifest)
        validate('AgentScenarios', wire)
        self.assertEqual(wire['scenarios'][0]['execution']['precondition_tests'],
                         self.scenario['execution']['precondition_tests'])

    def test_saved_compact_proposals_without_optional_fixture_arrays_still_hydrate(self):
        wire=agent_protocol.to_wire(self.manifest)
        for scenario in wire['scenarios']:scenario['execution'].pop('precondition_tests',None)
        original=deepcopy(wire)
        hydrated=agent_protocol.hydrate(wire,scope=self.manifest['scope'],inputs={},
            artifact=lambda path:{'path':path,'sha256':'a'*64,'media_type':'text/x-java'},
            read=lambda ref:b'',write=lambda *args:None)
        validate('ScenarioManifest',hydrated)
        self.assertEqual(wire,original)
        self.assertEqual(hydrated['scenarios'][0]['execution']['precondition_tests'],[])


if __name__ == '__main__': unittest.main()
