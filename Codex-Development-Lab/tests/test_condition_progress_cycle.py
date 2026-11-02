"""Actual cycle wiring, without additional agents or paid model requests."""
from copy import deepcopy
import json
import unittest

from common import acceptance
from worker import pipeline
from tests import test_pipeline_cycle as fixture


class ConditionProgressCycleTests(unittest.TestCase):
    setUp=fixture.CycleTest.setUp
    verified=fixture.CycleTest.verified

    def prepare(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();decision,graph=cycle.plan(catalog,graph,{})
        _,manifest,_=cycle.code_and_review(catalog,graph,decision)
        return cycle,catalog,graph,decision,manifest

    def fail_twice(self):
        cycle,catalog,graph,decision,manifest=self.prepare()
        for n in (1,2):
            if n==2:
                (self.ws/'existing.java').write_text('changed unrelated source')
                manifest=deepcopy(manifest)
                manifest['scope']['source_revision']=acceptance.source_fingerprint(self.ws)
            result,legacy=self.verified(self.ws,cycle.stage,self.job['id'],catalog,manifest,decision)
            result['scenario_results'][0]['status']='FAIL'
            result['scenario_results'][0]['checkpoints'][1].update(status='FAIL',actual='wording '+str(n))
            cycle.record_verification(catalog,manifest,decision,result,legacy)
        return cycle,catalog,graph,decision,manifest,result

    def test_partial_stall_requests_reflection_and_reaches_next_planner(self):
        cycle,catalog,graph,_,manifest,previous=self.fail_twice()
        self.assertGreater(cycle.state['repeated_observation'],0)
        self.assertIn('C1',cycle.state['reflection']['condition_ids'])
        self.assertFalse(any(x.get('status')=='USER_PAUSED' for x in self.engine.updates))
        next_cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,2)
        next_cycle.plan(catalog,graph,previous)
        req=next(c for name,role,c in reversed(self.engine.requests) if name=='PlannerDecision')
        self.assertTrue(req['input']['reflection_requested'])
        summary=json.loads(pipeline.source_bytes(self.ws,req['input']['condition_progress_ref']))
        self.assertIn('C1',summary['selection_advice']['repeated_condition_ids'])
        p=next(c for c in summary['conditions'] if c['condition_id']=='C1')
        history=json.loads(pipeline.source_bytes(self.ws,p['history_ref']))
        checkpoint_id=manifest['scenarios'][0]['checkpoints'][1]['id']
        self.assertEqual(len([r for r in history['history'] if r['checkpoint_id']==checkpoint_id]),2)
        cp=next(cp for cp in p['checkpoints'] if cp['checkpoint_id']==checkpoint_id)
        trace=json.loads(pipeline.source_bytes(self.ws,cp['history_ref']))
        self.assertEqual(len(trace['history']),2)
        self.assertTrue(all(r['checkpoint_id']==checkpoint_id for r in trace['history']))
        self.assertTrue(all(r['plan_ref'] and r['coder_ref'] for r in history['history']))

    def test_coder_and_reviewer_receive_full_history_links_and_meaning_instruction(self):
        cycle,catalog,graph,decision,_=self.prepare()
        coder=next(c for name,role,c in self.engine.requests if name=='CoderReport')
        ref=coder['input']['assignment']['condition_progress_ref']
        self.assertIn('conditions',json.loads(pipeline.source_bytes(self.ws,ref)))
        reviewer=next(c for name,role,c in reversed(self.engine.requests) if name=='ReviewResult')
        context=json.loads(pipeline.source_bytes(self.ws,reviewer['input']['context_ref']))
        self.assertIn('condition_progress_ref',context)
        self.assertIn('required action',context['test_meaning_review'])
        self.assertIn('weakening',context['test_meaning_review'])

    def test_new_exports_do_not_destroy_old_history_snapshots(self):
        cycle,catalog,graph,_,_,_=self.fail_twice()
        first_ref=cycle.state['condition_progress']['index_ref']
        first=json.loads(pipeline.source_bytes(self.ws,first_ref))
        history_ref=first['conditions'][0]['history_ref']
        original=pipeline.source_bytes(self.ws,history_ref)
        cycle.state['return_agenda']=[{'task_id':'T1','reason':'Perform independent experiment',
                                      'return_when':'The new observation is available'}]
        _,second_ref=cycle.refresh_condition_progress(catalog,graph)
        self.assertNotEqual(first_ref['path'],second_ref['path'])
        self.assertEqual(pipeline.source_bytes(self.ws,history_ref),original)
        self.assertEqual(json.loads(pipeline.source_bytes(self.ws,first_ref)),first)

    def test_navigation_growth_does_not_invalidate_an_accepted_phase(self):
        scope={'source_revision':'a'*64}
        first={'scope':scope,'condition_progress_ref':{'path':'old.json','sha256':'1'*64}}
        second={'scope':scope,'condition_progress_ref':{'path':'new.json','sha256':'2'*64}}
        self.assertEqual(pipeline.input_binding(first),pipeline.input_binding(second))
        second['scope']={'source_revision':'b'*64}
        self.assertNotEqual(pipeline.input_binding(first),pipeline.input_binding(second))

    def test_unresolved_return_step_survives_next_round_without_forcing_assignment(self):
        cycle,catalog,graph,_,_,previous=self.fail_twice()
        note={'task_id':'T1','reason':'Run a different causal experiment',
              'return_when':'An independent measurement distinguishes the causes'}
        cycle.state['return_agenda']=[note];cycle.save()
        resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,2)
        decision,_=resumed.plan(catalog,graph,previous)
        self.assertIn(note,resumed.state['return_agenda'])
        self.assertEqual(decision['actions'][0]['task_ids'],['T1','T2'])
        self.assertFalse(any(x.get('status')=='USER_PAUSED' for x in self.engine.updates))

    def test_old_assignment_gets_history_without_repeating_completed_coder(self):
        cycle,catalog,graph,decision,_=self.prepare()
        saved=pipeline.read_json(cycle.stage,'pipeline/coding-state.json')
        saved['assignment'].pop('condition_progress_ref',None)
        pipeline.write_json(cycle.stage,'pipeline/coding-state.json',saved)
        before=sum(n=='CoderReport' for n,_,_ in self.engine.requests)
        resumed=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        resumed.code_and_review(catalog,graph,decision)
        self.assertEqual(sum(n=='CoderReport' for n,_,_ in self.engine.requests),before)
        saved=pipeline.read_json(cycle.stage,'pipeline/coding-state.json')
        self.assertIn('condition_progress_ref',saved['assignment'])


if __name__=='__main__':unittest.main()
