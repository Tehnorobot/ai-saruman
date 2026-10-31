"""Independent regressions for audited runtime failures; no model or game calls."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import db
from agent import daemon as executor
from worker import engine


class RuntimeRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.getenv('LAB_DOCKER_TEST_ROOT'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        self.artifacts = self.root / 'artifacts'
        self.mailbox = self.root / 'mailbox'
        for owner, name, value in ((db, 'DB', self.root / 'state' / 'lab.sqlite'),
                                   (engine, 'WORKSPACE_ROOT', self.projects),
                                   (engine, 'ARTIFACTS', self.artifacts)):
            context = patch.object(owner, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects),
                                         'AGENT_TASKS': str(self.mailbox),
                                         'BACKUP_REQUESTS': str(self.root / 'backups')})
        context.start()
        self.addCleanup(context.stop)
        self.pid, self.jid = db.create_project('Runtime regression', 'spec.md',
                                              b'# A synthetic project specification', 1)
        self.workspace = self.projects / self.pid / 'workspace'
        if os.getenv('LAB_RUN_DOCKER_TESTS') == '1':
            # The production dashboard creates workspaces as uid 1000. The
            # integration controller runs as root only to reach the Engine.
            os.chown(self.workspace, 1000, 1000)
        self.output = self.artifacts / self.pid / self.jid

    def run_worker(self, checks, agent):
        with patch.object(engine, 'copy_skill_environment', lambda p: p / 'workspace'), \
             patch.object(engine, 'checks', checks), \
             patch.object(engine, 'request_agent', agent), \
             patch.object(engine, 'snapshot_request'):
            engine.replay_legacy(self.jid)

    def test_restart_during_final_verification_runs_checks_without_paid_turn(self):
        db.update(self.jid, status='RUNNING', round=1, phase='verifying')
        observed = []

        def verify(workspace, stage, jid):
            observed.append((workspace, stage.name, jid))
            report = {'status': 'INCOMPLETE', 'checks': [{'status': 'NOT_CONFIGURED'}]}
            engine.write_json(stage / 'verification.json', report)
            return report

        def no_agent(*args, **kwargs):
            self.fail('Restart of verification must not run a new model turn')

        self.run_worker(verify, no_agent)
        self.assertEqual(observed, [(self.workspace, 'round-01', self.jid)])
        self.assertTrue((self.output / 'round-01' / 'verification.json').is_file())
        self.assertEqual(json.loads((self.output / 'latest_verification.json').read_text())['status'],
                         'INCOMPLETE')
        self.assertEqual(db.job_row(self.jid)['status'], 'FINISHED')

    def test_kotlin_fabric_build_requires_explicit_game_verification(self):
        (self.workspace / 'build.gradle.kts').write_text('plugins { id("fabric-loom") }')
        (self.workspace / 'gradle.properties').write_text('minecraft_version=1.21.11\nfabric_version=0.1')
        stage = self.root / 'checks'
        stage.mkdir()
        with patch.object(engine, 'EULA', False), \
             patch.object(engine.DockerSandbox, 'verify', return_value={
                 'status':'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',
                 'checks':[{'label':'gradle_build','status':'PASS'},
                           {'label':'minecraft_client_gametest','status':'NOT_TESTED'}]}) as verify:
            report = engine.checks(self.workspace, stage, self.jid)
        self.assertFalse(verify.call_args.kwargs['eula'])
        game = [result for result in report['checks'] if result['label'] == 'minecraft_client_gametest']
        self.assertEqual(len(game), 1)
        self.assertEqual(game[0]['status'], 'NOT_TESTED')
        self.assertFalse((self.workspace / 'eula.txt').exists())

    def test_unavailable_sandbox_never_runs_project_command_on_controller(self):
        sentinel = self.workspace / 'unsafe-local-execution.txt'
        with patch.dict(os.environ, {'LAB_RUNNER_SOCKET': str(self.root / 'no-runner.sock')}):
            result = engine.command('synthetic_test', ['python3', '-c',
                'from pathlib import Path;Path("unsafe-local-execution.txt").write_text("ran")'],
                self.workspace, self.root / 'isolated.log', 10, self.jid)
        self.assertFalse(sentinel.exists(), 'Project command executed outside its sandbox')
        self.assertEqual(result['status'], 'ERROR')

    def test_running_attempt_waits_for_cancel_ack_and_late_result_cannot_poison_resume(self):
        db.claim('independent-verifier')
        seen = []
        cancelled = []

        def transport_step(_delay):
            requests = list((self.mailbox / 'requests').glob('*.json'))
            if requests:
                self.assertEqual(len(requests), 1)
                request = requests[0]
                payload = json.loads(request.read_text())
                tid = payload['id']
                self.assertNotIn(tid, seen)
                seen.append(tid)
                running = self.mailbox / 'running' / request.name
                request.rename(running)
                if len(seen) == 1:
                    db.update(self.jid, pause_requested=1)
                else:
                    engine.write_json(self.mailbox / 'results' / request.name,
                                      {'id': tid, 'status': 'PASS'})
                    running.unlink()
            else:
                self.assertEqual(len(seen), 1, 'Transport unexpectedly polled without a request')
                tid = seen[0]
                self.assertTrue((self.mailbox / 'cancel' / tid).exists())
                cancelled.append(tid)
                engine.write_json(self.mailbox / 'results' / (tid + '.json'),
                                  {'id': tid, 'status': 'CANCELLED'})
                (self.mailbox / 'running' / (tid + '.json')).unlink()

        logfile = self.root / 'stage' / 'codex.jsonl'
        with patch.object(engine.time, 'sleep', transport_step):
            paused = engine.request_agent(self.jid, self.pid, 1, 'first attempt', logfile)
            self.assertEqual(paused['status'], 'USER_PAUSED')
            self.assertEqual(cancelled, seen)
            db.update(self.jid, pause_requested=0)
            # A delayed duplicate result is deliberately left in the real mailbox.
            engine.write_json(self.mailbox / 'results' / (seen[0] + '.json'),
                              {'id': seen[0], 'status': 'CANCELLED'})
            resumed = engine.request_agent(self.jid, self.pid, 1, 'resumed attempt', logfile)
        self.assertEqual(resumed['status'], 'PASS')
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])

    def test_restart_reuses_inflight_attempt_instead_of_submitting_duplicate(self):
        class ControllerCrash(BaseException):
            pass

        logfile = self.root / 'stage' / 'codex.jsonl'
        with patch.object(engine.time, 'sleep', side_effect=ControllerCrash):
            with self.assertRaises(ControllerCrash):
                engine.request_agent(self.jid, self.pid, 1, 'first prompt', logfile)
        request = next((self.mailbox / 'requests').glob('*.json'))
        original_id = json.loads(request.read_text())['id']

        def complete_existing(_delay):
            requests = list((self.mailbox / 'requests').glob('*.json'))
            self.assertEqual(requests, [request])
            self.assertEqual(json.loads(request.read_text())['prompt'], 'first prompt')
            engine.write_json(self.mailbox / 'results' / request.name,
                              {'id': original_id, 'status': 'PASS'})
            request.unlink()

        with patch.object(engine.time, 'sleep', complete_existing):
            result = engine.request_agent(self.jid, self.pid, 1, 'must not resubmit', logfile)
        self.assertEqual(result['status'], 'PASS')

    def test_paused_verification_resumes_same_round_without_new_development(self):
        db.claim('first-controller')
        observed = []

        def verify(workspace, stage, jid):
            observed.append(stage.name)
            if len(observed) == 1:
                db.update(jid, pause_requested=1)
                return {'status': 'INCOMPLETE', 'checks': [{'status': 'USER_PAUSED'}]}
            return {'status': 'INCOMPLETE', 'checks': [{'status': 'NOT_CONFIGURED'}]}

        with patch.object(engine,'plan_round',side_effect=lambda jid,pid,n,ws,previous,stage,context:({'status':'PASS'},context)), \
             patch.object(engine, 'request_agent', return_value={'status': 'PASS', 'tail': ''}) as develop:
            self.run_worker(verify, develop)
            self.assertEqual(db.job_row(self.jid)['status'], 'USER_PAUSED')
            self.assertFalse((self.output / 'round-01' / 'verification_complete.json').exists())
            db.control_job(self.jid, 'resume')
            db.claim('replacement-controller')
            self.run_worker(verify, develop)
            self.assertEqual(develop.call_count, 1)
        self.assertEqual(observed, ['round-01', 'round-01'])
        self.assertTrue((self.output / 'round-01' / 'verification_complete.json').exists())
        self.assertEqual(db.job_row(self.jid)['status'], 'FINISHED')

    def test_unconfirmed_container_removal_quarantines_project_until_recovery(self):
        from common.project_lock import project_lock
        from worker.sandbox import DockerSandbox, RunnerAPIError, RunnerCleanupError
        sandbox = DockerSandbox()
        project = self.workspace.parent
        removed = []
        token = 'c' * 32

        def unavailable_cleanup(method, path, body=None, **kwargs):
            if method == 'GET' and path.startswith('/runs/by-job/'):
                raise RunnerAPIError(404, 'Runner GET: 404: absent')
            if method == 'POST' and path == '/runs':
                return {'Id': token}
            if method in ('PUT', 'POST') and path.startswith('/runs/' + token):
                return None
            if method == 'GET' and path == '/runs/' + token:
                return {'Uploaded': True, 'State': {'Running': False, 'ExitCode': 0}}
            if method == 'GET' and path.endswith('/logs'):
                return b'synthetic completed command'
            if method == 'DELETE':
                raise RuntimeError('Synthetic broker connection lost during cleanup')
            self.fail('Unexpected runner API call: ' + method + ' ' + path)

        with patch.object(sandbox, 'api', side_effect=unavailable_cleanup), \
             patch.object(sandbox, 'config', return_value={}), \
             patch('worker.sandbox.pack_tree'), \
             patch.object(sandbox, '_restore'):
            with self.assertRaises(RunnerCleanupError):
                sandbox.execute(['synthetic'], self.workspace, self.root / 'cleanup.log',
                                10, self.jid, lambda: None)
        marker = project / '.lab-runner.json'
        self.assertTrue(marker.is_file())
        for shared in (False, True):
            with self.subTest(shared=shared), self.assertRaisesRegex(RuntimeError, 'controller cleanup'):
                with project_lock(project, shared=shared):
                    self.fail('Writer/backup entered a quarantined project')

        def confirmed_cleanup(method, path, body=None, **kwargs):
            if method == 'GET':
                return {'Uploaded': True, 'Config': {'Labels': {'codex-lab.role': 'project-test',
                                             'codex-lab.project': self.pid}}}
            if method == 'POST':
                self.assertTrue(path.endswith('/stop'))
                removed.append('stop')
            else:
                self.assertEqual(method, 'DELETE')
                removed.append('delete')

        def restore(_token, workspace):
            self.assertEqual(_token, token)
            self.assertEqual(workspace, self.workspace)
            self.assertTrue(marker.exists(), 'Quarantine cleared before snapshot returned')
            removed.append('restore')

        with patch.object(sandbox, 'api', side_effect=confirmed_cleanup), \
             patch.object(sandbox, '_restore', side_effect=restore):
            sandbox.recover(project)
        self.assertEqual(removed, ['stop', 'restore', 'delete'])
        self.assertFalse(marker.exists())
        with project_lock(project):
            pass

    def test_legacy_engine_marker_cannot_be_used_as_broker_target(self):
        from worker.sandbox import DockerSandbox, RunnerCleanupError
        marker = self.workspace.parent / '.lab-runner.json'
        marker.write_text(json.dumps({'container': 'c' * 64}))
        sandbox = DockerSandbox()
        with patch.object(sandbox, 'api') as transport:
            with self.assertRaisesRegex(RunnerCleanupError, 'Legacy runner marker'):
                sandbox.recover(self.workspace.parent)
        transport.assert_not_called()
        self.assertTrue(marker.exists())

    def test_failed_snapshot_return_keeps_broker_run_and_project_quarantined(self):
        from worker.sandbox import DockerSandbox, RunnerCleanupError
        token = 'd' * 32
        marker = self.workspace.parent / '.lab-runner.json'
        marker.write_text(json.dumps({'container': token}))
        calls = []

        def transport(method, path, body=None, **kwargs):
            calls.append((method, path))
            if method == 'GET':
                return {'Uploaded': True, 'Config': {'Labels': {'codex-lab.project': self.pid}}}
            self.assertEqual((method, path), ('POST', '/runs/' + token + '/stop'))

        sandbox = DockerSandbox()
        with patch.object(sandbox, 'api', side_effect=transport), \
             patch.object(sandbox, '_restore', side_effect=OSError('Synthetic failed snapshot import')):
            with self.assertRaises(RunnerCleanupError):
                sandbox.recover(self.workspace.parent)
        self.assertTrue(marker.exists())
        self.assertEqual(calls, [('GET', '/runs/' + token), ('POST', '/runs/' + token + '/stop')])

    def assert_crashed_controller_waits_for_executor(self, action, expected_status):
        db.claim('crashed-controller')
        db.control_job(self.jid, action)
        db.update(self.jid, lease_until=0)
        self.assertEqual(db.claim('replacement-controller')['id'], self.jid)
        tid = self.jid + '-round01-' + 'b' * 32
        running = self.mailbox / 'running' / (tid + '.json')
        engine.write_json(running, {'id': tid, 'job_id': self.jid, 'project_id': self.pid})
        stage = self.output / 'round-01'
        engine.write_json(stage / 'agent_attempt.json', {'id': tid})
        acknowledged = []

        def executor_acknowledgement(_delay):
            row = db.job_row(self.jid)
            self.assertEqual(row['status'], 'RUNNING', 'Controller acknowledged before the writer exited')
            self.assertEqual(row['worker'], 'replacement-controller')
            self.assertTrue((self.mailbox / 'cancel' / tid).exists())
            self.assertFalse(db.set_job_codex_settings(self.jid, 'gpt-6-sol', 'high'))
            with self.assertRaises(ValueError):
                db.control_job(self.jid, 'resume')
            engine.write_json(self.mailbox / 'results' / (tid + '.json'),
                              {'id': tid, 'status': 'CANCELLED'})
            running.unlink()
            acknowledged.append(tid)

        with patch.object(engine.time, 'sleep', executor_acknowledgement), \
             patch.object(engine, 'copy_skill_environment') as prepare, \
             patch.object(engine, 'request_agent') as develop, \
             patch.object(engine, 'checks') as verify, \
             patch.object(engine, 'snapshot_request'):
            engine.replay_legacy(self.jid)
        self.assertEqual(acknowledged, [tid])
        self.assertEqual(db.job_row(self.jid)['status'], expected_status)
        self.assertFalse(running.exists())
        self.assertFalse((stage / 'agent_attempt.json').exists())
        prepare.assert_not_called()
        develop.assert_not_called()
        verify.assert_not_called()

    def test_crashed_controller_pause_waits_for_surviving_agent_exit(self):
        self.assert_crashed_controller_waits_for_executor('pause', 'USER_PAUSED')

    def test_crashed_controller_stop_waits_for_surviving_agent_exit(self):
        self.assert_crashed_controller_waits_for_executor('stop', 'STOPPED')

    def test_cleanup_failure_stops_runner_before_another_model_round(self):
        from worker.sandbox import RunnerCleanupError
        with db.connect() as connection:
            connection.execute('UPDATE jobs SET max_rounds=2 WHERE id=?', (self.jid,))
        db.claim('independent-verifier')

        def unconfirmed_exit(*args, **kwargs):
            raise RunnerCleanupError('Synthetic container removal was not confirmed')

        with patch.object(engine,'plan_round',side_effect=lambda jid,pid,n,ws,previous,stage,context:({'status':'PASS'},context)), \
             patch.object(engine, 'request_agent', return_value={'status': 'PASS', 'tail': ''}) as develop:
            self.run_worker(unconfirmed_exit, develop)
            self.assertEqual(develop.call_count, 1)
        self.assertEqual(db.job_row(self.jid)['status'], 'FAILED')
        self.assertFalse((self.output / 'round-01' / 'verification_complete.json').exists())
        self.assertFalse((self.output / 'round-02').exists())

    @unittest.skipUnless(os.getenv('LAB_RUN_DOCKER_TESTS') == '1', 'Explicit Docker integration run required')
    def test_real_project_command_cannot_access_controller_state(self):
        db.claim('independent-verifier')
        db.update(self.jid, phase='controller-owned-phase')
        controller_token = self.root / 'controller-only.txt'
        controller_token.write_text('independent-controller-sentinel')
        code = '''import json,os
from pathlib import Path
assert os.getuid() == 1000
assert not any(name in os.environ for name in ['LAB_DB','AWS_SECRET_ACCESS_KEY','INDEPENDENT_CONTROLLER_TOKEN'])
assert not Path(DB_PATH).exists()
assert not Path(TOKEN_PATH).exists()
assert not Path('/var/run/docker.sock').exists()
assert not Path('/agent-tasks/requests').exists()
try:
    Path('/opt/lab/forged-verdict.json').write_text('forged')
except OSError:
    pass
else:
    raise AssertionError('Container root filesystem is writable')
Path('child-result.txt').write_text('isolated child ran')
print(json.dumps({'isolated':True,'uid':os.getuid()}))
'''.replace('DB_PATH', repr(str(db.DB))).replace('TOKEN_PATH', repr(str(controller_token)))
        with patch.dict(os.environ, {'LAB_DB': str(db.DB),
                                     'INDEPENDENT_CONTROLLER_TOKEN': 'test-token-not-a-secret',
                                     'AWS_SECRET_ACCESS_KEY': 'synthetic-key-not-a-secret'}):
            result = engine.command('synthetic_isolation', ['python3', '-c', code],
                self.workspace, self.root / 'security.log', 30, self.jid)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertIn('"isolated": true', result['tail'])
        self.assertEqual((self.workspace / 'child-result.txt').read_text(), 'isolated child ran')
        self.assertEqual(db.job_row(self.jid)['phase'], 'controller-owned-phase')
        self.assertEqual(controller_token.read_text(), 'independent-controller-sentinel')

    @unittest.skipUnless(os.getenv('LAB_RUN_DOCKER_TESTS') == '1', 'Explicit Docker integration run required')
    def test_real_sandbox_timeout_removes_container_and_project_writer(self):
        from worker.sandbox import DockerSandbox
        db.claim('independent-verifier')
        result = engine.command('synthetic_timeout', ['python3', '-c',
            'import time;from pathlib import Path;Path("before-timeout.txt").write_text("started");'
            'time.sleep(20);Path("after-timeout.txt").write_text("should not run")'],
            self.workspace, self.root / 'timeout.log', 2, self.jid)
        self.assertEqual(result['status'], 'TIMEOUT', result)
        self.assertTrue((self.workspace / 'before-timeout.txt').exists())
        self.assertFalse((self.workspace / 'after-timeout.txt').exists())
        with self.assertRaisesRegex(RuntimeError, ': 404:'):
            DockerSandbox().api('GET', '/runs/by-job/' + self.jid)

    @unittest.skipUnless(os.getenv('LAB_VERIFY_PINNED_CLI') == '1', 'Explicit pinned CLI parser run required')
    def test_actual_executor_arguments_parse_on_pinned_cli_without_model_call(self):
        binary = shutil.which('codex')
        self.assertIsNotNone(binary)
        version = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=20)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout.strip(), 'codex-cli 0.162.1')
        for name in ('requests', 'results', 'logs', 'cancel'):
            (self.mailbox / name).mkdir(parents=True, exist_ok=True)
        tid = self.jid + '-round01-' + 'a' * 32
        request = self.mailbox / 'requests' / (tid + '.json')
        request.write_text(json.dumps({'id': tid, 'job_id': self.jid, 'project_id': self.pid,
            'prompt': 'Never submitted to a model.', 'model': 'gpt-6-sol', 'reasoning_effort': 'xhigh'}))
        observed = []

        def capture(command, *args, **kwargs):
            observed.extend(command)
            raise RuntimeError('Verifier captured argv; model execution deliberately prevented')

        with patch.object(executor, 'ROOT', self.mailbox), \
             patch.object(executor, 'PROJECTS', self.projects), \
             patch.object(executor.subprocess, 'Popen', capture):
            executor.run_task(request)
        self.assertTrue(observed, 'Executor did not construct an executable command')
        # --help exits during argument parsing, before authentication or a model request.
        help_result = subprocess.run([*observed, '--help'], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=20)
        self.assertEqual(help_result.returncode, 0, help_result.stdout + help_result.stderr)
        self.assertNotIn('unexpected argument', help_result.stderr)
        self.assertIn('Usage:', help_result.stdout)


if __name__ == '__main__':
    unittest.main()
