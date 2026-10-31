"""Actual dashboard accounting boundary; no external requests or model calls."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from common import db
from common.provider_client import GatewayError


class TokenUsageHttpTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.artifacts=self.root/'artifacts'
        self.mailbox=self.root/'coder';self.planner=self.root/'planner'
        for context in (patch.object(db,'DB',self.root/'db.sqlite'),
                patch.dict(os.environ,{'LAB_PROJECTS':str(self.root/'projects'),
                                      'PLANNER_TASKS':str(self.planner)}),
                patch.object(main,'ARTIFACTS',self.artifacts),
                patch.object(main,'AGENT_TASKS',self.mailbox)):
            context.start();self.addCleanup(context.stop)
        self.pid,self.jid=db.create_project('Token accounting','spec.md',b'# Mod\n\nImplement behavior.')
        self.tid=self.jid+'-round01-'+'a'*32
        self.client=TestClient(main.app)
        # Accounting assertions use a completed index snapshot. The background
        # scheduling/nonblocking contract is covered by request-history tests.
        def indexed(artifacts,mailboxes,pid,jid):
            return main.usage_accounting.codex_usage(artifacts,mailboxes,pid,jid,request_limit=None), {
                'codex_loading':False,'codex_stale':False}
        cached=patch.object(main.request_history,'snapshot',side_effect=indexed)
        cached.start();self.addCleanup(cached.stop)

    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def archive(self):
        folder=self.artifacts/self.pid/self.jid/'round-01/agent-history'/self.tid
        self.write(folder/'request.json',{'id':self.tid,'job_id':self.jid,'project_id':self.pid,
            'provider':'codex','role':'planner','model':'test-model'})
        self.write(folder/'meta.json',{'id':self.tid,'job_id':self.jid,'project_id':self.pid,
            'status':'PASS','created_at':'2026-10-25T10:00:00+00:00'})
        (folder/'events.jsonl').write_text(json.dumps({'type':'turn.completed','usage':{
            'input_tokens':100,'cached_input_tokens':80,'output_tokens':7}})+'\n',encoding='utf-8')

    def get(self):
        response=self.client.get('/api/jobs/'+self.jid+'/usage')
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_codex_remains_available_when_api_gateway_is_down_and_reading_is_pure(self):
        self.archive();before=db.job_row(self.jid)
        with patch.object(main,'provider_control',side_effect=GatewayError('offline')):
            first=self.get();second=self.get()
        self.assertTrue(first['available']);self.assertFalse(first['api_available'])
        self.assertTrue(first['codex_usage_available'])
        totals=first['totals']
        self.assertEqual((totals['input_tokens_total'],totals['output_tokens'],totals['total_tokens']),(100,7,107))
        self.assertEqual((totals['saved_input_tokens'],totals['cache_hit_percent']),(80,80))
        self.assertIsNone(totals['estimated_rub'])
        self.assertEqual(second['totals'],totals)
        self.assertEqual(db.job_row(self.jid),before)

    def test_api_and_codex_merge_without_counting_cached_input_twice(self):
        self.archive()
        api={'available':True,'totals':{'input_tokens':200,'output_tokens':30,
            'cache_read_input_tokens':50,'cache_creation_input_tokens':0,'requests':1,
            'estimated_rub':1.25,'billing_unknown_requests':0},'roles':[], 'requests':[]}
        with patch.object(main,'provider_control',return_value=api):report=self.get()
        self.assertEqual(report['totals']['input_tokens_total'],350)
        self.assertEqual(report['totals']['total_tokens'],387)
        self.assertEqual(report['totals']['saved_input_tokens'],130)
        self.assertEqual(report['api_totals']['estimated_rub'],1.25)
        self.assertEqual(report['totals']['api_requests'],1)
        self.assertEqual(report['totals']['codex_completed_turns'],1)
        self.assertEqual(report['codex_daily'][0]['date'],'2026-10-25')

    def test_live_planner_mailbox_is_included_without_changing_agent_state(self):
        request=self.planner/'running'/(self.tid+'.json')
        self.write(request,{'id':self.tid,'job_id':self.jid,'project_id':self.pid,
            'provider':'codex','role':'planner','model':'test-model'})
        log=self.planner/'logs'/(self.tid+'.jsonl');log.parent.mkdir(parents=True)
        log.write_text('{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}\n')
        before=request.read_bytes()
        with patch.object(main,'provider_control',side_effect=GatewayError('offline')):report=self.get()
        self.assertEqual(report['totals']['total_tokens'],15)
        self.assertEqual(report['roles'][0]['role'],'planner')
        self.assertEqual(request.read_bytes(),before)

    def test_unknown_job_does_not_scan_logs_or_call_gateway(self):
        with patch.object(main,'provider_control') as gateway, \
                patch.object(main.usage_accounting,'codex_usage') as scan:
            self.assertEqual(self.client.get('/api/jobs/'+'f'*14+'/usage').status_code,404)
        gateway.assert_not_called();scan.assert_not_called()
