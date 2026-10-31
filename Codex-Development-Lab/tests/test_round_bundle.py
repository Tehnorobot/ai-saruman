"""Validate coherent packages and dispatch the entire package to a single coder."""
import copy,json,unittest
from unittest.mock import patch
from common import acceptance,planning
from worker import engine
from tests import test_planner_workflow as fixture
from tests import test_acceptance_workflow as verification_fixture


class RoundBundleTests(unittest.TestCase):
    setUp=fixture.PlannerWorkflowTests.setUp
    response=fixture.PlannerWorkflowTests.response
    plan=fixture.PlannerWorkflowTests.plan
    complete=fixture.PlannerWorkflowTests.complete
    verify=fixture.PlannerWorkflowTests.verify

    def package(self):
        self.context['current_task']['requirement_ids']=self.ids[:]
        self.value.update(bundle_reason='Both behaviors share one controller and a single game fixture.',
            additional_actions=[{'kind':'implement_and_map_requirement','target':self.ids[1],
                'requirement_ids':[self.ids[1]],'reason':'Complete the related lifecycle behavior',
                'depends_on':[self.ids[0]]}],verification_limit=2)

    def validate(self):
        return planning.validate(self.value,self.catalog,acceptance.load_plan(self.workspace,self.catalog),self.context,1,'normal')

    def test_two_related_actions_preserve_order_scope_and_dependency(self):
        self.package();decision=self.validate()
        self.assertEqual([a['target'] for a in planning.round_actions(decision)],self.ids)
        self.assertEqual(decision['action_count'],2)
        self.assertEqual(decision['additional_actions'][0]['depends_on'],[self.ids[0]])

    def test_single_legacy_action_is_still_valid(self):
        decision=self.validate()
        self.assertEqual(decision['action_count'],1)
        self.assertEqual(planning.round_actions(decision),[decision['action']])

    def test_unknown_unrelated_duplicate_and_future_dependency_are_rejected(self):
        for change in ({'target':'REQ-unknown'},{'target':self.ids[0]},
                       {'depends_on':['future-target']},{'kind':'review_coverage'}):
            with self.subTest(change=change):
                self.package();self.value['additional_actions'][0].update(change)
                with self.assertRaises(ValueError):self.validate()
        self.package();self.context['current_task']['requirement_ids']=[self.ids[0]]
        with self.assertRaisesRegex(ValueError,'current task'):self.validate()

    def test_count_reason_and_verification_limit_are_enforced(self):
        for change in ({'bundle_reason':''},{'verification_limit':True},{'verification_limit':1},
                       {'verification_limit':5},{'additional_actions':[{}]*4}):
            with self.subTest(change=change):
                self.package();self.value.update(change)
                with self.assertRaises(ValueError):self.validate()

    def test_deep_review_manual_and_directive_rounds_cannot_hide_extra_implementation(self):
        self.package();self.value['manual_reviews']=[{}]
        with self.assertRaises(ValueError):self.validate()
        self.package();self.value.pop('manual_reviews',None)
        with self.assertRaisesRegex(ValueError,'[Dd]eep diagnosis'):
            planning.validate(self.value,self.catalog,acceptance.load_plan(self.workspace,self.catalog),self.context,1,'deep_diagnosis')
        self.package();self.context['scheduling_directive']={'kind':'rotate_requirement','required_target':self.ids[0]}
        with self.assertRaisesRegex(ValueError,'mandatory directives'):self.validate()

    def test_every_member_requires_real_evidence_when_repairing(self):
        self.package();self.value['action'].update(kind='repair_scenario',target='broken')
        self.value['additional_actions'][0].update(kind='repair_scenario',target='another',depends_on=['broken'])
        self.value['action']['evidence_refs']=[{'path':'.lab-feedback/trace.txt','start_line':1,'end_line':1,'excerpt':'observed real error'}]
        self.context['verification_feedback']={'file_count':1}
        plan={'scenarios':[{'id':'broken','requirement_ids':[self.ids[0]]},
                           {'id':'another','requirement_ids':[self.ids[1]]}]}
        with self.assertRaisesRegex(ValueError,'cite its actual lines'):
            planning.validate(self.value,self.catalog,plan,self.context,1,'normal')

    def test_one_coder_call_receives_entire_package_and_history_preserves_it(self):
        self.package();dispatches=[]
        prepare=engine.prepare_context
        def context_for_shared_task(*args,**kwargs):
            context=prepare(*args,**kwargs)
            # The inherited fixture puts the two requirements in separate groups.
            # This test models a controller task containing both related members.
            context['current_task']['requirement_ids']=self.ids[:]
            return context
        def respond(jid,pid,n,prompt,logfile,**kwargs):
            dispatches.append((kwargs.get('role','coder'),prompt))
            if kwargs.get('role')=='planner':return self.response(jid,pid,n,prompt,logfile,**kwargs)
            self.assertIn('round_actions',prompt)
            self.assertIn(self.ids[1],prompt)
            self.assertIn('Both behaviors share',prompt)
            return {'status':'PASS','tail':''}
        with patch.object(engine,'request_agent',side_effect=respond), \
                patch.object(engine,'prepare_context',side_effect=context_for_shared_task), \
                patch.object(engine,'plan_round',side_effect=self.actual_plan), \
                patch.object(engine,'poll_job',return_value=None):
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual([r for r,_ in dispatches],['planner','coder'])
        saved=acceptance.read_json(self.stage/'planner','plan.json')
        self.assertEqual(saved['action_count'],2)
        memory=acceptance.read_json(self.stage.parent,'round-memory.json')
        self.assertEqual([a['target'] for a in memory['round_actions']],self.ids)
        public=planning.public_state(self.stage.parent)['data']
        self.assertEqual(public['additional_actions'][0]['target'],self.ids[1])

    def test_recovered_package_is_revalidated_before_any_coder_dispatch(self):
        self.package()
        with patch.object(engine,'request_agent',side_effect=self.response):result,_=self.plan()
        self.assertEqual(result['status'],'PASS')
        path=self.stage/'planner/plan.json';value=json.loads(path.read_text())
        value['additional_actions'][0]['target']='REQ-forged';path.write_text(json.dumps(value))
        prepare=engine.prepare_context
        def refreshed_context(*args,**kwargs):
            context=prepare(*args,**kwargs)
            context['current_task']['requirement_ids']=self.ids[:]
            return context
        with patch.object(engine,'prepare_context',side_effect=refreshed_context), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:result,_=self.plan()
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['status'],'PASS')
        corrected=json.loads(path.read_text())
        self.assertEqual(corrected['additional_actions'][0]['target'],self.ids[1])
        self.assertTrue(list((self.stage/'planner').glob('plan-stale-*.json')))

    def test_next_round_context_never_relabels_old_package_as_new(self):
        self.package()
        with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
        engine.prepare_context(self.workspace,self.stage.parent,2,{})
        memory=acceptance.read_json(self.stage.parent,'round-memory.json')
        self.assertEqual(memory['round_action_round'],2)
        self.assertEqual(memory['round_actions'],[memory['round_action']])
        self.assertEqual(memory['round_package']['action_count'],1)

    def test_test_queue_uses_only_same_round_and_spec_controller_checkpoint(self):
        self.package()
        with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
        actions,limit=planning.verification_package(self.stage.parent,1,self.catalog)
        self.assertEqual([a['target'] for a in actions],self.ids);self.assertEqual(limit,2)
        self.assertEqual(planning.verification_package(self.stage.parent,2,self.catalog)[0],[])
        self.assertEqual(planning.verification_package(self.stage.parent,1,{'spec_sha256':'other'})[0],[])


class RoundBundleChecksIntegrationTests(unittest.TestCase):
    setUp=verification_fixture.WorkflowTests.setUp
    complete=verification_fixture.WorkflowTests.complete
    verify=verification_fixture.WorkflowTests.verify

    def test_actual_controller_verification_prioritizes_new_package_scenarios(self):
        self.stage.mkdir(parents=True)
        (self.workspace.parent/'ТЗ.md').write_text('# Mod\n\n## Behavior\n\n- First behavior\n- Second behavior\n',encoding='utf-8')
        catalog=acceptance.build_catalog(acceptance.read_spec(self.workspace.parent));ids=[r['id'] for r in catalog['requirements']]
        raw={'schema_version':1,'spec_sha256':catalog['spec_sha256'],
             'requirements':[{'id':rid,'title':'Goal','expected':'Observed result','kind':'unit','scenario_ids':[f'scenario-{i}']}
                             for i,rid in enumerate(ids)],
             'scenarios':[{'id':f'scenario-{i}','kind':'unit','task':'testBehavior','requirement_ids':[rid],
                           'expected_tests':[{'classname':'Behavior','name':f'test{i}'}]} for i,rid in enumerate(ids)]}
        acceptance.write_workspace_json(self.workspace,'acceptance-plan.json',raw)
        folder=self.stage/'planner';folder.mkdir()
        engine.write_json(folder/'plan.json',{'status':'PLANNED','round':1,
            'spec_sha256':catalog['spec_sha256'],'verification_limit':2,
            'action':{'kind':'implement_and_map_requirement','target':ids[1]},
            'additional_actions':[{'kind':'implement_and_map_requirement','target':ids[0]}]})
        def verify(*args,**kwargs):
            request=acceptance.read_json(self.workspace,'acceptance-request.json')
            self.assertEqual([s['id'] for s in request['scenarios']],['scenario-1','scenario-0'])
            return {'status':'FAIL','checks':[{'label':'gradle_build','status':'FAIL','detail':'fixture build failure'}]}
        with patch.object(engine.DockerSandbox,'verify',side_effect=verify) as sandbox:
            self.actual_checks(self.workspace,self.stage,self.jid)
        self.assertEqual(sandbox.call_count,1)
