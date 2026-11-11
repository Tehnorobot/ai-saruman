"""Cross-role facts, claims and recovery without live models or game execution."""
from copy import deepcopy
from contextlib import redirect_stdout,redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import acceptance,agent_context,agent_protocol,role_context,task_graph
from common.pipeline_contracts import load_example
from worker import pipeline
from tests import test_pipeline_cycle as cycle_fixture


class RoleContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_real_diff_excludes_service_archive_and_does_not_claim_pass(self):
        (self.root/'main.py').write_text('print(1)\n')
        before=role_context.capture_sources(self.root)
        (self.root/'main.py').write_text('print(2)\n')
        (self.root/'.lab-evidence').mkdir();(self.root/'.lab-evidence/data').write_text('private diagnostic')
        after=role_context.capture_sources(self.root)
        delta=role_context.source_diff(before,after)
        self.assertEqual(delta['changed_paths'],['main.py'])
        self.assertIn('-print(1)',delta['text']);self.assertIn('+print(2)',delta['text'])
        self.assertNotIn('private diagnostic',delta['text'])
        revision=acceptance.source_fingerprint(self.root)
        (self.root/'.lab-evidence/another').write_text('more')
        self.assertEqual(acceptance.source_fingerprint(self.root),revision)

    def test_small_context_keeps_exact_primary_fact_and_links_full_review(self):
        inputs={'assignment':{'instructions':['long canonical text'],'plan_ref':{'path':'plan.json'}},
                'review':{'findings':[{'id':'F1','kind':'technical_risk','observation':'detail'*10000}]}}
        original=deepcopy(inputs);ref={'path':'full.json','sha256':'a'*64,'media_type':'application/json'}
        small=role_context.model_input(inputs,full_input_ref=ref)
        self.assertLess(len(json.dumps(small)),1000)
        self.assertEqual(small['review']['findings'][0]['id'],'F1');self.assertEqual(inputs,original)
        result={'scope':{'source_revision':'old'},'build_status':'FAIL',
            'execution_diagnostics':[{'diagnostic_id':'D1','stage':'build_test','actual':'exact assertion','evidence_refs':[ref]}],
            'scenario_results':[{'scenario_id':'game','attempted':False,'stopped_by':['D1']}]}
        view=role_context.build('planner','plan',{'source_revision':'new'},inputs,full_input_ref=ref,latest_result=result)
        self.assertEqual(view['facts'][0]['preview'],'exact assertion')
        self.assertEqual(view['not_attempted'][0]['stopped_by'],['D1'])
        self.assertEqual(view['previous_observation_scope']['source_revision'],'old')

    def test_present_unverified_dependency_does_not_expand_narrow_repair(self):
        catalog=load_example('RequirementsCatalog');graph=load_example('TaskGraph');decision=load_example('PlannerDecision')
        decision['deferred']=[]
        action=decision['actions'][0];action.update(kind='repair',task_ids=['T2'],condition_ids=['C2'])
        states=[{'task_id':'T1','status':'needs_retest','implementation':'present_claim'},
                {'task_id':'T2','status':'pending','implementation':'present_claim'}]
        self.assertIs(task_graph.validate_decision(decision,graph,catalog,states),decision)
        states[0]['implementation']='absent'
        with self.assertRaisesRegex(ValueError,'unsatisfied_dependency'):task_graph.validate_decision(decision,graph,catalog,states)

    def test_product_failure_is_visible_even_when_build_has_no_failed_check(self):
        result={'scope':{},'build_status':'PASS','execution_diagnostics':[],
            'scenario_results':[{'scenario_id':'real-game','attempted':True,
                'checkpoints':[{'checkpoint_id':'CP2','status':'FAIL','actual':'Approached target, no hit occurred','evidence_refs':[]}]}]}
        view=role_context.build('planner','plan',{}, {},full_input_ref={},latest_result=result)
        self.assertEqual(view['facts'][0]['checkpoint_id'],'CP2')
        self.assertEqual(view['failed_checkpoint_count'],1)
        self.assertIn('no hit occurred',view['facts'][0]['preview'])

    def test_adjacent_actions_do_not_gain_invented_dependencies(self):
        plan=load_example('AgentPlan');plan['actions'].append(deepcopy(plan['actions'][0]))
        plan['actions'][1]['depends_on_action_indexes']=[]
        def hydrate():return agent_protocol.hydrate(plan,scope={},inputs={'graph_ref':{}},artifact=lambda x:x,read=lambda x:b'',write=lambda *a:None)
        value=hydrate();self.assertEqual(value['actions'][1]['depends_on_action_ids'],[])
        plan['actions'][1]['depends_on_action_indexes']=[0]
        self.assertEqual(hydrate()['actions'][1]['depends_on_action_ids'],['A1'])
        plan['actions'][1]['depends_on_action_indexes']=[1]
        with self.assertRaisesRegex(ValueError,'earlier action'):hydrate()

    def test_stable_finding_is_not_renumbered_when_wording_changes(self):
        review=load_example('AgentReview');review['findings'][0]['finding_id']='F-open'
        review['findings'][0]['notes_md']='Same issue explained differently'
        args={'scope':{},'inputs':{'context_ref':{},'finding_namespace':'phase',
            'open_findings':[{'id':'F-open'}]},'artifact':lambda x:x,'read':lambda x:b'','write':lambda *a:None}
        value=agent_protocol.hydrate(review,**args)
        self.assertEqual(value['findings'][0]['id'],'F-open')
        review['findings'][0]['finding_id']='F-invented'
        with self.assertRaisesRegex(ValueError,'existing open finding'):agent_protocol.hydrate(review,**args)

    def test_public_hypothesis_handle_and_assessment_remain_linked(self):
        first=agent_context.record_note(self.root,kind='hypothesis',body_md='Possible path mismatch',condition_ids=['C51'])
        second=agent_context.record_note(self.root,kind='assessment',body_md='Run not started, no causal conclusion',
            hypothesis_id=first['hypothesis_id'],status='inconclusive',observation_refs=['O1'])
        original=agent_context.read_json(self.root,first['ref']['path'])
        assessed=agent_context.read_json(self.root,second['ref']['path'])
        self.assertEqual(original['hypothesis_id'],assessed['hypothesis_id'])
        self.assertEqual(original['condition_ids'],['C51']);self.assertEqual(assessed['status'],'inconclusive')

    def test_public_continuation_snapshot_survives_live_checkpoint_updates(self):
        task={'continuation_key':'phase','role':'planner','project_id':'p','job_id':'j'}
        state=agent_context.Continuation(self.root,task,{'input':{},'scope':{}},'main')
        state.save([{'role':'user','content':'old prompt'},{'role':'assistant','content':[
            {'type':'thinking','thinking':'private'},{'type':'text','text':'public negative experiment'}]}])
        restored=state.load('new prompt')
        marker='Full public phase record: '
        ref=json.loads(restored[0]['content'].split(marker,1)[1].split('\n',1)[0])
        state.save(restored);state.finish()
        snapshot=agent_context.read_artifact(self.root,ref)
        self.assertIn('public negative experiment',snapshot['content'])
        self.assertNotIn('"thinking"',snapshot['content'])

    def test_codex_helper_requires_context_and_honours_readonly(self):
        from agent.codex_tools import main
        with patch('pathlib.Path.cwd',return_value=self.root),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main(['run_command','{}'])
            context={'role':'coder','readonly':True,'scope':{}}
            (self.root/'context.json').write_text(json.dumps(context))
            with self.assertRaisesRegex(ValueError,'read-only'):
                main(['run_command','{"argv":["echo","forbidden"]}','--workspace',str(self.root),'--context','context.json'])


class CycleContextTests(unittest.TestCase):
    setUp=cycle_fixture.CycleTest.setUp
    def test_actual_diff_and_unresolved_finding_view_are_passed_to_reviewer(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();decision,graph=cycle.plan(catalog,graph,{})
        cycle.code_and_review(catalog,graph,decision)
        reviews=[context for name,_,context in self.engine.requests if name=='ReviewResult' and context['input']['subject']=='code']
        self.assertTrue(reviews[0]['input']['diff_ref'])
        delta=pipeline.source_bytes(self.ws,reviews[0]['input']['diff_ref']).decode()
        self.assertIn('PlantTest.java',delta)
        context=json.loads(pipeline.source_bytes(self.ws,reviews[0]['input']['context_ref']))
        self.assertIn('open_finding_ledger_ref',context)
        self.assertEqual(context['diff_baseline_kind'],'before_coding')


if __name__=='__main__':unittest.main()
