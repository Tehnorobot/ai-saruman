"""Controller integration: reviews do not silently dispatch a coder or generic game."""
import json,unittest
from unittest.mock import patch
from common import acceptance,coverage,planning
from worker import engine
from tests import test_planner_workflow as fixture
from tests import test_acceptance_workflow as workflow_fixture


class CoveragePlannerTests(unittest.TestCase):
    setUp=fixture.PlannerWorkflowTests.setUp
    response=fixture.PlannerWorkflowTests.response
    plan=fixture.PlannerWorkflowTests.plan
    complete=fixture.PlannerWorkflowTests.complete
    verify=fixture.PlannerWorkflowTests.verify

    def test_review_only_uses_planner_without_calling_coder(self):
        rid=self.ids[0]
        self.value['action'].update(kind='review_coverage',target=rid)
        self.value['coverage_reviews']=[{'requirement_id':rid,'obligations':[
            {'id':condition['id'],'verdict':'missing','scenario_ids':[],
             'reason':'No executable assertion currently covers this source condition.'}
            for condition in coverage.obligations(self.catalog)[rid]]}]
        with patch.object(engine,'request_agent',side_effect=self.response) as request, \
                patch.object(engine,'plan_round',side_effect=self.actual_plan), \
                patch.object(engine,'poll_job',return_value=None):
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        self.assertEqual(result['status'],'PASS')
        self.assertTrue(result['skip_coder'])
        self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.kwargs['role'],'planner')
        self.assertTrue((self.stage.parent/'coverage-reviews.json').exists())

    def test_available_failure_requires_actually_read_evidence(self):
        self.context['verification_feedback']={'file_count':1}
        self.value['action'].update(kind='diagnose_failure',target='observed-failure')
        with self.assertRaisesRegex(ValueError,'cite its actual lines'):
            planning.validate(self.value,self.catalog,acceptance.load_plan(self.workspace,self.catalog),self.context,1,'normal')
        source=self.workspace/'.lab-feedback';source.mkdir(exist_ok=True)
        (source/'trace.txt').write_text('AssertionError: the observed failure')
        self.value['action']['evidence_refs']=[{'path':'.lab-feedback/trace.txt','start_line':1,'end_line':1,
                                               'excerpt':'AssertionError: the observed failure'}]
        calls=0
        def respond(*args,**kwargs):
            nonlocal calls
            calls+=1
            self.assertLessEqual(calls,2,'Planner did not accept the actual diagnostic read')
            result=self.response(*args,**kwargs)
            if calls==2:
                logfile=args[4]
                content=logfile.read_text()
                observed={'type':'item.completed','item':{'type':'tool_result','text':
                    '1: '+(source/'trace.txt').read_text()}}
                logfile.write_text(json.dumps(observed)+'\n'+content)
            return result
        with patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(request.call_count,2)
        rejected=list((self.stage/'planner').glob('plan-invalid-*.json'))
        self.assertEqual(len(rejected),1)
        self.assertIn('not observed through tools',json.loads(rejected[0].read_text())['error'])

    def test_missing_condition_cannot_be_ignored_by_repeating_passed_scenario(self):
        rid=self.ids[0]
        plan={'requirements':[],'scenarios':[{'id':'air','requirement_ids':[rid]}]}
        self.context['coverage_status']=[{'requirement_id':rid,'conditions':[{'id':'lava','scenario_ids':[]}]}]
        self.context['scenario_facts']={'items':[{'id':'air','status':'TEST_PASSED'}]}
        self.value['action'].update(kind='implement_or_verify_scenario',target='air')
        with self.assertRaisesRegex(ValueError,'Uncovered source conditions'):
            planning.validate(self.value,self.catalog,plan,self.context,1,'normal')


class EmptyCoverageQueueTests(unittest.TestCase):
    setUp=workflow_fixture.WorkflowTests.setUp
    complete=workflow_fixture.WorkflowTests.complete
    verify=workflow_fixture.WorkflowTests.verify

    def test_empty_named_queue_never_falls_back_to_global_gameplay(self):
        self.stage.mkdir(parents=True)
        (self.workspace.parent/'ТЗ.md').write_text('# Mod\n\n- Surface safely\n',encoding='utf-8')
        catalog=acceptance.build_catalog(acceptance.read_spec(self.workspace.parent))
        rid=catalog['requirements'][0]['id']
        raw={'schema_version':1,'spec_sha256':catalog['spec_sha256'],
            'requirements':[{'id':rid,'title':'Goal','expected':'Observed behavior','kind':'unit','scenario_ids':['air']}],
            'scenarios':[{'id':'air','kind':'unit','task':'testAir','requirement_ids':[rid],
                          'expected_tests':[{'classname':'Air','name':'surface'}]}]}
        (self.workspace/'acceptance-plan.json').write_text(json.dumps(raw))
        def verify(*args,**kwargs):
            request=acceptance.read_json(self.workspace,'acceptance-request.json')
            self.assertEqual(request['scenarios'],[])
            self.assertTrue(request['skip_global_gameplay'])
            return {'status':'FAIL','checks':[{'label':'gradle_build','status':'FAIL','detail':'fixture compile failure'}]}
        with patch.object(engine.task_assignment,'request',return_value={
                'schema_version':1,'spec_sha256':catalog['spec_sha256'],'catalog_ids':[rid],'scenarios':[]}), \
                patch.object(engine.DockerSandbox,'verify',side_effect=verify) as sandbox:
            self.actual_checks(self.workspace,self.stage,self.jid)
        self.assertEqual(sandbox.call_count,1)
