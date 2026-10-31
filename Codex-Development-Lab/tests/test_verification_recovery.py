"""Exercise real worker checkpoints: harness errors must not buy another turn."""
import json
from pathlib import Path
import tempfile
from threading import Thread, Event
import unittest
from unittest.mock import patch

from common import db
from common.project_lock import project_lock
from worker import engine
from worker.sandbox import DockerSandbox
from tests.test_timeout_recovery import TimeoutRecoveryTests


class VerificationRecoveryTests(unittest.TestCase):
    setUp = TimeoutRecoveryTests.setUp
    complete = TimeoutRecoveryTests.complete

    def verify(self, workspace, stage, jid):
        self.check_calls.append((workspace, stage, jid))
        return self.reports.pop(0)

    def failure(self):
        return {'status':'FAIL','checks':[{'label':'verification_session',
                'status':'FAIL','exit_code':1,
                'tail':"PermissionError: '/workspace/acceptance-request.json'"}]}

    def test_infrastructure_error_pauses_and_resume_rechecks_without_model(self):
        self.reports=[self.failure()]
        TimeoutRecoveryTests.run_worker(self,['PASS'])
        self.assertEqual(len(self.dispatched),1)
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase'],row['round']),
                         ('USER_PAUSED','verification_error',1))
        self.assertIn('PermissionError',row['last_error'])
        self.assertTrue((self.stage/'verification_complete.json').is_file())
        self.assertTrue((self.artifacts/self.pid/self.jid/'round-memory.json').is_file())
        self.reports=[{'status':'INCOMPLETE','checks':[{'label':'gradle_build','status':'PASS'}]}]
        TimeoutRecoveryTests.resume(self)
        TimeoutRecoveryTests.run_worker(self,[])
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(len(self.check_calls),2)
        self.assertEqual(db.job_row(self.jid)['status'],'FINISHED')
        backups=list((self.stage/'verification-retries').glob('*/verification_complete.json'))
        self.assertEqual(len(backups),1)
        archived=json.loads(backups[0].read_text())
        self.assertEqual({k:archived[k] for k in self.failure()},self.failure())
        self.assertIn('progress_guard',archived)

    def test_failed_verification_retry_stays_paused_without_paid_request(self):
        self.reports=[self.failure()]
        TimeoutRecoveryTests.run_worker(self,['PASS'])
        TimeoutRecoveryTests.resume(self)
        self.reports=[self.failure()]
        TimeoutRecoveryTests.run_worker(self,[])
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(len(self.check_calls),2)
        self.assertEqual(db.job_row(self.jid)['status'],'USER_PAUSED')

    def test_mod_compile_failure_does_not_count_as_infrastructure_failure(self):
        for label in ('gradle_build','minecraft_client_gametest','production_jar'):
            self.assertIsNone(engine.infrastructure_failure({'checks':[{'label':label,'status':'FAIL'}]}))
        self.assertIsNotNone(engine.infrastructure_failure({'checks':[
            {'label':'verification_report','status':'ERROR'}]}))
        for report in ({'checks':None},{'checks':[None]},[]):
            self.assertEqual(engine.infrastructure_failure(report)['label'],'verification_report')

    def test_real_sandbox_nonzero_exit_for_compile_failure_remains_fixable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            report={'status':'FAIL','checks':[{'label':'gradle_build','status':'FAIL','exit_code':1}]}
            def execute(*args,**kwargs):
                (root/'verification.json').write_text(json.dumps(report),encoding='utf-8')
                return {'status':'FAIL','exit_code':1,'tail':'build failed'}
            with patch.object(DockerSandbox,'_execute',side_effect=execute):
                actual=DockerSandbox().verify(root,root,60,self.jid,lambda:None)
            self.assertTrue(any(c['label']=='verification_session' for c in actual['checks']))
            self.assertIsNone(engine.infrastructure_failure(actual))
            actual['checks'][-1].update(status='TIMEOUT',exit_code=None)
            self.assertIsNotNone(engine.infrastructure_failure(actual))

    def test_recover_existing_executor_without_waiting_on_its_workspace_lock(self):
        tid=self.jid+'-round01-'+'f'*32
        self.stage.mkdir(parents=True)
        engine.write_json(self.stage/'agent_attempt.json',{'id':tid})
        engine.write_json(self.stage/'timeout_recovery.json',{
            'generation':0,'attempts_started':1,'in_flight':True})
        for name in ('requests','running','results','logs','cancel'):
            (self.mailbox/name).mkdir(parents=True,exist_ok=True)
        engine.write_json(self.mailbox/'running'/(tid+'.json'),{
            'id':tid,'project_id':self.pid,'job_id':self.jid,'prompt':'original bounded request'})
        engine.write_json(self.mailbox/'results'/(tid+'.json'),{
            'id':tid,'status':'PASS','duration_seconds':1})
        (self.mailbox/'logs'/(tid+'.jsonl')).write_text('{}\n')
        self.reports=[{'status':'INCOMPLETE','checks':[]}]
        recovered=Event()
        def verified(*args):
            recovered.set()
            return self.verify(*args)
        thread=Thread(target=engine.replay_legacy,args=(self.jid,),daemon=True)
        with patch.object(engine,'checks',side_effect=verified):
            with project_lock(self.workspace.parent):
                thread.start()
                reached_verification=recovered.wait(timeout=3)
            thread.join(timeout=3)
        self.assertTrue(reached_verification,'Transport recovery must not wait on the surviving executor lock')
        self.assertFalse(thread.is_alive())
        self.assertEqual(db.job_row(self.jid)['status'],'FINISHED')
        self.assertEqual(self.dispatched,[])


if __name__=='__main__':
    unittest.main()
