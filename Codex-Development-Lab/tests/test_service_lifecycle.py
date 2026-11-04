"""Result handoff and lossless pause during service work; no provider calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import pipeline_memory as memory
from worker import engine, pipeline
from test_pipeline_cycle import FakeEngine


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.project=self.root/'project';self.project.mkdir()
        self.ws=self.project/'workspace';self.ws.mkdir();self.out=self.root/'artifacts';self.out.mkdir()
        (self.project/'ТЗ.md').write_text('Plant a sapling.\nGrow a tree.\n',encoding='utf-8')
        self.job={'id':'1'*14,'project_id':'2'*14,'round':0,'max_rounds':1,'provider':'codex','model':'',
            'reasoning_effort':'default','role_models':'{}','dslab_token_budget':100000,
            'dslab_max_calls':100,'dslab_max_output_tokens':8000}
        self.engine=FakeEngine(self.ws)

    def cycle(self):
        with patch.object(pipeline.Cycle,'_usage',return_value={}):
            return pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)

    def test_initial_memory_pause_is_handled_without_starting_models(self):
        self.engine.status='USER_PAUSED'
        pipeline.run(self.engine,self.job,self.ws,self.out,{})
        self.assertEqual(self.engine.requests,[])
        self.assertEqual(self.engine.updates[-1]['status'],'USER_PAUSED')
        state=pipeline.read_json(self.out,'pipeline/state.json')
        self.assertFalse(state['service_operation']['active'])

    def test_committed_event_pointer_survives_mirror_interruption(self):
        cycle=self.cycle()
        with patch('common.pipeline_memory._export_batch',side_effect=pipeline.Interrupted('USER_PAUSED')):
            with self.assertRaises(pipeline.Interrupted):
                cycle.event('plan',{'contract':'PlannerDecision','summary':'Synthetic plan event'},'planner')
        state=pipeline.read_json(self.out,'pipeline/state.json')
        rid=state['last_plan_record']
        self.assertEqual(memory.read_payload(self.out,rid)['summary'],'Synthetic plan event')
        self.assertEqual(state['events'].count(rid),1)

    def test_correction_lookup_reuses_verified_immutable_payloads_until_new_entries(self):
        from common import correction_journal as journal
        cycle=self.cycle()
        value=journal.record('ReviewResult',{},[{'path':'$.contract'}])
        cycle.store_correction(value,'fixture-phase')
        cycle._correction_records_cache={}
        with patch.object(memory,'read_payload',wraps=memory.read_payload) as read:
            first=cycle.correction_records('ReviewResult')
            again=cycle.correction_records('ReviewResult')
            self.assertEqual(first,again)
            self.assertEqual(read.call_count,1)
            again[0][1]['correction']['outcome']='caller-mutated'
            self.assertNotEqual(cycle.correction_records('ReviewResult')[0][1]['correction']['outcome'],'caller-mutated')
            cycle.event('usage',{'summary':'Unrelated metadata'},'controller')
            self.assertEqual(cycle.correction_records('ReviewResult'),first)
            self.assertEqual(read.call_count,1)
            cycle.scope={**cycle.scope,'spec_revision':'f'*64}
            self.assertEqual(cycle.correction_records('ReviewResult'),[])

    def test_cached_accepted_checkpoint_clears_stale_processing_state(self):
        cycle=self.cycle()
        result=cycle.ask('RequirementsCatalog','planner',{},'checkpoint-fixture')
        cycle.state['operation']={'status':'processing_result','role':'planner'}
        cycle.save()
        with patch.object(self.engine,'request_agent',side_effect=AssertionError('Unexpected model call')):
            restored=cycle.ask('RequirementsCatalog','planner',{},'checkpoint-fixture')
        self.assertEqual(restored,result)
        self.assertEqual(cycle.state['operation']['status'],'completed')
        self.assertTrue(cycle.state['operation']['reused_checkpoint'])

    def test_executor_result_callback_precedes_archive_even_on_recovery(self):
        for callback_raises,archive_raises in ((False,False),(True,False),(True,True)):
            with self.subTest(callback_raises=callback_raises,archive_raises=archive_raises),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);mailbox=root/'mailbox'
                for name in ('requests','running','results','logs','cancel'):(mailbox/name).mkdir(parents=True)
                tid=self.job['id']+'-round01-'+'a'*32
                stage=root/'round-01';stage.mkdir()
                (stage/'agent_attempt.json').write_text(json.dumps({'id':tid,'role':'coder'}))
                (mailbox/'results'/(tid+'.json')).write_text(json.dumps({'id':tid,'status':'FAIL','error':None}))
                (mailbox/'logs'/(tid+'.jsonl')).write_text(json.dumps({'type':'turn.failed','error':{'code':'stream_interrupted'}})+'\n')
                events=[]
                def received(result):
                    events.append('received')
                    if callback_raises:raise RuntimeError('Synthetic status write failure')
                def archive(*a,**kw):
                    events.append('archive')
                    if archive_raises:raise OSError('Synthetic archive error')
                with patch.dict(os.environ,{'AGENT_TASKS':str(mailbox)}),patch.object(engine.db,'job_row',return_value=self.job), \
                     patch.object(engine,'archive_attempt',side_effect=archive),patch.object(engine,'revoke_provider_task') as revoke:
                    if callback_raises:
                        with self.assertRaisesRegex(RuntimeError,'Synthetic status'):
                            engine.request_agent(self.job['id'],self.job['project_id'],1,'Synthetic request',stage/'codex.jsonl',on_result=received)
                    else:
                        result=engine.request_agent(self.job['id'],self.job['project_id'],1,'Synthetic request',stage/'codex.jsonl',on_result=received)
                        self.assertEqual(result['status'],'FAIL')
                    self.assertEqual(events,['received','archive'])
                    revoke.assert_called_once()


if __name__=='__main__':unittest.main()
