"""Codex failure classification must not trust text from completed tool calls."""
import json
import unittest

from worker import engine


class LegacyFailureClassificationTests(unittest.TestCase):
    def test_terminal_cli_codes_are_classified(self):
        for code in ('usage_limit','rate_limit_exceeded','quota_exceeded'):
            with self.subTest(code=code):
                self.assertTrue(engine.is_quota(json.dumps({'type':'error','code':code})))
                self.assertTrue(engine.is_quota(json.dumps({'type':'turn.failed','error':{'code':code}})))
        for code in ('authentication','invalid_api_key','unauthorized'):
            with self.subTest(code=code):
                self.assertTrue(engine.is_auth_failure(json.dumps({'type':'turn.failed','error':{'code':code}})))
                self.assertFalse(engine.is_quota(json.dumps({'type':'error','code':code})))

    def test_tool_output_and_agent_messages_cannot_pause_job(self):
        text='ERROR: You have reached your weekly limit, reset tomorrow; unauthorized'
        for item in ({'type':'command_execution','aggregated_output':text},
                     {'type':'agent_message','text':text},
                     {'type':'command_execution','aggregated_output':json.dumps({'type':'error','code':'rate_limit_exceeded'})}):
            with self.subTest(item=item['type']):
                transcript=json.dumps({'type':'item.completed','item':item})
                self.assertFalse(engine.is_quota(transcript))
                self.assertFalse(engine.is_auth_failure(transcript))

    def test_unrelated_terminal_error_does_not_reclassify_earlier_output(self):
        transcript='\n'.join((json.dumps({'type':'item.completed','item':{
            'type':'command_execution','aggregated_output':'quota exceeded: unauthorized'}}),
            json.dumps({'type':'turn.failed','error':{'message':'CLI process failed'}})))
        self.assertFalse(engine.is_quota(transcript))
        self.assertFalse(engine.is_auth_failure(transcript))

    def test_unknown_code_overrides_diagnostic_prose(self):
        for code in ('invalid_request', 'error', None, ['rate_limit_exceeded']):
            transcript=json.dumps({'type':'turn.failed','error':{
                'code':code,'message':'You have reached your usage limit'}})
            self.assertFalse(engine.is_quota(transcript))

    def test_narrow_plain_cli_compatibility(self):
        self.assertTrue(engine.is_quota('ERROR: You have reached your weekly limit, reset tomorrow'))
        self.assertTrue(engine.is_quota('You have reached your usage limit'))
        self.assertTrue(engine.is_auth_failure('Authentication required: login'))
        for text in ('Java rate limit exception','Compilation failed: unauthorized',
                     'Tool output: You have reached your usage limit',
                     'You have reached your usage limit\ncompilation failed'):
            self.assertFalse(engine.is_quota(text))
            self.assertFalse(engine.is_auth_failure(text))


if __name__=='__main__':
    unittest.main()
