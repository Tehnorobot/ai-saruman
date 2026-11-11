"""Dependency setup observations remain diagnostics, never product proof."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from common import evidence_access, role_context
from tests import test_pipeline_verification as fixtures
from worker.world_fixture import WorldFixtureSession


@unittest.skipUnless(sys.platform.startswith('linux') and hasattr(os, 'O_NOFOLLOW'),
                     'Production receipt filesystem checks require Linux')
class ClientDependencyReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='client-dependency-receipt-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / 'game'
        self.game.mkdir()
        self.output = self.root / 'output'
        self.output.mkdir()
        self.source = self.game / 'client-dependency-preparation.json'
        self.session = WorldFixtureSession.__new__(WorldFixtureSession)
        self.session.active = [(self.root, self.game)]

    def test_runtime_ready_receipt_is_retained_exactly_without_product_pass(self):
        raw = json.dumps({'schema_version': 1, 'status': 'READY',
                          'adapter': 'altoclef-title-1.21.11-0.95.61',
                          'reason': None,
                          'observations': {'task_chain_present': True, 'slot_handler_present': True}},
                         indent=2).encode('utf-8') + b'\n'
        self.source.write_bytes(raw)
        result = self.session.collect_client_setup(self.output)
        self.assertEqual(result['status'], 'READY')
        self.assertNotEqual(result['status'], 'PASS')
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual((self.output / result['file']).read_bytes(), raw)
        self.assertIn('not product', result['provenance'])
        self.assertNotIn('assertion_evidence', result)

    def test_missing_receipt_does_not_invent_a_ready_or_failed_dependency(self):
        self.assertIsNone(self.session.collect_client_setup(self.output))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_no_active_clone_has_no_dependency_observations(self):
        self.session.active = []
        self.assertIsNone(self.session.collect_client_setup(self.output))

    def test_invalid_receipt_structure_or_status_is_rejected(self):
        for raw in (b'[]', b'null', b'{"reason":"no status"}', b'{"status":"PASS"}',
                    b'{"status":"READY",', b'\xff\xfe'):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                with self.assertRaises((ValueError, UnicodeError)):
                    self.session.collect_client_setup(self.output)
                self.assertEqual(list(self.output.iterdir()), [])

    def test_oversized_receipt_is_rejected_before_export(self):
        self.source.write_bytes(b'{"status":"READY","padding":"' + b'x' * 65536 + b'"}')
        with self.assertRaisesRegex(ValueError, 'Unsafe|Oversized'):
            self.session.collect_client_setup(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_deeply_nested_project_json_cannot_escape_as_a_pipeline_exception(self):
        self.source.write_bytes(b'{"status":"READY","nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}')
        try:
            value=self.session.collect_client_setup(self.output)
        except ValueError:
            self.assertEqual(list(self.output.iterdir()), [])
        else:
            # Decoder depth differs across Python versions. If it safely
            # decodes the bounded document, retaining it is also valid.
            self.assertEqual(value['status'],'READY')
            self.assertEqual((self.output/value['file']).read_bytes(),self.source.read_bytes())

    def test_decoder_recursion_failure_becomes_a_recoverable_diagnostic_error(self):
        self.source.write_bytes(b'{"status":"READY"}')
        with patch('worker.world_fixture.json.loads',side_effect=RecursionError('decoder depth')):
            with self.assertRaisesRegex(ValueError,'Invalid client dependency preparation JSON'):
                self.session.collect_client_setup(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_symlink_receipt_is_not_followed(self):
        outside = self.root / 'outside.json'
        outside.write_text('{"status":"READY"}', encoding='utf-8')
        self.source.symlink_to(outside)
        with self.assertRaises(OSError):
            self.session.collect_client_setup(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_hardlinked_receipt_is_rejected(self):
        outside = self.root / 'outside.json'
        outside.write_text('{"status":"READY"}', encoding='utf-8')
        os.link(outside, self.source)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.session.collect_client_setup(self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_fifo_receipt_is_rejected_without_blocking(self):
        os.mkfifo(self.source)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.session.collect_client_setup(self.output)
        self.assertEqual(list(self.output.iterdir()), [])


class ClientDependencyFeedbackTests(unittest.TestCase):
    setUp = fixtures.PipelineVerificationTests.setUp
    refresh_source = fixtures.PipelineVerificationTests.refresh_source
    run_verification = fixtures.PipelineVerificationTests.run_verification
    statuses = fixtures.PipelineVerificationTests.statuses

    def dependency_setup(self, status='UNAVAILABLE', *, include_files=True):
        self.manifest['scenarios'][0]['execution']['kind'] = 'gameplay'
        self.manifest['scenarios'][0]['execution']['gradle_task'] = 'runClient'
        original = self.sandbox.verify
        reason = 'UnionClef title initialization did not supply a SlotHandler; exact runtime state retained'
        self.receipt = {'schema_version': 1, 'status': status,
                        'adapter': 'altoclef-title-1.21.11-0.95.61', 'reason': reason,
                        'observations': {'task_chain_present': False, 'slot_handler_present': False}}
        self.raw_receipt = json.dumps(self.receipt, indent=2).encode() + b'\n'
        self.raw_log = ('EXACT DEPENDENCY CAUSE\n' + 'full runtime state\n' * 10000).encode()

        def observed(ws, stage, *args, **kwargs):
            report = original(ws, stage, *args, **kwargs)
            target = stage / 'acceptance_results.json'
            payload = json.loads(target.read_text(encoding='utf-8'))
            row = payload['scenarios'][0]
            sid = row['id']
            row['execution_id'] = 'RUN-actually-started-client'
            row['dependency_preparation'] = {
                'status': status, 'file': 'client-dependency-preparation.json',
                'sha256': hashlib.sha256(self.raw_receipt).hexdigest(), 'detail': reason,
                'provenance': 'Client setup diagnostics only; not product acceptance proof.'}
            directory = stage / 'acceptance' / sid
            directory.mkdir(parents=True, exist_ok=True)
            if include_files:
                (directory / 'client-dependency-preparation.json').write_bytes(self.raw_receipt)
                (directory / 'test.log').write_bytes(self.raw_log)
            target.write_text(json.dumps(payload), encoding='utf-8')
            report['acceptance_results'] = payload
            return report

        self.sandbox.verify = observed
        return reason

    def assert_diagnostic_has_originals(self, result, reason):
        diagnostic = next(d for d in result['execution_diagnostics']
                          if d['stage'] == 'dependency_preparation')
        self.assertEqual(diagnostic['status'], 'NOT_TESTED')
        self.assertEqual(diagnostic['actual'], reason)
        by_kind = {evidence_access.read_artifact(self.ws, ref)['kind']: ref
                   for ref in diagnostic['evidence_refs']}
        receipt = by_kind['client_dependency_preparation']
        log = by_kind['client_dependency_log']
        self.assertEqual(receipt['sha256'], hashlib.sha256(self.raw_receipt).hexdigest())
        self.assertEqual(log['sha256'], hashlib.sha256(self.raw_log).hexdigest())
        read = evidence_access.read_artifact(self.ws, log, start_line=1, end_line=1)
        self.assertEqual(read['content'], 'EXACT DEPENDENCY CAUSE\n')
        row = result['scenario_results'][0]
        self.assertNotEqual(row.get('attempted'), False)
        self.assertEqual(row['execution_id'], 'RUN-actually-started-client')
        self.assertFalse(result['infrastructure_errors'])
        return diagnostic

    def test_unavailable_dependency_keeps_full_receipt_and_log_links(self):
        reason = self.dependency_setup()
        result, _ = self.run_verification()
        self.assert_diagnostic_has_originals(result, reason)

    def test_waiting_dependency_keeps_runtime_diagnostics(self):
        reason = self.dependency_setup('WAITING')
        result, _ = self.run_verification()
        self.assert_diagnostic_has_originals(result, reason)

    def test_dependency_diagnostics_do_not_erase_real_product_failure(self):
        reason = self.dependency_setup()
        self.sandbox.xml = self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="real placement failure"/></testcase>')
        result, _ = self.run_verification()
        self.assert_diagnostic_has_originals(result, reason)
        self.assertEqual(self.statuses(result), ['PASS', 'FAIL', 'PASS'])
        self.assertEqual(result['scenario_results'][0]['status'], 'FAIL')

    def test_ready_receipt_adds_no_failure_diagnostic_or_extra_acceptance(self):
        self.dependency_setup('READY')
        result, _ = self.run_verification()
        self.assertFalse(any(d['stage'] == 'dependency_preparation'
                             for d in result['execution_diagnostics']))
        self.assertEqual(self.statuses(result), ['PASS'] * 3)
        self.assertEqual(len(result['scenario_results'][0]['checkpoints']), 3)

    def test_missing_diagnostic_files_keep_the_exact_runtime_reason(self):
        reason = self.dependency_setup(include_files=False)
        result, _ = self.run_verification()
        diagnostic = next(d for d in result['execution_diagnostics']
                          if d['stage'] == 'dependency_preparation')
        self.assertEqual(diagnostic['actual'], reason)
        self.assertNotEqual(result['scenario_results'][0].get('attempted'), False)

    def test_planner_context_can_navigate_to_dependency_failure(self):
        reason = self.dependency_setup()
        result, _ = self.run_verification()
        view = role_context.build('planner', 'plan', result['scope'], {},
                                  full_input_ref={'path': 'input.json'}, latest_result=result)
        self.assertTrue(any(fact['stage'] == 'dependency_preparation'
                            and reason in fact['preview'] for fact in view['facts']))


if __name__ == '__main__':
    unittest.main()
