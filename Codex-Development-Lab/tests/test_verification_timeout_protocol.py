"""Configured phase budgets survive every trusted verification protocol layer."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runner.policy import container_config
from worker import sandbox_entry, verification_plan
from worker.sandbox import DockerSandbox


class VerificationTimeoutProtocolTests(unittest.TestCase):
    def test_worker_and_runner_preserve_large_configured_phase_budgets(self):
        for timeout in (60, 1800, 1801, 3601, 7200, 86400):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace, output = root / 'workspace', root / 'output'
                workspace.mkdir()
                (workspace / 'acceptance-request.json').write_text(json.dumps({
                    'schema_version': 2, 'scenarios': [{}, {}]}), encoding='utf-8')
                sandbox = DockerSandbox()

                def execute(argv, cwd, logfile, session_timeout, jid, poll, **kwargs):
                    self.assertEqual(session_timeout, timeout * 5 + 180)
                    payload = {'project': 'a' * 14, 'job': jid, 'verification': True,
                               **kwargs['verification']}
                    config = container_config('trusted', 'private-state', 'c' * 32,
                                              payload, 'd' * 32)
                    self.assertEqual(json.loads(config['Cmd'][0]), {
                        'verification': True, 'timeout_seconds': timeout})
                    (output / 'verification.json').write_text(json.dumps({
                        'status': 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',
                        'checks': [{'label': 'build', 'status': 'PASS'}]}), encoding='utf-8')
                    return {'status': 'PASS', 'exit_code': 0}

                with patch.object(sandbox, '_execute', side_effect=execute) as run:
                    report = sandbox.verify(workspace, output, timeout, 'b' * 14, lambda: None)
                run.assert_called_once()
                self.assertEqual(report['status'], 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED')

    def test_supervisor_accepts_large_budget_before_checking_root_identity(self):
        for timeout in (1801, 3601, 7200, 86400):
            with self.subTest(timeout=timeout), \
                 patch.dict(os.environ, {'HOME': '/tmp/lab'}), \
                 patch.object(sandbox_entry.sys, 'argv', ['supervisor', json.dumps({
                     'verification': True, 'timeout_seconds': timeout})]), \
                 patch.object(sandbox_entry.os, 'geteuid', return_value=1000, create=True):
                with self.assertRaisesRegex(RuntimeError, 'trusted root supervisor'):
                    sandbox_entry.main()

    def test_verification_plan_passes_large_budget_to_actual_stage(self):
        for timeout in (3601, 7200, 86400):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / 'workspace'
                workspace.mkdir()
                with patch.object(verification_plan, 'select_plan', return_value={
                         'label': 'synthetic_test', 'argv': ['synthetic'], 'minecraft': False}), \
                     patch.object(verification_plan, 'collect_artifacts', return_value=[]), \
                     patch.object(verification_plan, 'run_command', return_value={
                         'status': 'PASS', 'exit_code': 0}) as run:
                    report = verification_plan.run_verification(
                        timeout, workspace=workspace, output=root / 'output')
                self.assertEqual(run.call_args.args[3], timeout)
                self.assertEqual(report['status'], 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED')

    def test_worker_rejects_invalid_budgets_without_starting_a_run(self):
        sandbox = DockerSandbox()
        for timeout in (None, True, False, '7200', 7200.0, -1, 0, 59):
            with self.subTest(timeout=timeout), patch.object(sandbox, '_execute') as run:
                with self.assertRaises(ValueError):
                    sandbox.verify('unused', 'unused', timeout, 'b' * 14, lambda: None)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
