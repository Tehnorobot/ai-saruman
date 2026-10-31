"""Role handoff targets never terminate an active model process."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent import daemon
from common.execution_limits import role_timeout


class ExecutionLimitsTests(unittest.TestCase):
    def test_planner_inherits_configured_agent_timeout_without_hidden_cap(self):
        with patch.dict(os.environ, {'PLANNER_TIMEOUT_SECONDS': ''}):
            self.assertEqual(role_timeout('planner', 7200), 7200)
            self.assertEqual(role_timeout('coder', 7200), 7200)

    def test_explicit_planner_timeout_does_not_change_coder(self):
        with patch.dict(os.environ, {'PLANNER_TIMEOUT_SECONDS': '900'}):
            self.assertEqual(role_timeout('planner', 3600), 900)
            self.assertEqual(role_timeout('coder', 3600), 3600)

    def test_invalid_timeout_is_explicit_configuration_error(self):
        for value in ('zero', '0', '-1', '59', '1.5'):
            with self.subTest(value=value), patch.dict(os.environ, {'PLANNER_TIMEOUT_SECONDS': value}):
                with self.assertRaisesRegex(ValueError, 'PLANNER_TIMEOUT_SECONDS'):
                    role_timeout('planner', 3600)

    def run_planner(self, limit, clocks, polls):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / 'projects'
            box = root / 'box'
            pid, jid = 'a' * 14, 'b' * 14
            (projects / pid / 'workspace').mkdir(parents=True)
            (box / 'requests').mkdir(parents=True)
            source = box / 'requests' / (jid + '-round01.json')
            source.write_text(json.dumps({'id': source.stem, 'project_id': pid, 'job_id': jid,
                'role': 'planner', 'provider': 'minimax', 'prompt': 'Plan'}))
            process = MagicMock(returncode=0)
            process.poll.side_effect = polls
            scope = MagicMock()
            scope.close.return_value = []
            with patch.dict(os.environ, {'PLANNER_TIMEOUT_SECONDS': str(limit)}), \
                    patch.object(daemon, 'PROJECTS', projects), patch.object(daemon, 'ROOT', box), \
                    patch.object(daemon, 'EXECUTOR_ROLE', 'planner'), \
                    patch.object(daemon, 'TaskProcesses', return_value=scope), \
                    patch.object(daemon.time, 'monotonic', side_effect=clocks), \
                    patch.object(daemon.time, 'sleep'), \
                    patch.object(daemon.subprocess, 'Popen', return_value=process):
                daemon._run_task(source)
            return json.loads((box / 'results' / source.name).read_text())

    def test_planner_can_finish_after_600_seconds_within_configured_limit(self):
        result = self.run_planner(900, [0, 700, 701], [None, 0, 0])
        self.assertEqual(result['status'], 'PASS', result)

    def test_elapsed_role_limit_does_not_kill_successful_planner(self):
        result = self.run_planner(900, [0, 901, 902], [None, 0, 0])
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['duration_seconds'],901)
        self.assertIsNone(result['error'])

    def test_reviewer_has_its_own_soft_target(self):
        with patch.dict(os.environ, {'REVIEWER_TIMEOUT_SECONDS':'7200'}):
            self.assertEqual(role_timeout('reviewer',3600),7200)
            self.assertEqual(role_timeout('coder',3600),3600)
