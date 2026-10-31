"""Actual controller mailbox can outlive a soft target without a new model call."""
import json
import os
import unittest
from unittest.mock import patch

from worker import engine
from tests import test_timeout_recovery as fixture


class SoftDeadlineTransportTests(unittest.TestCase):
    setUp=fixture.TimeoutRecoveryTests.setUp
    complete=fixture.TimeoutRecoveryTests.complete
    verify=fixture.TimeoutRecoveryTests.verify

    def test_long_pending_task_is_not_cancelled_and_api_lease_renews(self):
        clock=[0]
        waits=[]
        selection={'provider':'minimax','model':'MiniMax-M3.1-Flash','reasoning_effort':'low'}
        def wait(delay):
            waits.append(delay)
            pending=list((self.mailbox/'requests').glob('*.json'))
            self.assertEqual(len(pending),1)
            task=json.loads(pending[0].read_text())
            self.assertFalse((self.mailbox/'cancel'/task['id']).exists())
            if len(waits)==1:
                clock[0]=10000  # Past both agent's soft interval and its old TTL.
            else:
                engine.write_json(self.mailbox/'results'/pending[0].name,
                                  {'id':task['id'],'status':'PASS'})
        with patch.dict(os.environ,{'AGENT_TASKS':str(self.mailbox)}), \
                patch('common.provider_settings.resolve_role',return_value=selection), \
                patch.object(engine.time,'monotonic',side_effect=lambda:clock[0]), \
                patch.object(engine.time,'sleep',side_effect=wait), \
                patch.object(engine,'provider_control',return_value={'capability':'test-only'}) as control:
            result=engine.request_agent(self.jid,self.pid,1,'Preserve source',self.stage/'codex.jsonl',
                                        output_contract='AgentReport')
        self.assertEqual(result['status'],'PASS')
        registrations=[c for c in control.call_args_list if c.args[:2]==('POST','/internal/tasks')]
        renewals=[c for c in control.call_args_list if c.args[0]=='POST' and c.args[1].endswith('/renew')]
        self.assertEqual(len(registrations),1,'A renewal must not reset the task budget')
        self.assertEqual(len(renewals),1)
        self.assertEqual(renewals[0].args[2],{'job_id':self.jid,'project_id':self.pid,'ttl_seconds':3600})
        self.assertEqual(self.sentinel.read_text(),'implementation from timed-out attempt')


if __name__=='__main__':unittest.main()
