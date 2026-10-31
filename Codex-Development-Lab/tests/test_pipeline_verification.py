"""Independent verification policy fixtures; no LLM, Docker or game calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from common import acceptance
from common.pipeline_contracts import load_example, validate
from worker.pipeline_verification import verify
from worker.sandbox import RunnerCleanupError


class FixtureSandbox:
    def __init__(self):
        self.calls = []
        self.xml = (b'<testsuite><testcase classname="dev.example.PlantTests" name="siteFound"/>'
                    b'<testcase classname="dev.example.PlantTests" name="saplingPlaced"/>'
                    b'<testcase classname="dev.example.PlantTests" name="treeGrown"/></testsuite>')
        self.bad_hash = False
        self.no_result = False
        self.source_change = False
        self.video = False
        self.screenshot = False
        self.error = None
        self.report_link = None

    def verify(self, ws, stage, timeout, jid, poll, *, eula):
        if self.error: raise self.error
        request = acceptance.read_json(ws, 'acceptance-request.json')
        self.calls.append({'request':request,'timeout':timeout,'jid':jid,'eula':eula})
        rows = []
        for scenario in request['scenarios']:
            relative = 'acceptance/' + scenario['id'] + '/assertions/report-000.xml'
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.report_link is None: target.write_bytes(self.xml)
            else: target.symlink_to(self.report_link)
            row = {'id':scenario['id'],'requirement_ids':scenario['requirement_ids'],
                'status':'TEST_PASSED','test_status':'PASS','video':None,
                'assertion_evidence':{'expected_tests':scenario['expected_tests'],
                    'test_cases':[{'classname':'dev.example.PlantTests','name':'saplingPlaced','status':'PASS'}],
                    'reports':[{'file':relative,'sha256':'f'*64 if self.bad_hash else hashlib.sha256(self.xml).hexdigest()}]}}
            if self.video:
                movie = 'acceptance/' + scenario['id'] + '/capture.mp4'
                (stage / movie).write_bytes(b'\x00\x00\x00\x18ftypisomfixture')
                row.update(video=movie, video_capture={'status':'PASS','scenario_window_complete':True})
            if self.screenshot:
                still = stage / 'acceptance' / scenario['id'] / 'screenshots' / 'capture-frame.png'
                still.parent.mkdir(parents=True, exist_ok=True)
                still.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
                row.setdefault('video_capture', {'status':'PASS'})['still_frame'] = 'screenshots/capture-frame.png'
            rows.append(row)
        if not self.no_result:
            (stage/'acceptance_results.json').write_text(json.dumps({'schema_version':2,
                'spec_sha256':request['spec_sha256'],'scenarios':rows}), encoding='utf-8')
        if self.source_change: (ws/'Changed.java').write_text('changed during execution')
        return {'status':'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',
                'checks':[{'label':'gradle_build','status':'PASS'}], 'video':None}


class PipelineVerificationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.ws = self.root/'project'/'workspace'; self.ws.mkdir(parents=True)
        self.stage = self.root/'artifacts'/'job'/'round-03'
        self.catalog = load_example('RequirementsCatalog')
        self.manifest = load_example('ScenarioManifest')
        self.decision = load_example('PlannerDecision')
        self.sandbox = FixtureSandbox()
        test = self.manifest['scenarios'][0]['test_file']
        path = self.ws / test['path']; path.parent.mkdir(parents=True)
        raw = b'class PlantTest { /* fixture only */ }'
        path.write_bytes(raw); test['sha256'] = hashlib.sha256(raw).hexdigest()
        self.manifest['scenarios'][0]['execution']['kind'] = 'unit'
        self.manifest['scenarios'][0]['execution']['gradle_task'] = 'test'
        self.manifest['scenarios'][0]['media'] = []
        self.refresh_source()

    def refresh_source(self):
        self.manifest['scope']['source_revision'] = acceptance.source_fingerprint(self.ws)

    def run_verification(self, **kwargs):
        return verify(self.ws, self.stage, 'job-3', self.catalog, self.manifest, self.decision,
            sandbox=self.sandbox,poll=lambda:None,timeout=900,eula=False,**kwargs)

    def statuses(self, result):
        return [c['status'] for c in result['scenario_results'][0]['checkpoints']]

    def test_exact_fresh_junit_cases_establish_pass_and_snapshot_provenance(self):
        result, legacy = self.run_verification()
        validate('VerificationResult', result)
        self.assertEqual(self.statuses(result), ['PASS','PASS','PASS'])
        self.assertEqual(result['scenario_results'][0]['status'], 'PASS')
        self.assertEqual(result['scope']['source_revision'], self.manifest['scope']['source_revision'])
        self.assertEqual(result['scope']['round'], 3)
        self.assertEqual(legacy['checks'][0]['status'], 'PASS')
        self.assertTrue((self.stage/'test-sources'/self.manifest['scenarios'][0]['test_file']['path']).is_file())
        for observation in result['scenario_results'][0]['checkpoints']:
            reference = observation['evidence_refs'][0]
            raw = (self.stage.parent/reference['artifact']['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),reference['artifact']['sha256'])
            self.assertEqual(raw.decode().splitlines()[reference['start_line']-1], reference['excerpt'])

    def test_early_failure_preserves_success_and_marks_later_stage_not_executed(self):
        self.sandbox.xml = (b'<testsuite><testcase classname="dev.example.PlantTests" name="siteFound"/>'
            b'<testcase classname="dev.example.PlantTests" name="saplingPlaced"><failure message="No block placed"/>'
            b'</testcase></testsuite>')
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['PASS','FAIL','NOT_EXECUTED'])
        self.assertEqual(result['scenario_results'][0]['status'], 'FAIL')
        self.assertIn('No block placed', result['scenario_results'][0]['checkpoints'][1]['actual'])

    def test_persisted_test_cases_claim_does_not_override_xml_failure(self):
        self.sandbox.xml = b'<testsuite><testcase classname="dev.example.PlantTests" name="saplingPlaced"><error message="Real failure"/></testcase></testsuite>'
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['NOT_EXECUTED','FAIL','NOT_EXECUTED'])

    def test_skipped_checkpoint_is_never_pass(self):
        self.sandbox.xml = self.sandbox.xml.replace(b'name="treeGrown"/>', b'name="treeGrown"><skipped message="Capability absent"/></testcase>')
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['PASS','PASS','SKIPPED'])
        self.assertEqual(result['scenario_results'][0]['status'], 'SKIPPED')

    def test_duplicate_named_case_is_ambiguous_not_pass(self):
        self.sandbox.xml = self.sandbox.xml.replace(b'</testsuite>', b'<testcase classname="dev.example.PlantTests" name="saplingPlaced"/></testsuite>')
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['PASS','ERROR','PASS'])
        self.assertEqual(result['scenario_results'][0]['status'], 'ERROR')

    def test_missing_binding_is_not_executed_even_with_a_test_of_same_name(self):
        self.manifest['scenarios'][0]['execution']['checkpoint_tests'].pop()
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result)[-1], 'NOT_EXECUTED')
        self.assertEqual(result['scenario_results'][0]['status'], 'NOT_EXECUTED')

    def test_report_hash_mismatch_invalidates_all_named_case_claims(self):
        self.sandbox.bad_hash = True
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['ERROR']*3)
        self.assertIn('hash mismatch', result['scenario_results'][0]['checkpoints'][0]['actual'])

    def test_source_changes_during_verification_do_not_reuse_pass(self):
        self.sandbox.source_change = True
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['ERROR']*3)
        self.assertIn('Source snapshot changed', '\n'.join(result['infrastructure_errors']))

    def test_wrong_test_file_hash_is_rejected_before_launch(self):
        self.manifest['scenarios'][0]['test_file']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'test file hash'): self.run_verification()
        self.assertEqual(self.sandbox.calls, [])

    def test_wrong_manifest_source_is_rejected_before_launch(self):
        self.manifest['scope']['source_revision'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'current source snapshot'): self.run_verification()
        self.assertEqual(self.sandbox.calls, [])

    def test_prior_result_cannot_establish_pass_when_runner_emits_nothing(self):
        self.stage.mkdir(parents=True)
        (self.stage/'acceptance_results.json').write_text('{"scenarios":[{"status":"TEST_PASSED"}]}')
        self.sandbox.no_result = True
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['ERROR']*3)

    def test_request_has_no_four_scenario_cap_and_full_mode_has_exact_inventory(self):
        base = self.manifest['scenarios'][0]
        self.manifest['scenarios'] = [{**deepcopy(base),'id':'scenario-'+str(i)} for i in range(7)]
        result, _ = self.run_verification(full=True)
        self.assertEqual(len(result['scenario_results']), 7)
        self.assertEqual(len(self.sandbox.calls[0]['request']['scenarios']), 7)
        self.assertEqual(self.sandbox.calls[0]['request']['schema_version'], 2)
        request = json.loads((self.stage/'pipeline-verification-request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['run_kind'],'full')
        self.assertEqual(len(request['scenario_ids']),7)
        self.assertEqual(request['regression_scenario_ids'],[])

    def test_gameplay_without_optional_video_keeps_independent_scenario_pass(self):
        self.manifest['scenarios'][0]['execution']['kind'] = 'gameplay'
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['PASS']*3)
        self.assertEqual(result['scenario_results'][0]['status'], 'PASS')
        self.assertFalse(result['infrastructure_errors'])

    def test_missing_requested_capture_does_not_override_real_test_failure(self):
        self.manifest['scenarios'][0]['execution']['kind']='gameplay'
        self.manifest['scenarios'][0]['media']=['video','screenshot']
        self.sandbox.xml=self.sandbox.xml.replace(b'name="saplingPlaced"/>',b'name="saplingPlaced"><failure message="actual game assertion failed"/></testcase>')
        result,legacy=self.run_verification()
        self.assertEqual(result['scenario_results'][0]['status'],'FAIL')
        self.assertEqual(self.statuses(result),['PASS','FAIL','PASS'])
        self.assertEqual(result['scenario_results'][0]['media_refs'],[])
        self.assertTrue(legacy['pipeline_diagnostics'])

    def test_requested_media_are_attached_only_with_actual_safe_files(self):
        self.manifest['scenarios'][0]['execution']['kind'] = 'gameplay'
        self.manifest['scenarios'][0]['media'] = ['video','screenshot']
        self.sandbox.video = self.sandbox.screenshot = True
        result, _ = self.run_verification()
        self.assertEqual(result['scenario_results'][0]['status'], 'PASS')
        self.assertEqual([m['media_type'] for m in result['scenario_results'][0]['media_refs']], ['video/mp4','image/png'])

    def test_xml_entity_declaration_is_rejected_without_parsing_external_data(self):
        self.sandbox.xml = b'<!DOCTYPE testsuite [<!ENTITY x SYSTEM "file:///etc/passwd">]><testsuite/>'
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['ERROR']*3)
        self.assertIn('Unsafe JUnit', result['scenario_results'][0]['checkpoints'][0]['actual'])

    def test_foreign_report_symlink_never_establishes_pass(self):
        outside = self.root/'outside.xml'; outside.write_bytes(self.sandbox.xml)
        probe = self.root/'symlink-probe'
        try: probe.symlink_to(outside)
        except OSError: self.skipTest('Symlinks are unavailable')
        probe.unlink()
        self.sandbox.report_link = outside
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result), ['ERROR']*3)

    def test_unsafe_source_reference_and_unknown_binding_do_not_start_runner(self):
        self.manifest['scenarios'][0]['test_file']['path'] = '../outside.java'
        with self.assertRaises(ValueError): self.run_verification()
        self.assertEqual(self.sandbox.calls, [])
        self.manifest['scenarios'][0]['test_file']['path'] = 'src/gametest/PlantTest.java'
        self.manifest['scenarios'][0]['execution']['checkpoint_tests'][0]['checkpoint_id'] = 'CP-unknown'
        with self.assertRaisesRegex(ValueError,'checkpoint binding'): self.run_verification()

    def test_unconfirmed_runner_cleanup_is_propagated_to_keep_project_locked(self):
        self.sandbox.error = RunnerCleanupError('Stop is unconfirmed')
        with self.assertRaises(RunnerCleanupError): self.run_verification()

    def test_unbound_expected_case_must_still_execute_for_whole_scenario_pass(self):
        self.manifest['scenarios'][0]['execution']['expected_tests'].append({'classname':'Fixture','name':'cleanup'})
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result),['PASS']*3)
        self.assertEqual(result['scenario_results'][0]['status'],'NOT_EXECUTED')

    def test_suite_failure_without_a_failed_named_checkpoint_prevents_scenario_pass(self):
        self.sandbox.xml = self.sandbox.xml.replace(b'<testsuite>',b'<testsuite errors="1">')
        result, _ = self.run_verification()
        self.assertEqual(self.statuses(result),['PASS']*3)
        self.assertEqual(result['scenario_results'][0]['status'],'FAIL')

    def test_foreign_project_input_does_not_start_the_runner(self):
        self.manifest['scope']['project_id'] = 'other-project'
        with self.assertRaisesRegex(ValueError,'different projects'): self.run_verification()
        self.assertEqual(self.sandbox.calls,[])

    def test_full_verification_substage_keeps_round_and_job_relative_refs(self):
        self.stage /= 'full-verification'
        result, _ = self.run_verification(full=True)
        self.assertEqual(result['scope']['round'],3)
        self.assertTrue(result['request_ref']['path'].startswith('round-03/full-verification/'))
        self.assertTrue(result['scenario_results'][0]['checkpoints'][0]['evidence_refs'][0]['artifact']['path'].startswith('round-03/full-verification/'))

    def test_recording_window_ids_share_one_lowercase_portable_contract(self):
        for identifier in ('S1','mixed_Case','scenario.name','scenario:one','../escape'):
            with self.subTest(identifier=identifier):
                self.manifest['scenarios'][0]['id'] = identifier
                with self.assertRaises(ValueError): self.run_verification()
        self.assertEqual(self.sandbox.calls,[])

    def test_planner_selects_related_regressions_without_running_every_independent_scenario(self):
        first=self.manifest['scenarios'][0]
        second=deepcopy(first);second['id']='tree-only';second['condition_ids']=['C2'];second['task_ids']=['T2']
        second['checkpoints']=second['checkpoints'][2:]
        second['execution']['checkpoint_tests']=second['execution']['checkpoint_tests'][2:]
        second['execution']['expected_tests']=second['execution']['expected_tests'][2:]
        first['condition_ids']=['C1'];first['task_ids']=['T1'];first['checkpoints']=first['checkpoints'][:2]
        first['execution']['checkpoint_tests']=first['execution']['checkpoint_tests'][:2]
        first['execution']['expected_tests']=first['execution']['expected_tests'][:2]
        self.manifest['scenarios'].append(second)
        result,_=self.run_verification()
        self.assertEqual([x['scenario_id'] for x in result['scenario_results']],['s1'])
        self.decision['regression_condition_ids']=['C2']
        result,_=self.run_verification()
        self.assertEqual({x['scenario_id'] for x in result['scenario_results']},{'s1','tree-only'})


if __name__ == '__main__': unittest.main()
