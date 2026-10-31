"""The configured API output limit applies to planning as well as coding."""
import unittest
from unittest.mock import patch

from common import db
from worker import engine
from tests import test_timeout_recovery as fixture


class PlannerOutputLimitTests(unittest.TestCase):
    setUp=fixture.TimeoutRecoveryTests.setUp
    complete=fixture.TimeoutRecoveryTests.complete
    verify=fixture.TimeoutRecoveryTests.verify

    def register(self,*,provider='minimax',maximum=32192,budget=9000000,limits=None):
        selection={'provider':provider,
                   'model':{'minimax':'MiniMax-M3.1-Flash','dslab':'claude-sonnet-5-5',
                            'litellm':'deepseek-ai/DeepSeek-V4-Flash-0731',
                            'codex':'gpt-6.1-sol'}[provider],
                   'reasoning_effort':'default'}
        row={**db.job_row(self.jid),'dslab_token_budget':budget,
             'dslab_max_output_tokens':maximum,'dslab_max_calls':100}
        options={'planner_limits':limits} if limits is not None else {}
        with patch.dict('os.environ',{'PLANNER_TASKS':str(self.root/'planner-box')}), \
                patch.object(engine.db,'job_row',return_value=row), \
                patch('common.provider_settings.resolve_role',return_value=selection), \
                patch.object(engine,'poll_job',return_value='USER_PAUSED'), \
                patch.object(engine,'provider_control',return_value={'capability':'synthetic-capability'}) as control:
            result=engine.request_agent(self.jid,self.pid,1,'Plan the next bounded step',
                self.stage/'planner/codex.jsonl',role='planner',**options)
        self.assertEqual(result['status'],'USER_PAUSED')
        return [call.args[2] for call in control.call_args_list
                if call.args[:2]==('POST','/internal/tasks')]

    def test_user_maximum_32192_reaches_minimax_planner_without_hidden_4096_cap(self):
        registrations=self.register()
        self.assertEqual(len(registrations),1)
        registered=registrations[0]
        self.assertEqual(registered['max_output_tokens'],32192)
        self.assertEqual(registered['token_budget'],1800000)
        self.assertEqual(registered['max_calls'],20)
        self.assertEqual(registered['effort'],'default')

    def test_default_output_8192_is_not_reduced_for_planning(self):
        registered=self.register(maximum=8192,budget=1000000)[0]
        self.assertEqual(registered['max_output_tokens'],8192)
        self.assertEqual(registered['token_budget'],200000)

    def test_correction_cannot_exceed_remaining_token_or_request_reserve(self):
        registered=self.register(limits={'tokens':6000,'calls':2})[0]
        self.assertEqual(registered['max_output_tokens'],6000)
        self.assertEqual(registered['token_budget'],6000)
        self.assertEqual(registered['max_calls'],2)

    def test_small_original_planner_budget_still_caps_output(self):
        registered=self.register(budget=10000)[0]
        self.assertEqual(registered['max_output_tokens'],2000)
        self.assertEqual(registered['token_budget'],2000)

    def test_correction_keeps_smaller_user_output_limit(self):
        registered=self.register(maximum=2048,limits={'tokens':6000,'calls':2})[0]
        self.assertEqual(registered['max_output_tokens'],2048)
        self.assertEqual(registered['token_budget'],6000)

    def test_same_output_contract_applies_to_dslab_and_litellm(self):
        for provider in ('dslab','litellm'):
            with self.subTest(provider=provider):
                self.assertEqual(self.register(provider=provider)[0]['max_output_tokens'],32192)

    def test_codex_planner_does_not_register_api_or_inherit_api_output_fields(self):
        self.assertEqual(self.register(provider='codex'),[])

    def test_gateway_lease_is_independent_of_soft_planner_target(self):
        with patch.dict('os.environ', {'PLANNER_TIMEOUT_SECONDS': '7200'}):
            registered=self.register()[0]
        self.assertEqual(registered['ttl_seconds'],3600)
