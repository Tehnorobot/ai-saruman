"""Consent and role-configuration boundaries; no model, Docker or game calls."""
import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch

from common.codex_settings import codex_agent_overrides
from runner.policy import container_config, validate_request
from worker import engine
from common import db


class ExecutionPolicyTests(unittest.TestCase):
    def request(self, **fields):
        return {'project': 'a'*14, 'job': 'b'*14, 'argv': ['gradle', 'build'], 'record': False, **fields}

    def test_game_consent_is_opt_in_and_cannot_be_an_environment_dictionary(self):
        for fields, expected in (({}, 'false'), ({'eula': False}, 'false'), ({'eula': True}, 'true')):
            config = container_config('fixed-image', 'private-volume', 'c'*32, self.request(**fields), 'd'*32)
            env = dict(item.split('=', 1) for item in config['Env'])
            self.assertEqual(env['MINECRAFT_EULA_ACCEPTED'], expected)
        for value in ('true', 1, None, {'DOCKER_HOST': 'unsafe'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_request(self.request(eula=value))
        with self.assertRaises(ValueError):
            validate_request(self.request(env={'MINECRAFT_EULA_ACCEPTED': 'true'}))

    def test_project_command_cannot_override_operator_game_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(engine, 'EULA', False), patch.object(engine.DockerSandbox, 'execute',
                    return_value={'status': 'PASS', 'exit_code': 0}) as execute:
                engine.command('compile', ['gradle', 'build'], root, root/'build.log', 10, 'b'*14,
                               env={'MINECRAFT_EULA_ACCEPTED': 'true', 'CI': 'false'})
            self.assertEqual(execute.call_args.kwargs['env']['MINECRAFT_EULA_ACCEPTED'], 'false')

    def test_roles_are_explicit_cli_overrides_without_modifying_global_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            role = root/'.codex'/'agents'/'reviewer.toml'
            role.parent.mkdir(parents=True)
            role.write_text('developer_instructions="review only"')
            args = codex_agent_overrides(root)
            values = [args[i+1] for i, arg in enumerate(args) if arg == '-c']
            self.assertIn('features.multi_agent=true', values)
            entry = next(value for value in values if value.startswith('agents.reviewer.config_file='))
            self.assertEqual(json.loads(entry.split('=', 1)[1]), str(role))
            self.assertFalse((root/'config.toml').exists())

    @unittest.skipUnless(os.name == 'posix', 'Actual role-config link boundary requires POSIX')
    def test_role_config_cannot_reference_a_file_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root/'workspace'
            roles = workspace/'.codex'/'agents'
            roles.mkdir(parents=True)
            external = root/'external.toml'
            external.write_text('developer_instructions="external"')
            (roles/'reviewer.toml').symlink_to(external)
            with self.assertRaises(ValueError):
                codex_agent_overrides(workspace)

    def test_parallel_service_startup_migrates_resume_generation_once(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(db, 'DB', Path(temporary)/'state.sqlite'):
            db.init()
            with db.connect() as connection:
                connection.execute('ALTER TABLE jobs DROP COLUMN resume_generation')
            barrier = Barrier(6)
            def startup(_index):
                barrier.wait(timeout=10)
                db.init()
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(startup, range(6)))
            with db.connect() as connection:
                columns = [row['name'] for row in connection.execute('PRAGMA table_info(jobs)')]
            self.assertEqual(columns.count('resume_generation'), 1)


if __name__ == '__main__':
    unittest.main()
