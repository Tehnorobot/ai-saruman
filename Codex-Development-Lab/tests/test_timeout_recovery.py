"""Independent timeout/recovery regressions with real mailbox/history persistence.

No model, game, Engine, or network calls. The transport writes executor results
to temporary files; worker runner, DB transitions and archival are actual code.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import db
from worker import engine


class SimulatedWorkerCrash(BaseException):
    pass


class TimeoutRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects, self.artifacts = self.root / 'projects', self.root / 'artifacts'
        self.mailbox = self.root / 'mailbox'
        self.dispatched = []
        self.outcomes = []
        self.before_complete = None
        self.check_calls = []
        self.backoff_waits = 0
        self.pause_during_backoff = False
        for context in (
            patch.object(db, 'DB', self.root / 'lab.sqlite'),
            patch.object(engine, 'WORKSPACE_ROOT', self.projects),
            patch.object(engine, 'ARTIFACTS', self.artifacts),
            patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects),
                                    'AGENT_TASKS': str(self.mailbox),
                                    'BACKUP_REQUESTS': str(self.root / 'backup-requests')}),
            patch.object(engine, 'copy_skill_environment', lambda project: project / 'workspace'),
            patch.object(engine.DockerSandbox, 'recover', return_value=None),
            patch.object(engine, 'checks', side_effect=self.verify),
            patch.object(engine,'plan_round',side_effect=lambda jid,pid,n,ws,previous,stage,context:({'status':'PASS'},context)),
            patch.object(engine.time, 'sleep', side_effect=self.complete),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.pid, self.jid = db.create_project('Timeout recovery', 'spec.md',
                                              b'# Continue existing source without losing work', 1)
        self.workspace = self.projects / self.pid / 'workspace'
        self.sentinel = self.workspace / 'partially-implemented.txt'
        self.sentinel.write_text('implementation from timed-out attempt', encoding='utf-8')
        self.stage = self.artifacts / self.pid / self.jid / 'round-01'
        db.claim('timeout-verifier-first')

    def verify(self, workspace, stage, jid):
        self.check_calls.append((workspace, stage, jid))
        return {'status': 'INCOMPLETE', 'checks': [{'status': 'NOT_CONFIGURED'}]}

    def complete(self, _delay):
        pending = sorted((self.mailbox / 'requests').glob('*.json'))
        if not pending:
            self.backoff_waits += 1
            if self.pause_during_backoff:
                db.control_job(self.jid,'pause')
            return
        self.assertEqual(len(pending), 1)
        request = pending[0]
        task = json.loads(request.read_text(encoding='utf-8'))
        self.dispatched.append(task)
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'implementation from timed-out attempt')
        if self.before_complete:
            self.before_complete(task)
        if not self.outcomes:
            self.fail('Unexpected model dispatch without a supplied executor outcome')
        status = self.outcomes.pop(0)
        running = self.mailbox / 'running' / request.name
        request.rename(running)
        text = ('You have reached your usage limit' if status == 'QUOTA'
                else 'partial progress with source preserved' if status == 'TIMEOUT'
                else 'finished this model turn')
        log = self.mailbox / 'logs' / (task['id'] + '.jsonl')
        event = ({'type': 'turn.failed', 'error': {'code': 'usage_limit', 'message': text}}
                 if status == 'QUOTA' else {'type': 'item.completed', 'item': {
                     'type': 'agent_message', 'text': text}})
        log.write_text(json.dumps(event) + '\n', encoding='utf-8')
        engine.write_json(self.mailbox / 'results' / request.name, {
            'id': task['id'], 'status': 'FAIL' if status == 'QUOTA' else status,
            'duration_seconds': 1500.46 if status == 'TIMEOUT' else 1,
            'error': 'Execution time limit reached' if status == 'TIMEOUT' else None})
        running.unlink()

    def run_worker(self, outcomes):
        self.outcomes = list(outcomes)
        engine.replay_legacy(self.jid)

    def archived(self):
        folders = list((self.stage / 'agent-history').glob('*'))
        return {folder.name: folder for folder in folders if folder.is_dir()}

    def resume(self):
        self.assertEqual(db.control_job(self.jid, 'resume')['status'], 'QUEUED')
        self.assertEqual(db.claim('timeout-verifier-replacement')['id'], self.jid)

    def test_timeout_retries_same_round_with_actual_sources_and_original_attempt_evidence(self):
        self.run_worker(['TIMEOUT', 'PASS'])
        self.assertEqual(len(self.dispatched), 2)
        self.assertEqual(db.job_row(self.jid)['status'], 'FINISHED')
        self.assertEqual(len(self.check_calls), 1)
        self.assertEqual(self.check_calls[0][0], self.workspace)
        self.assertNotEqual(self.dispatched[0]['id'], self.dispatched[1]['id'])
        self.assertIn('TIMEOUT', self.dispatched[1]['prompt'].upper())
        self.assertNotEqual(self.dispatched[0]['prompt'], self.dispatched[1]['prompt'])
        archived = self.archived()
        self.assertEqual(set(archived), {task['id'] for task in self.dispatched})
        for task, status in zip(self.dispatched, ('TIMEOUT', 'PASS')):
            folder = archived[task['id']]
            saved = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['prompt'], task['prompt'])
            self.assertEqual(json.loads((folder / 'result.json').read_text())['status'], status)
            self.assertTrue((folder / 'events.jsonl').is_file())
        self.assertEqual(self.sentinel.read_text(), 'implementation from timed-out attempt')

    def test_repeated_timeouts_continue_instead_of_legacy_fixed_attempt_pause(self):
        self.run_worker(['TIMEOUT', 'TIMEOUT', 'TIMEOUT', 'PASS'])
        row = db.job_row(self.jid)
        self.assertEqual(len(self.dispatched), 4)
        self.assertEqual(row['status'], 'FINISHED')
        self.assertEqual(row['round'], 1)
        self.assertIsNone(row['worker'])
        self.assertEqual(len(self.check_calls), 1)
        state=json.loads((self.stage/'timeout_recovery.json').read_text())
        self.assertEqual(state['attempts_started'],4)
        self.assertEqual(state['reflection']['attempts_started'],3)
        self.assertEqual(len(self.archived()), 4)
        self.assertGreater(self.backoff_waits,0)
        self.assertIn('Reflect on the interrupted attempt',self.dispatched[-1]['prompt'])

    def test_timeout_archive_preserves_structured_cause_when_user_pauses_recovery(self):
        self.pause_during_backoff=True
        self.run_worker(['TIMEOUT'])
        self.assertEqual(db.job_row(self.jid)['status'],'USER_PAUSED')
        result = json.loads((next(iter(self.archived().values())) / 'result.json').read_text())
        self.assertEqual(result['status'], 'TIMEOUT')
        self.assertEqual(result['duration_seconds'], 1500.46)
        self.assertIn('Execution time limit reached',result['error'])

    def test_user_pause_interrupts_recovery_and_resume_keeps_previous_history(self):
        self.pause_during_backoff=True
        self.run_worker(['TIMEOUT'])
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(self.check_calls,[])
        old_ids = set(self.archived())
        self.resume()
        self.pause_during_backoff=False
        self.run_worker(['TIMEOUT', 'PASS'])
        self.assertEqual(db.job_row(self.jid)['status'], 'FINISHED')
        self.assertEqual(len(self.dispatched), 3)
        self.assertEqual(len(self.archived()), 3)
        self.assertTrue(old_ids.issubset(self.archived()))
        self.assertEqual(len(self.check_calls), 1)

    def test_worker_restart_recovers_in_flight_timeout_and_continues(self):
        def crash_before_second_result(_task):
            if len(self.dispatched) == 2:
                self.assertTrue((self.stage / 'timeout_recovery.json').is_file())
                raise SimulatedWorkerCrash()
        self.before_complete = crash_before_second_result
        with self.assertRaises(SimulatedWorkerCrash):
            self.run_worker(['TIMEOUT', 'TIMEOUT'])
        self.assertEqual(db.job_row(self.jid)['status'], 'RUNNING')
        self.before_complete = None
        db.update(self.jid, lease_until=0)
        self.assertEqual(db.claim('recovered-controller')['id'], self.jid)
        # Recover the second identity, then continue to a new third attempt.
        self.run_worker(['TIMEOUT','PASS'])
        row = db.job_row(self.jid)
        self.assertEqual(row['status'], 'FINISHED')
        self.assertEqual(len({task['id'] for task in self.dispatched}), 3)
        self.assertEqual(self.dispatched[1]['id'],self.dispatched[2]['id'])
        self.assertEqual(len(self.archived()), 3)
        self.assertEqual(len(self.check_calls), 1)

    def test_crash_after_completed_timeout_preserves_result_before_continuation(self):
        original = engine.request_agent
        def crash_after_completed_timeout(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result['status'], 'TIMEOUT')
            self.assertEqual(len(self.archived()), 1)
            raise SimulatedWorkerCrash()
        with patch.object(engine, 'request_agent', side_effect=crash_after_completed_timeout):
            with self.assertRaises(SimulatedWorkerCrash):
                self.run_worker(['TIMEOUT'])
        db.update(self.jid, lease_until=0)
        self.assertEqual(db.claim('checkpoint-gap-recovery')['id'], self.jid)
        self.run_worker(['TIMEOUT','PASS'])
        row = db.job_row(self.jid)
        self.assertEqual(row['status'], 'FINISHED')
        self.assertEqual(len({task['id'] for task in self.dispatched}), 3)
        self.assertEqual(len(self.archived()), 3)
        self.assertEqual(len(self.check_calls), 1)

    def test_crash_after_success_before_development_checkpoint_does_not_repeat_model(self):
        original = engine.request_agent
        def crash_after_completed_success(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result['status'], 'PASS')
            raise SimulatedWorkerCrash()
        with patch.object(engine, 'request_agent', side_effect=crash_after_completed_success):
            with self.assertRaises(SimulatedWorkerCrash):
                self.run_worker(['PASS'])
        self.assertFalse((self.stage / 'development_complete.json').exists())
        db.update(self.jid, lease_until=0)
        self.assertEqual(db.claim('success-gap-recovery')['id'], self.jid)
        self.run_worker([])
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(len(self.archived()), 1)
        self.assertEqual(len(self.check_calls), 1)
        self.assertEqual(db.job_row(self.jid)['status'], 'FINISHED')

    def test_completed_failure_survives_checkpoint_gap_without_extra_model_call(self):
        original = engine.request_agent
        for status in ('FAIL', 'ERROR'):
            with self.subTest(status=status):
                if status == 'ERROR':
                    self.resume()
                def crash_after_result(*args, **kwargs):
                    result = original(*args, **kwargs)
                    self.assertEqual(result['status'], status)
                    raise SimulatedWorkerCrash()
                previous_dispatches = len(self.dispatched)
                with patch.object(engine, 'request_agent', side_effect=crash_after_result):
                    with self.assertRaises(SimulatedWorkerCrash):
                        self.run_worker([status])
                db.update(self.jid, lease_until=0)
                self.assertEqual(db.claim('failure-gap-controller')['id'], self.jid)
                self.run_worker([])
                self.assertEqual(len(self.dispatched), previous_dispatches + 1)
                self.assertEqual(db.job_row(self.jid)['status'], 'FAILED')
                self.assertEqual(self.check_calls, [])

    def test_completed_failure_is_not_lost_if_worker_crashes_before_checkpoint(self):
        original = engine.request_agent
        def crash_after_failure(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result['status'], 'FAIL')
            raise SimulatedWorkerCrash()
        with patch.object(engine, 'request_agent', side_effect=crash_after_failure):
            with self.assertRaises(SimulatedWorkerCrash):
                self.run_worker(['FAIL'])
        db.update(self.jid, lease_until=0)
        self.assertEqual(db.claim('failure-gap-recovery')['id'], self.jid)
        self.run_worker([])
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(db.job_row(self.jid)['status'], 'FAILED')
        self.assertEqual(self.check_calls, [])

    def test_old_failed_job_can_resume_without_new_project_or_lost_source(self):
        db.update(self.jid, status='FAILED', phase='agent_error', worker=None,
                  lease_until=0, last_error='Old unstructured timeout log')
        before = self.workspace
        self.resume()
        self.run_worker(['PASS'])
        self.assertEqual(db.job_row(self.jid)['project_id'], self.pid)
        self.assertEqual(self.workspace, before)
        self.assertEqual(self.sentinel.read_text(), 'implementation from timed-out attempt')
        self.assertEqual(db.job_row(self.jid)['status'], 'FINISHED')

    def test_api_timeout_continuations_share_original_round_allowance(self):
        self.stage.mkdir(parents=True,exist_ok=True)
        with db.connect() as connection:
            connection.execute('UPDATE jobs SET dslab_token_budget=2000,dslab_max_calls=2 WHERE id=?',(self.jid,))
        usage=dict.fromkeys(('requests','input_tokens','output_tokens','cache_creation_input_tokens',
                            'cache_read_input_tokens','billing_unknown_requests'),0)
        def used(*args,**kwargs):return {'totals':dict(usage)}
        def timeout(*args,**kwargs):
            usage['requests']+=1;usage['input_tokens']+=1100
            return {'status':'TIMEOUT','duration_seconds':1,'tail':'partial'}
        with patch('common.provider_settings.resolve_role',return_value={'provider':'minimax'}), \
                patch.object(engine,'provider_control',side_effect=used), \
                patch.object(engine,'request_agent',side_effect=timeout) as request:
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        self.assertEqual(result['status'],'ERROR')
        self.assertIn('cumulative',result['tail'])
        self.assertEqual([c.kwargs['allowance'] for c in request.call_args_list],
                         [{'tokens':2000,'calls':2},{'tokens':900,'calls':1}])
        self.assertEqual(json.loads((self.stage/'timeout_recovery.json').read_text())['usage_before']['requests'],0)

    def test_in_flight_transport_is_recovered_before_usage_lookup(self):
        self.stage.mkdir(parents=True,exist_ok=True)
        taskid=self.jid+'-round01-'+'a'*32
        engine.write_json(self.stage/'agent_attempt.json',{'id':taskid})
        engine.write_json(self.stage/'timeout_recovery.json',{'generation':0,'attempts_started':1,
            'in_flight':True,'usage_before':None})
        with patch('common.provider_settings.resolve_role',return_value={'provider':'minimax'}), \
                patch.object(engine,'provider_control',side_effect=engine.GatewayError('offline')) as control, \
                patch.object(engine,'request_agent',return_value={'status':'PASS'}) as request:
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        self.assertEqual(result['status'],'PASS')
        control.assert_not_called()
        self.assertNotIn('allowance',request.call_args.kwargs)

    def test_automatic_quota_resume_does_not_replay_cached_failure_forever(self):
        self.run_worker(['QUOTA'])
        self.assertEqual(db.job_row(self.jid)['status'], 'QUOTA_PAUSED')
        first_generation = db.job_row(self.jid)['resume_generation']
        db.update(self.jid, resume_at=0)
        self.assertEqual(db.claim('quota-resume-controller')['id'], self.jid)
        self.assertGreater(db.job_row(self.jid)['resume_generation'], first_generation)
        self.run_worker(['PASS'])
        self.assertEqual(db.job_row(self.jid)['status'], 'FINISHED')
        self.assertEqual(len(self.dispatched), 2)
        self.assertEqual(len(self.archived()), 2)
        self.assertEqual(len(self.check_calls), 1)

    def test_resume_http_accepts_failed_but_rejects_stopped_finished_and_running(self):
        from fastapi.testclient import TestClient
        from app import main as dashboard
        client = TestClient(dashboard.app)
        for status, expected in (('FAILED', 200), ('STOPPED', 409), ('FINISHED', 409), ('RUNNING', 409)):
            with self.subTest(status=status):
                db.update(self.jid, status=status, worker=None, lease_until=0,
                          cancel_requested=0, pause_requested=0)
                response = client.post('/api/jobs/' + self.jid + '/resume')
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual(db.job_row(self.jid)['status'], 'QUEUED' if expected == 200 else status)


class TimeoutResumeUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required for actual dashboard behavior')
    def test_failed_job_exposes_enabled_resume_button(self):
        source = Path(__file__).resolve().parents[1] / 'app' / 'index.html'
        script = re.search(r'<script>(.*?)</script>', source.read_text(encoding='utf-8'), re.S).group(1)
        script = script[:script.rfind('setInterval(')]
        harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
class E{constructor(){this.children=[];this.value='';this.disabled=false}append(...x){this.children.push(...x)}replaceChildren(...x){this.children=x}get options(){return this.children}get firstChild(){return this.children[0]}}
const nodes=new Map(),job={id:'aaaaaaaaaaaaaa',title:'Old failed task',status:'FAILED',round:0,max_rounds:5};
const context=vm.createContext({console,document:{getElementById(id){if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id)},createElement(){return new E()}},
fetch:async path=>({ok:true,text:async()=>JSON.stringify(path==='/api/jobs'?[job]:path==='/api/jobs/'+job.id?job:path.endsWith('/agent-history')?{attempts:[]}:path.endsWith('/verification')?{}:[])}),FormData:class{},alert(){},window:{}});
vm.runInContext(SOURCE,context);
(async()=>{await vm.runInContext('refresh()',context);assert.equal(nodes.get('resume').disabled,false);assert.equal(nodes.get('stop').disabled,true)})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('SOURCE', json.dumps(script))
        result = subprocess.run([shutil.which('node'), '-e', harness], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
