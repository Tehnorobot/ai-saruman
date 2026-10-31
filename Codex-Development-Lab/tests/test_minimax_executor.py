"""MiniMax routes through the bounded API executor, never the Codex CLI."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import daemon
from common import db
from worker import engine
from tests import test_timeout_recovery as fixture

SELECTION={'provider':'minimax','model':'MiniMax-M3.1-Flash','reasoning_effort':'default'}


class MiniMaxDaemonTests(unittest.TestCase):
    def test_existing_api_coder_accepts_minimax_but_codex_coder_does_not(self):
        task={**SELECTION,'role':'coder'}
        with patch.object(daemon,'EXECUTOR_ROLE','coder'),patch.object(daemon,'PROVIDER','dslab'):
            self.assertTrue(daemon.matches_executor(task))
        with patch.object(daemon,'EXECUTOR_ROLE','coder'),patch.object(daemon,'PROVIDER','codex'):
            self.assertFalse(daemon.matches_executor(task))

    def test_dedicated_readonly_planner_accepts_minimax_without_role_confusion(self):
        with patch.object(daemon,'EXECUTOR_ROLE','planner'),patch.object(daemon,'PROVIDER','any'):
            self.assertTrue(daemon.matches_executor({**SELECTION,'role':'planner'}))
            self.assertFalse(daemon.matches_executor({**SELECTION,'role':'coder'}))

    def test_minimax_provider_identity_survives_mailbox_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            request=Path(directory)/'request.json'
            request.write_text(json.dumps(SELECTION))
            self.assertEqual(daemon.task_provider(request),'minimax')

    def test_both_roles_use_native_anthropic_executor_without_cli_fallback(self):
        for role in ('coder','planner'):
            with self.subTest(role=role),patch.object(daemon,'EXECUTOR_ROLE',role), \
                    patch.object(daemon,'codex_cli_overrides',side_effect=AssertionError('Codex called')):
                argv=daemon.build_command({**SELECTION,'role':role},Path('.'))
            self.assertIn('-I',argv)
            self.assertEqual(Path(argv[-1]).name,'dslab.py')
            self.assertNotIn('codex',argv)


class MiniMaxWorkerTests(unittest.TestCase):
    setUp=fixture.TimeoutRecoveryTests.setUp
    complete=fixture.TimeoutRecoveryTests.complete
    verify=fixture.TimeoutRecoveryTests.verify

    def test_planner_and_coder_keep_existing_split_and_default_effort(self):
        row={**db.job_row(self.jid),'dslab_token_budget':2000000,
            'dslab_max_output_tokens':8192,'dslab_max_calls':100}
        with patch.dict('os.environ',{'PLANNER_TASKS':str(self.root/'planner-box')}), \
                patch.object(engine.db,'job_row',return_value=row), \
                patch('common.provider_settings.resolve_role',return_value=dict(SELECTION)), \
                patch.object(engine,'poll_job',return_value='USER_PAUSED'), \
                patch.object(engine,'provider_control',return_value={'capability':'synthetic-capability'}) as control:
            engine.request_agent(self.jid,self.pid,1,'Plan',self.stage/'planner/codex.jsonl',role='planner')
            (self.stage/'planner/plan.json').write_text('{}')
            engine.request_agent(self.jid,self.pid,1,'Implement',self.stage/'codex.jsonl')
        registrations=[call.args[2] for call in control.call_args_list if call.args[:2]==('POST','/internal/tasks')]
        self.assertEqual([(r['role'],r['provider'],r['model'],r['effort']) for r in registrations],
            [('planner','minimax',SELECTION['model'],'default'),('coder','minimax',SELECTION['model'],'default')])
        self.assertEqual([r['token_budget'] for r in registrations],[400000,1600000])
        self.assertEqual([r['max_calls'] for r in registrations],[20,80])

    def test_planner_correction_uses_only_remaining_reserve(self):
        row={**db.job_row(self.jid),'dslab_token_budget':100000,'dslab_max_calls':50}
        with patch.dict('os.environ',{'PLANNER_TASKS':str(self.root/'planner-box')}), \
                patch.object(engine.db,'job_row',return_value=row), \
                patch('common.provider_settings.resolve_role',return_value=dict(SELECTION)), \
                patch.object(engine,'poll_job',return_value='USER_PAUSED'), \
                patch.object(engine,'provider_control',return_value={'capability':'synthetic-capability'}) as control:
            engine.request_agent(self.jid,self.pid,1,'Correct',self.stage/'planner/codex.jsonl',
                role='planner',planner_limits={'tokens':6000,'calls':2})
        registration=next(c.args[2] for c in control.call_args_list if c.args[:2]==('POST','/internal/tasks'))
        self.assertEqual((registration['token_budget'],registration['max_calls']),(6000,2))

    def test_minimax_capability_is_revoked_on_completion(self):
        with patch('common.provider_settings.resolve_role',return_value=dict(SELECTION)), \
                patch.object(engine,'provider_control') as control:
            engine.revoke_provider_task('task-id',self.jid)
        control.assert_called_once_with('POST','/internal/tasks/task-id/revoke')

    def test_historical_api_timeout_can_continue_without_a_fixed_attempt_cap(self):
        self.stage.mkdir(parents=True)
        usage={'totals':dict.fromkeys(('requests','input_tokens','output_tokens','cache_creation_input_tokens',
                                      'cache_read_input_tokens','billing_unknown_requests'),0)}
        with patch('common.provider_settings.resolve_role',return_value=dict(SELECTION)), \
                patch.object(engine,'provider_control',return_value=usage), \
                patch.object(engine.time,'sleep'), \
                patch.object(engine,'request_agent',side_effect=[{'label':'minimax','status':'TIMEOUT','tail':'timeout',
                    'duration_seconds':10},{'label':'minimax','status':'PASS','tail':'continued saved work'}]) as request:
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(request.call_count,2)
        state=json.loads((self.stage/'timeout_recovery.json').read_text())
        self.assertEqual(state['attempts_started'],2)

    def test_gateway_error_names_selected_provider_and_cannot_fall_back(self):
        with patch('common.provider_settings.resolve_role',return_value=dict(SELECTION)), \
                patch.object(engine,'provider_control',side_effect=engine.GatewayError('Gateway unavailable')):
            result=engine.request_agent(self.jid,self.pid,1,'Implement',self.stage/'codex.jsonl')
        self.assertEqual((result['label'],result['status']),('minimax','ERROR'))
        self.assertFalse(list((self.mailbox/'requests').glob('*.json')))

    def test_minimax_provider_failure_pauses_without_running_verification(self):
        with patch('common.provider_settings.resolve_role',return_value=dict(SELECTION)), \
                patch.object(engine,'develop_round',return_value={'label':'codex','status':'FAIL','tail':'quota'}) as develop, \
                patch.object(engine,'checks') as checks:
            engine.replay_legacy(self.jid)
        develop.assert_called_once();checks.assert_not_called()
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase']),('USER_PAUSED','provider_error'))
