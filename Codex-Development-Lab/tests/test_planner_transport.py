"""Planner permissions are enforced at dispatch, independently of model output."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import daemon, dslab
from common import history


class PlannerTransportTests(unittest.TestCase):
    def test_coder_patch_cleanup_is_recovered_only_by_fresh_readonly_executor(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);failed=root/'cleanup-failed';failed.mkdir()
            tid='a'*14+'-round01-'+'b'*32;marker=failed/(tid+'.json')
            marker.write_text(json.dumps({'pid_namespace':123,'result':{'id':tid,'role':'coder',
                'provider':'minimax','output_contract':'SubmissionPatch','status':'ERROR'}}))
            with patch.object(daemon,'ROOT',root),patch.object(daemon,'EXECUTOR_ROLE','coder'),patch.object(daemon,'PROVIDER','dslab'),patch.object(daemon,'pid_namespace',return_value=456):
                daemon.acknowledge_restarted_cleanup()
            self.assertTrue(marker.exists())
            with patch.object(daemon,'ROOT',root),patch.object(daemon,'EXECUTOR_ROLE','planner'),patch.object(daemon,'PROVIDER','any'),patch.object(daemon,'pid_namespace',return_value=456):
                daemon.acknowledge_restarted_cleanup()
            self.assertFalse(marker.exists())
            result=json.loads((root/'results'/marker.name).read_text())
            self.assertEqual(result['output_contract'],'SubmissionPatch')

    def test_planner_compose_project_mount_is_readonly_and_has_no_host_control(self):
        if not (Path(__file__).resolve().parents[1] / 'compose.yaml').exists():
            self.skipTest('Deployment manifest is checked from the source tree and on the running container')
        compose = (Path(__file__).resolve().parents[1] / 'compose.yaml').read_text(encoding='utf-8')
        block = compose.split('\n  agent-planner:\n', 1)[1].split('\n  backup:\n', 1)[0]
        self.assertIn('- ./projects:/projects:ro', block)
        self.assertNotIn('- ./projects:/projects\n', block)
        self.assertIn('read_only: true', block)
        self.assertIn('networks: [agent_internal]', block)
        self.assertIn('- ./planner-tasks:/agent-tasks', block)
        self.assertNotIn('- ./agent-tasks:/agent-tasks', block)
        self.assertIn('- planner_auth:/home/lab/.codex', block)
        self.assertIn('- codex_auth:/planner-auth:ro', block)
        self.assertNotIn('- codex_auth:/home/lab/.codex', block)
        self.assertNotIn('docker.sock', block)
        self.assertNotIn('cap_add:', block)
        self.assertNotIn('privileged:', block)
        common = compose.split('services:', 1)[0]
        self.assertIn('cap_drop: [ALL]', common)
        self.assertIn('no-new-privileges:true', common)

    def test_auth_bootstrap_links_without_reading_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'mounted-auth.json'
            source.touch()
            private = root / 'private'
            with patch.object(daemon, 'EXECUTOR_ROLE', 'planner'), \
                    patch.object(daemon, 'PLANNER_AUTH_SOURCE', source), \
                    patch.dict(os.environ, {'CODEX_HOME': str(private)}), \
                    patch.object(Path, 'read_text', side_effect=AssertionError('Credentials read')), \
                    patch.object(Path, 'read_bytes', side_effect=AssertionError('Credentials read')), \
                    patch.object(daemon.os, 'symlink') as symlink:
                daemon.bootstrap_planner_auth()
            symlink.assert_called_once_with(str(source), str(private / 'auth.json'))
            self.assertEqual(source.stat().st_size, 0)

    def test_auth_bootstrap_never_overwrites_existing_or_dangling_auth(self):
        with patch.object(daemon, 'EXECUTOR_ROLE', 'planner'), \
                patch.object(daemon.os.path, 'lexists', return_value=True), \
                patch.object(daemon.os, 'symlink') as symlink, \
                patch.object(Path, 'mkdir') as mkdir:
            daemon.bootstrap_planner_auth()
            symlink.assert_not_called()
            mkdir.assert_not_called()

    def test_auth_bootstrap_does_nothing_in_coder_executor(self):
        with patch.object(daemon, 'EXECUTOR_ROLE', 'coder'), patch.object(daemon.os, 'symlink') as symlink:
            daemon.bootstrap_planner_auth()
            symlink.assert_not_called()

    def test_codex_planner_cannot_load_writable_child_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            agents = root / '.codex' / 'agents'
            agents.mkdir(parents=True)
            (agents / 'implementer.toml').write_text('sandbox_mode="danger-full-access"')
            with patch.object(daemon, 'EXECUTOR_ROLE', 'planner'), \
                    patch.object(daemon, 'codex_agent_overrides', side_effect=AssertionError('Writable roles loaded')):
                command = daemon.build_command({'role': 'planner', 'model': 'gpt-6-sol',
                                                'reasoning_effort': 'medium'}, root)
            self.assertEqual(command[command.index('--sandbox') + 1], 'danger-full-access')
            self.assertIn('features.multi_agent=false', command)
            self.assertIn('approval_policy="never"', command)
            self.assertNotIn('features.multi_agent=true', command)
            self.assertIn('gpt-6-sol', command)

    def test_planner_cannot_run_in_writable_coder_executor(self):
        for provider in ('codex', 'dslab'):
            with self.subTest(provider=provider), patch.object(daemon, 'EXECUTOR_ROLE', 'coder'), \
                    self.assertRaisesRegex(ValueError, 'dedicated executor'):
                daemon.build_command({'provider': provider, 'role': 'planner'}, Path('.'))

    def test_mailbox_claims_are_partitioned_by_role_and_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            request = Path(folder) / 'request.json'
            for executor_role, provider in (('coder', 'codex'), ('coder', 'dslab'), ('planner', 'any')):
                with patch.object(daemon, 'EXECUTOR_ROLE', executor_role), patch.object(daemon, 'PROVIDER', provider):
                    for task_role in ('coder', 'planner'):
                        for task_provider in ('codex', 'dslab'):
                            request.write_text(json.dumps({'role': task_role, 'provider': task_provider}))
                            self.assertEqual(daemon.task_matches(request),
                                             executor_role == task_role and (provider == 'any' or provider == task_provider))

    def test_planner_does_not_install_skills_or_take_a_writable_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            jid = 'a' * 14
            request = Path(folder) / (jid + '-round01-' + 'b' * 32 + '.json')
            request.write_text(json.dumps({'id': request.stem, 'project_id': 'c' * 14,
                                           'job_id': jid, 'role': 'planner', 'provider': 'codex'}))
            with patch.object(daemon, 'EXECUTOR_ROLE', 'planner'), patch.object(daemon, 'PROVIDER', 'any'), \
                    patch.object(daemon, 'project_lock', side_effect=AssertionError('Writable lock')), \
                    patch.object(daemon, 'install_skills', side_effect=AssertionError('Write skills')), \
                    patch.object(daemon, '_run_task', return_value='planned') as run:
                self.assertEqual(daemon.run_task(request), 'planned')
                run.assert_called_once_with(request)

    def test_coder_never_acknowledges_failed_planner_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            failed = root / 'cleanup-failed'
            failed.mkdir()
            task_id = 'a' * 14 + '-round01-' + 'b' * 32
            marker = failed / (task_id + '.json')
            marker.write_text(json.dumps({'pid_namespace': 123,
                                         'result': {'id': task_id, 'role': 'planner',
                                                    'provider': 'codex', 'status': 'ERROR'}}))
            with patch.object(daemon, 'ROOT', root), patch.object(daemon, 'EXECUTOR_ROLE', 'coder'), \
                    patch.object(daemon, 'PROVIDER', 'codex'), patch.object(daemon, 'pid_namespace', return_value=456):
                daemon.acknowledge_restarted_cleanup()
            self.assertTrue(marker.exists())
            self.assertFalse((root / 'results' / marker.name).exists())
            with patch.object(daemon, 'ROOT', root), patch.object(daemon, 'EXECUTOR_ROLE', 'planner'), \
                    patch.object(daemon, 'PROVIDER', 'any'), patch.object(daemon, 'pid_namespace', return_value=456):
                daemon.acknowledge_restarted_cleanup()
            self.assertFalse(marker.exists())
            self.assertTrue((root / 'results' / marker.name).is_file())

    def test_legacy_coder_keeps_existing_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            command = daemon.build_command({}, Path(folder))
        self.assertEqual(command[command.index('--sandbox') + 1], 'danger-full-access')
        self.assertIn('features.multi_agent=true', command)

    def test_unknown_roles_fail_for_both_providers(self):
        for provider in ('codex', 'dslab'):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                daemon.build_command({'provider': provider, 'role': 'administrator'}, Path('.'))

    def test_dslab_planner_has_bounded_readonly_execution(self):
        request = {'model': 'example', 'prompt': 'Plan current work.', 'role': 'planner'}
        with patch.object(dslab.sys, 'stdin', io.StringIO(json.dumps(request))), \
                patch.object(dslab, 'Executor') as executor, patch.object(dslab, 'event'):
            self.assertEqual(dslab.main(), 0)
            executor.return_value.run.assert_called_once_with(
                request['prompt'], agent='planner', readonly=True, turns=12)

    def test_dslab_default_coder_keeps_execution_profile(self):
        request = {'model': 'example', 'prompt': 'Implement current work.'}
        with patch.object(dslab.sys, 'stdin', io.StringIO(json.dumps(request))), \
                patch.object(dslab, 'Executor') as executor, patch.object(dslab, 'event'):
            self.assertEqual(dslab.main(), 0)
            executor.return_value.run.assert_called_once_with(request['prompt'])

    def test_planner_uses_registered_call_limit_instead_of_twelve(self):
        request={'model':'example','prompt':'Plan','role':'planner','dslab_max_calls':20}
        with patch.object(dslab.sys,'stdin',io.StringIO(json.dumps(request))), \
             patch.object(dslab,'Executor') as executor,patch.object(dslab,'event'):
            self.assertEqual(dslab.main(),0)
            executor.return_value.run.assert_called_once_with('Plan',agent='planner',readonly=True,turns=20)

    def test_planner_accepts_user_call_limit_above_old_ceiling(self):
        request={'model':'example','prompt':'Plan','role':'planner','dslab_max_calls':50000}
        with patch.object(dslab.sys,'stdin',io.StringIO(json.dumps(request))),patch.object(dslab,'Executor') as executor,patch.object(dslab,'event'):
            self.assertEqual(dslab.main(),0)
            executor.return_value.run.assert_called_once_with('Plan',agent='planner',readonly=True,turns=50000)

    def test_invalid_planner_limits_cannot_create_unbounded_execution(self):
        for limit in (0,-1,True,'20'):
            request={'model':'example','prompt':'Plan','role':'planner','dslab_max_calls':limit}
            with self.subTest(limit=limit),patch.object(dslab.sys,'stdin',io.StringIO(json.dumps(request))), \
                 patch.object(dslab,'Executor') as executor,patch.object(dslab,'event'):
                self.assertEqual(dslab.main(),1)
                executor.assert_not_called()

    def test_dslab_unknown_role_never_runs_executor(self):
        request = {'model': 'example', 'prompt': 'Unsafe', 'role': 'administrator'}
        with patch.object(dslab.sys, 'stdin', io.StringIO(json.dumps(request))), \
                patch.object(dslab, 'Executor') as executor, patch.object(dslab, 'event'):
            self.assertEqual(dslab.main(), 1)
            executor.assert_not_called()

    def test_readonly_tools_reject_all_writers_and_delegation(self):
        with tempfile.TemporaryDirectory() as folder:
            executor = dslab.Executor({}, Path(folder))
            for name in ('write_file', 'replace_text', 'run_command', 'delegate'):
                with self.subTest(tool=name), self.assertRaisesRegex(ValueError, 'read-only'):
                    executor.tool(name, {}, agent='planner', readonly=True)

    def test_archived_role_does_not_expose_provider_credentials(self):
        pid, jid = 'a' * 14, 'b' * 14
        tid = jid + '-round01-' + 'c' * 32
        saved = history._request({'project_id': pid, 'job_id': jid, 'id': tid,
                                  'role': 'planner', 'dslab_capability': 'SECRET'}, pid, jid, tid)
        self.assertEqual(saved['role'], 'planner')
        self.assertNotIn('dslab_capability', saved)


if __name__ == '__main__':
    unittest.main()
