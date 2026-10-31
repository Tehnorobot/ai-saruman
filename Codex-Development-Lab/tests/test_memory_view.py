import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from common import db,memory_view
from app import main
from worker.round_context import prepare_context,save_round_snapshot


class MemoryWindowTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.artifacts=self.root/'artifacts'
        for context in (patch.object(db,'DB',self.root/'db.sqlite'),
                        patch.object(main,'ARTIFACTS',self.artifacts),
                        patch.dict(os.environ,{'LAB_PROJECTS':str(self.root/'projects')})):
            context.start();self.addCleanup(context.stop)
        self.pid,self.jid=db.create_project('Memory test','spec.md',b'# Requirements\nA sufficiently long requirement.')
        self.jobroot=self.artifacts/self.pid/self.jid;self.stage=self.jobroot/'round-01';self.stage.mkdir(parents=True)
        self.ws=self.root/'projects'/self.pid/'workspace'
        self.client=TestClient(main.app);self.url='/api/jobs/'+self.jid+'/memory'

    def write(self,path,value):path.write_text(json.dumps(value),encoding='utf-8')

    def test_legacy_reports_do_not_pretend_to_have_historical_memory(self):
        self.write(self.stage/'verification_complete.json',{'status':'FAIL','checks':[
            {'label':'gradle_build','status':'FAIL','detail':'real error','api_key':'private'}]})
        prepare_context(self.ws,self.jobroot,2,{})
        before=db.job_row(self.jid)
        overview=self.client.get(self.url).json()
        self.assertEqual(overview['current']['state'],'available')
        self.assertFalse(overview['rounds'][0]['memory_available'])
        data=self.client.get(self.url+'/rounds/1').json()
        self.assertEqual(data['memory']['state'],'missing')
        self.assertEqual(data['verification']['data']['checks'][0]['detail'],'real error')
        self.assertIn('не исторический',data['current_memory_for_round']['provenance'])
        self.assertNotIn('private',json.dumps(data))
        self.assertEqual(db.job_row(self.jid),before)

    def test_actual_start_and_end_snapshots_and_unverified_solutions(self):
        prepare_context(self.ws,self.jobroot,1,{})
        save_round_snapshot(self.jobroot,1,'before_development')
        data=self.client.get(self.url+'/rounds/1').json()
        self.assertEqual(data['memory']['data']['snapshot_phase'],'before_development')
        self.write(self.ws/'agent-handoff.json',{'summary':'implemented','api_key':'private',
             'errors_and_solutions':[{'error':'e','solution':'s','evidence':'agent claim','token':'private'}]})
        report={'status':'INCOMPLETE','checks':[{'label':'gradle_build','status':'PASS','exit_code':0}]}
        self.write(self.stage/'verification_complete.json',report)
        prepare_context(self.ws,self.jobroot,2,report)
        save_round_snapshot(self.jobroot,1,'after_verification',self.ws)
        data=self.client.get(self.url+'/rounds/1').json()
        self.assertEqual(data['memory']['data']['snapshot_phase'],'after_verification')
        self.assertEqual(data['handoff']['data']['errors_and_solutions'][0]['solution'],'s')
        self.assertNotIn('private',json.dumps(data))
        self.assertTrue(self.client.get(self.url).json()['rounds'][0]['memory_available'])

    def task_memory(self,title,number=1):
        return {'errors':[],'assignment':{'current_task_id':'task-one','status':'active',
            'current_task':{'id':'task-one','title':title,'requirement_ids':['REQ-one'],
                'unresolved_requirement_ids':['REQ-one'],'secret':'private'},'token':'private'},
            'round_action':{'kind':'repair_scenario','target':'melee','diagnostic':'Actual assertion failed',
                'requirement_ids':['REQ-one'],'secret':'private'},'round_action_round':number,
            'arbitrary_secret':'private'}

    def test_round_task_uses_start_not_end_or_current_next_assignment(self):
        self.write(self.stage/'round-memory-start.json',self.task_memory('Assigned before work'))
        self.write(self.stage/'round-memory.json',self.task_memory('Next task after verification',2))
        self.write(self.jobroot/'round-memory.json',self.task_memory('Current next task',2))
        self.write(self.stage/'verification_complete.json',{'status':'FAIL',
            'task_assignment':self.task_memory('Report fallback')['assignment']})
        detail=self.client.get(self.url+'/rounds/1').json()
        task=detail['round_task']
        self.assertEqual(task['data']['assignment']['current_task']['title'],'Assigned before work')
        self.assertEqual(task['data']['round_action_round'],1)
        self.assertTrue(task['source'].endswith('round-memory-start.json'))
        self.assertIn('перед работой',task['provenance'])
        overview=self.client.get(self.url).json()
        self.assertEqual(overview['rounds'][0]['task_title'],'Assigned before work')
        self.assertEqual(overview['rounds'][0]['action_target'],'melee')
        self.assertEqual(overview['current']['data']['task_assignment']['current_task']['title'],'Current next task')
        self.assertNotIn('private',json.dumps(detail))

    def test_round_task_report_fallback_never_uses_next_task_assignment(self):
        self.write(self.stage/'verification_complete.json',{'status':'FAIL',
            'task_assignment':self.task_memory('Actual report task')['assignment'],
            'next_task_assignment':self.task_memory('Wrong next task')['assignment']})
        task=self.client.get(self.url+'/rounds/1').json()['round_task']
        self.assertEqual(task['data']['assignment']['current_task']['title'],'Actual report task')
        self.assertIsNone(task['data']['round_action'])
        self.assertIn('отчёта',task['provenance'])

    def test_missing_historical_task_does_not_borrow_current_memory(self):
        self.write(self.jobroot/'round-memory.json',self.task_memory('Current only',8))
        self.write(self.stage/'round-memory.json',self.task_memory('Next task',2))
        detail=self.client.get(self.url+'/rounds/1').json()
        self.assertEqual(detail['round_task']['state'],'missing')
        self.assertIsNone(detail['round_task']['data'])
        self.assertEqual(self.client.get(self.url).json()['rounds'][0]['task_title'],'')

    def test_current_task_view_is_whitelisted_bounded_and_read_only(self):
        value=self.task_memory('x'*3000)
        value['assignment']['current_task']['requirement_ids']=['r'*500]*1000
        value['round_action']['diagnostics']=['d'*5000]*100
        self.write(self.jobroot/'round-memory.json',value)
        before={str(p):p.read_bytes() for p in self.jobroot.rglob('*') if p.is_file()}
        row=db.job_row(self.jid)
        with patch.object(os,'scandir',side_effect=AssertionError('Current task must not scan history')):
            current=memory_view.current_task(self.artifacts,self.pid,self.jid)
        data=current['data']
        self.assertEqual(len(data['task_assignment']['current_task']['title']),300)
        self.assertEqual(len(data['task_assignment']['current_task']['requirement_ids']),64)
        self.assertEqual(len(data['round_action']['diagnostics']),4)
        self.assertNotIn('private',json.dumps(data))
        self.client.get(self.url)
        self.client.get(self.url+'/rounds/1')
        self.assertEqual(db.job_row(self.jid),row)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.jobroot.rglob('*') if p.is_file()})

    def test_historical_action_of_other_round_is_not_relabelled(self):
        self.write(self.stage/'round-memory-start.json',self.task_memory('Original task',2))
        data=self.client.get(self.url+'/rounds/1').json()['round_task']['data']
        self.assertEqual(data['assignment']['current_task']['title'],'Original task')
        self.assertIsNone(data['round_action'])
        self.assertIsNone(data['round_action_round'])

    def test_only_public_main_summary_is_exported(self):
        events=[{'item':{'type':'agent_message','agent_id':'reviewer-1','text':'child'}},
                {'item':{'type':'reasoning','text':'private'}},
                {'item':{'type':'agent_message','text':'public final'}}]
        (self.stage/'codex.jsonl').write_text('\n'.join(json.dumps(x) for x in events))
        data=self.client.get(self.url+'/rounds/1').json()
        self.assertEqual(data['summary']['text'],'public final')
        self.assertNotIn('private',json.dumps(data))

    def test_missing_corrupt_and_oversized_documents_explicit(self):
        self.assertEqual(self.client.get(self.url).json()['current']['state'],'missing')
        (self.jobroot/'round-memory.json').write_text('{bad')
        self.assertEqual(self.client.get(self.url).json()['current']['state'],'invalid')
        (self.jobroot/'round-memory.json').write_bytes(b'x'*(memory_view.LIMIT+1))
        self.assertEqual(self.client.get(self.url).json()['current']['state'],'too_large')

    def test_route_validation_and_fixed_static_assets(self):
        self.assertEqual(self.client.get('/api/jobs/'+self.jid+'/round-task').json()['task']['state'],'missing')
        self.write(self.jobroot/'round-memory.json',self.task_memory('Assigned',6))
        before=db.job_row(self.jid)
        task=self.client.get('/api/jobs/'+self.jid+'/round-task').json()
        self.assertEqual(task['task']['data']['round_action_round'],6)
        self.assertEqual(task['task']['data']['task_assignment']['current_task']['title'],'Assigned')
        self.assertEqual(db.job_row(self.jid),before)
        self.assertEqual(self.client.get('/api/jobs/missing/round-task').status_code,404)
        self.assertEqual(self.client.get(self.url+'/rounds/0').status_code,400)
        self.assertEqual(self.client.get(self.url+'/rounds/2').status_code,404)
        self.assertEqual(self.client.get('/api/jobs/missing/memory').status_code,404)
        for path in ('/memory.js','/memory.css'):
            self.assertEqual(self.client.get(path).status_code,200)
        with self.assertRaises(ValueError):memory_view.overview(self.artifacts,'../data',self.jid)

    def test_round_memory_link_cannot_read_external_document(self):
        outside=self.root/'outside.json';outside.write_text('{"errors":[],"secret":"do not read"}')
        try:(self.stage/'round-memory.json').symlink_to(outside)
        except OSError:self.skipTest('Symlink privilege unavailable')
        data=self.client.get(self.url+'/rounds/1').json()
        self.assertEqual(data['memory']['state'],'unavailable')
        self.assertNotIn('do not read',json.dumps(data))


if __name__=='__main__':unittest.main()
