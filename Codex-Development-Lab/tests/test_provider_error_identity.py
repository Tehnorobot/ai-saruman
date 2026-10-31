"""Failure text identifies the actual provider, model and role, not its adapter."""
import json
import os
import unittest
from unittest.mock import patch
from worker import engine
from tests import test_timeout_recovery as fixture


class ProviderErrorIdentityTests(unittest.TestCase):
    def test_timeout_is_minimax_not_codex(self):
        message=engine.agent_error({'provider':'minimax','model':'MiniMax-M3.1-Flash',
            'role':'planner','status':'TIMEOUT','duration_seconds':600.5,'error':'Execution time limit reached'})
        self.assertTrue(message.startswith('MiniMax / MiniMax-M3.1-Flash [planner] TIMEOUT;'),message)
        self.assertNotIn('Codex',message)
        self.assertNotIn('DSLab',message)

    def test_structured_failure_uses_same_identity(self):
        message=engine.agent_error({'provider':'litellm','model':'server-model','role':'coder',
            'status':'FAIL','tail':json.dumps({'type':'turn.failed','error':{'message':'API stream interrupted'}})})
        self.assertIn('LiteLLM / server-model [coder] FAIL',message)
        self.assertIn('API stream interrupted',message)

    def test_missing_provider_does_not_invent_codex(self):
        self.assertTrue(engine.agent_error({'status':'ERROR','error':'Missing metadata'}).startswith('Agent ERROR;'))


class RecoveredProviderIdentityTests(unittest.TestCase):
    setUp=fixture.TimeoutRecoveryTests.setUp
    verify=fixture.TimeoutRecoveryTests.verify
    complete=fixture.TimeoutRecoveryTests.complete

    def test_recovered_attempt_uses_archived_provider_not_current_job_selection(self):
        box=self.root/'planner-box'
        for name in ('results','logs'):(box/name).mkdir(parents=True)
        folder=self.stage/'planner';folder.mkdir(parents=True)
        tid=self.jid+'-round01-'+'a'*32
        (folder/'agent_attempt.json').write_text(json.dumps({'id':tid}))
        archive=self.stage/'agent-history'/tid;archive.mkdir(parents=True)
        (archive/'request.json').write_text(json.dumps({'id':tid,'job_id':self.jid,'project_id':self.pid,
            'role':'planner','provider':'minimax','model':'MiniMax-M3.1-Flash','reasoning_effort':'low'}))
        (box/'results'/(tid+'.json')).write_text(json.dumps({'id':tid,'status':'TIMEOUT','error':'Configured time limit'}))
        with patch.dict(os.environ,{'PLANNER_TASKS':str(box)}), \
                patch.object(engine,'provider_control'):
            result=engine.request_agent(self.jid,self.pid,1,'Not a new request',folder/'codex.jsonl',
                role='planner',preserve_result=True)
        self.assertEqual(result['provider'],'minimax')
        self.assertEqual(result['model'],'MiniMax-M3.1-Flash')
        self.assertEqual(result['label'],'minimax')
        self.assertIn('MiniMax',engine.agent_error(result))
