"""Actual planning checkpoints, manual routing and recovery; no model calls."""
import json
import unittest
from unittest.mock import patch
from common import acceptance,db,planning
from worker import engine,planner
from worker.round_context import prepare_context
from tests import test_timeout_recovery as fixture

class PlannerWorkflowTests(unittest.TestCase):
    complete=fixture.TimeoutRecoveryTests.complete
    verify=fixture.TimeoutRecoveryTests.verify

    def setUp(self):
        self.actual_plan=engine.plan_round
        fixture.TimeoutRecoveryTests.setUp(self)
        (self.workspace.parent/'ТЗ.md').write_text('# Mod\n\n## Combat\n\n- Attack hostile targets\n\n## Usability\n\n- Comfortable appearance\n',encoding='utf-8')
        self.stage.mkdir(parents=True)
        self.catalog=acceptance.build_catalog(acceptance.read_spec(self.workspace.parent))
        self.ids=[r['id'] for r in self.catalog['requirements']]
        self.raw={'schema_version':1,'spec_sha256':self.catalog['spec_sha256'],
            'requirements':[{'id':rid,'title':'Rule','expected':'Observed result','kind':'manual','scenario_ids':[]} for rid in self.ids],
            'scenarios':[]}
        acceptance.write_workspace_json(self.workspace,'acceptance-plan.json',self.raw)
        self.context=prepare_context(self.workspace,self.stage.parent,1,{})
        self.value={'summary':'Implement the exact requirement',
            'action':{'kind':'implement_and_map_requirement','target':self.ids[0],
                'requirement_ids':[self.ids[0]],'reason':'First requirement needs an executable check'},
            'hypotheses':[],'next_steps':['Implement combat with named assertions'],'manual_reviews':[]}

    def response(self,jid,pid,n,prompt,logfile,**kwargs):
        self.assertEqual(kwargs['role'],'planner')
        logfile.write_text(json.dumps({'item':{'type':'agent_message','text':json.dumps(self.value)}}))
        return {'status':'PASS','tail':''}

    def plan(self,previous=None):
        return self.actual_plan(self.jid,self.pid,1,self.workspace,previous or {},self.stage,self.context)

    def test_success_checkpoint_reused_without_another_model_call(self):
        source=acceptance.source_fingerprint(self.workspace)
        with patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,context=self.plan()
            self.assertEqual(result['status'],'PASS')
            self.plan()
        self.assertEqual(request.call_count,1)
        public=planning.public_state(self.stage.parent)['data']
        self.assertTrue(public['plan_reused'])
        self.assertFalse(public['automatic_switch'])
        self.assertEqual(public['model_selection']['provider'],'codex')
        self.assertEqual(context['round_action']['target'],self.ids[0])
        self.assertEqual(acceptance.source_fingerprint(self.workspace),source)
        self.assertTrue((self.stage/'planner/plan.json').exists())

    def test_wrong_requirement_cannot_redirect_coder(self):
        self.value['action']['target']=self.ids[1]
        def respond(*args,**kwargs):
            result=self.response(*args,**kwargs)
            self.value['action']['target']=self.ids[0]
            return result
        with patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(request.call_count,2)
        self.assertTrue((self.stage/'planner/plan.json').exists())

    def test_controller_rotation_rejects_repeating_old_target(self):
        self.context['scheduling_directive']={'kind':'rotate_requirement','required_target':self.ids[1]}
        self.context['current_task']['requirement_ids']=self.ids
        def respond(*args,**kwargs):
            result=self.response(*args,**kwargs)
            self.value['action'].update(target=self.ids[1],requirement_ids=[self.ids[1]])
            return result
        with patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(request.call_count,2)

    def test_cached_plan_revalidated_after_controller_rotation(self):
        with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
        self.context['scheduling_directive']={'kind':'rotate_requirement','required_target':self.ids[1]}
        self.value['action'].update(target=self.ids[1],requirement_ids=[self.ids[1]])
        count=0
        def respond(*args,**kwargs):
            nonlocal count
            count+=1
            if count>2:
                errors=[json.loads(p.read_text())['error'] for p in (self.stage/'planner').glob('plan-invalid-*.json')]
                self.fail(str(errors[-2:]))
            return self.response(*args,**kwargs)
        original_prepare=engine.prepare_context
        def prepare(*args,**kwargs):
            context=original_prepare(*args,**kwargs)
            context['current_task']['requirement_ids']=self.ids
            return context
        with patch.object(engine,'prepare_context',side_effect=prepare), \
                patch.object(engine,'request_agent',side_effect=respond) as request:
            result,_=self.plan()
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['status'],'PASS')
        self.assertTrue(list((self.stage/'planner').glob('plan-stale-*.json')))

    def test_mandatory_return_rejects_another_requirement(self):
        self.context['scheduling_directive']={'kind':'return_to_failure','required_target':'original-failure'}
        with patch.object(engine,'poll_job',side_effect=[None,None,'USER_PAUSED']), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'USER_PAUSED')
        self.assertEqual(request.call_count,1)
        invalid=list((self.stage/'planner').glob('plan-invalid-*.json'))
        self.assertIn('return to the deferred failure',json.loads(invalid[0].read_text())['error'])

    def test_planner_source_mutation_blocks_coding(self):
        def mutate(*args,**kwargs):
            (self.workspace/'attack.java').write_text('mutation')
            return self.response(*args,**kwargs)
        with patch.object(engine,'request_agent',side_effect=mutate):result,_=self.plan()
        self.assertEqual(result['status'],'ERROR')
        self.assertIn('changed project',result['tail'])

    def test_failed_planner_attempt_retired_for_explicit_resume(self):
        folder=self.stage/'planner';folder.mkdir()
        marker=folder/'agent_attempt.json'
        marker.write_text(json.dumps({'id':self.jid+'-round01-'+'a'*32}))
        with patch.object(engine,'request_agent',return_value={'status':'FAIL','tail':'provider unavailable'}):
            result,_=self.plan()
        self.assertEqual(result['status'],'FAIL')
        self.assertFalse(marker.exists())

    def test_three_stalls_require_deep_hypotheses_and_preserve_context(self):
        self.value['hypotheses']=['The test calls a client-only API from the server test thread']
        with patch.object(engine,'request_agent',side_effect=self.response):
            result,context=self.plan({'progress_guard':{'streak':3}})
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(context['planner']['mode'],'deep_diagnosis')
        self.assertTrue(context['planner']['hypotheses'])

    def test_manual_annotation_survives_normalization_without_pass(self):
        review={'category':'visual_judgment','reason':'Only the user can judge personal visual comfort of this interface.',
            'automation_blocker':'The isolated client cannot measure the personal comfort judgment of the user.',
            'steps':['Open the interface and compare text readability.'],'expected_result':'The user can read the text comfortably.'}
        self.raw['requirements'][1]['manual_review']=review
        acceptance.write_workspace_json(self.workspace,'acceptance-plan.json',self.raw)
        plan=acceptance.load_plan(self.workspace,self.catalog)
        self.assertFalse(plan['notes'])
        state=acceptance.build_state(self.catalog,plan,None)
        self.assertEqual(state['requirements'][1]['review_status'],'USER_TEST_REQUIRED')
        self.assertEqual(state['summary']['test_passed'],0)
        self.assertEqual(state['requirements'][1]['manual_review']['steps'],review['steps'])

    def test_nested_null_junit_message_keeps_root_cause(self):
        from worker.acceptance_runner import assertion_evidence
        import os
        if os.name!='posix':self.skipTest('Production FD traversal requires Linux')
        build=self.root/'build';reports=build/'test-results';reports.mkdir(parents=True)
        (reports/'TEST.xml').write_text('<testsuite><testcase classname="C" name="test"><error message="null">java.lang.reflect.InvocationTargetException\nCaused by: java.lang.IllegalStateException: use computeOnClient\n at Test.run(Test.java:12)</error></testcase></testsuite>')
        status,detail,evidence=assertion_evidence(build,self.root/'evidence',[{'classname':'C','name':'test'}])
        self.assertEqual(status,'FAIL')
        self.assertIn('use computeOnClient',detail)
        self.assertNotIn('failure: null',detail)

    def test_manual_review_status_never_claims_ready(self):
        report={'status':'IN_PROGRESS','next_task_assignment':{'status':'all_automatable_verified_requires_review'}}
        self.assertTrue(engine.acceptance_review_ready(report))
        self.assertNotIn(report['status'],('PASS','READY'))

    def test_planner_bad_json_not_silently_accepted(self):
        log=self.stage/'bad.jsonl';log.write_text(json.dumps({'item':{'type':'tool_result','text':json.dumps(self.value)}}))
        with self.assertRaises(ValueError):planning.final_message(log)

    def test_planning_api_is_readonly_and_exposes_manual_instructions(self):
        from fastapi.testclient import TestClient
        from app import main
        review={'category':'visual_judgment','reason':'Only the user can judge personal visual comfort of this interface.',
            'automation_blocker':'The isolated client cannot measure the personal comfort judgment of the user.',
            'steps':['Open the interface and compare text readability.'],'expected_result':'The user can read the text comfortably.'}
        self.raw['requirements'][1]['manual_review']=review
        acceptance.write_workspace_json(self.workspace,'acceptance-plan.json',self.raw)
        before=db.job_row(self.jid)
        with patch.object(main,'ARTIFACTS',self.artifacts),patch.object(main,'PROJECTS',self.projects):
            client=TestClient(main.app)
            response=client.get('/api/jobs/'+self.jid+'/planning')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['planner']['state'],'missing')
            manual=response.json()['manual_reviews']
            self.assertEqual(len(manual),1)
            self.assertEqual(manual[0]['review_status'],'USER_TEST_REQUIRED')
            self.assertEqual(manual[0]['manual_review']['steps'],review['steps'])
            self.assertEqual(client.get('/api/jobs/missing/planning').status_code,404)
            self.assertEqual(client.get('/planner.js').status_code,200)
            self.assertEqual(client.get('/planner.css').status_code,200)
        self.assertEqual(db.job_row(self.jid),before)

    def test_planner_and_coder_order_and_cached_plan_on_retry(self):
        order=[]
        def request(*args,**kwargs):
            role=kwargs.get('role','coder');order.append(role)
            if role=='planner':return self.response(*args,**kwargs)
            return {'status':'PASS','tail':'saved coder result'}
        with patch.object(engine,'plan_round',side_effect=self.actual_plan),patch.object(engine,'request_agent',side_effect=request):
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(order,['planner','coder'])

    def test_planner_error_prevents_coder_dispatch(self):
        order=[]
        def fail(*args,**kwargs):
            order.append(kwargs.get('role','coder'))
            return {'status':'FAIL','tail':'provider error'}
        with patch.object(engine,'plan_round',side_effect=self.actual_plan),patch.object(engine,'request_agent',side_effect=fail):
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(order,['planner'])

    def test_planner_transport_has_separate_mailbox_and_cannot_reuse_coder_request(self):
        box=self.root/'planner-mailbox'
        coder=self.mailbox/'requests';coder.mkdir(parents=True)
        legacy=coder/(self.jid+'-round01.json');legacy.write_text('{}')
        with patch.dict('os.environ',{'PLANNER_TASKS':str(box)}),patch.object(engine,'poll_job',return_value='USER_PAUSED'):
            result=engine.request_agent(self.jid,self.pid,1,'Read only',self.stage/'planner/codex.jsonl',role='planner')
        self.assertEqual(result['status'],'USER_PAUSED')
        self.assertTrue(legacy.exists())
        requests=list((self.stage/'agent-history').glob('*/request.json'))
        self.assertEqual(len(requests),1)
        archived=json.loads(requests[0].read_text())
        self.assertEqual(archived['role'],'planner')
        self.assertNotEqual(archived['id'],legacy.stem)

    def test_dslab_planner_and_coder_share_existing_round_budget(self):
        row={**db.job_row(self.jid),'provider':'dslab','model':'claude-sonnet-5-5',
            'dslab_token_budget':2000000,'dslab_max_output_tokens':4096,'dslab_max_calls':100}
        box=self.root/'planner-mailbox'
        with patch.dict('os.environ',{'PLANNER_TASKS':str(box)}),patch.object(engine.db,'job_row',return_value=row), \
             patch.object(engine,'poll_job',return_value='USER_PAUSED'), \
             patch.object(engine,'provider_control',return_value={'capability':'synthetic-test-capability'}) as control:
            engine.request_agent(self.jid,self.pid,1,'Plan',self.stage/'planner/codex.jsonl',role='planner')
            (self.stage/'planner/plan.json').write_text('{}')
            engine.request_agent(self.jid,self.pid,1,'Implement',self.stage/'codex.jsonl')
        registrations=[c.args[2] for c in control.call_args_list if c.args[:2]==('POST','/internal/tasks')]
        self.assertEqual(len(registrations),2)
        self.assertEqual(sum(c['token_budget'] for c in registrations),2000000)
        self.assertEqual([(c['role'],c['token_budget']) for c in registrations],
                         [('planner',400000),('coder',1600000)])
        self.assertEqual(sum(c['max_calls'] for c in registrations),100)
        self.assertEqual([c['max_calls'] for c in registrations],[20,80])

    def test_stall_continues_to_user_round_limit_without_fake_progress(self):
        calls=[]
        def develop(jid,pid,n,ws,previous,stage,generation):
            calls.append(n);(stage/'planner').mkdir(exist_ok=True)
            engine.write_json(stage/'planner/plan.json',{'mode':planner.mode(previous)})
            return {'status':'PASS','tail':''}
        report={'status':'FAIL','checks':[{'label':'gradle_build','status':'PASS'}],
                'task_assignment':{'current_task_id':'same-task'},'acceptance':{'source_sha256':'a'*64}}
        with db.connect() as con:con.execute('UPDATE jobs SET max_rounds=10 WHERE id=?',(self.jid,))
        with patch.object(engine,'develop_round',side_effect=develop),patch.object(engine,'checks',return_value=report):
            engine.replay_legacy(self.jid)
        self.assertEqual(calls,list(range(1,11)))
        self.assertEqual(db.job_row(self.jid)['phase'],'max_rounds_reached')
        self.assertTrue(acceptance.read_json(self.stage.parent,'progress-guard.json')['stalled'])
        last=acceptance.read_json(self.stage.parent/'round-05/planner','plan.json')
        self.assertEqual(last['mode'],'deep_diagnosis')

    def test_distinct_providers_reach_the_correct_stage(self):
        selection={'planner':{'provider':'litellm','model':'zai-org/GLM-5.3-Flash','reasoning_effort':'default'},
                   'coder':{'provider':'dslab','model':'claude-sonnet-5-5','reasoning_effort':'medium'}}
        row={**db.job_row(self.jid),'role_models':json.dumps(selection)}
        with patch.dict('os.environ',{'PLANNER_TASKS':str(self.root/'planner-box')}),patch.object(engine.db,'job_row',return_value=row), \
             patch.object(engine,'poll_job',return_value='USER_PAUSED'), \
             patch.object(engine,'provider_control',return_value={'capability':'synthetic-test'}) as control:
            engine.request_agent(self.jid,self.pid,1,'Plan',self.stage/'planner/codex.jsonl',role='planner')
            (self.stage/'planner/plan.json').write_text('{}')
            engine.request_agent(self.jid,self.pid,1,'Code',self.stage/'codex.jsonl')
        registrations=[c.args[2] for c in control.call_args_list if c.args[:2]==('POST','/internal/tasks')]
        self.assertEqual([(c['role'],c['provider'],c['model']) for c in registrations],
            [('planner','litellm','zai-org/GLM-5.3-Flash'),('coder','dslab','claude-sonnet-5-5')])

    def test_role_settings_api_validates_and_preserves_paused_job(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client=TestClient(app);endpoint='/api/jobs/'+self.jid+'/agent-models'
        with db.connect() as con:con.execute("UPDATE jobs SET status='USER_PAUSED' WHERE id=?",(self.jid,))
        models={'planner':{'provider':'litellm','model':'zai-org/GLM-5.3-Flash','reasoning_effort':'default'}}
        response=client.put(endpoint,json={'models':models})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['models'],models)
        self.assertEqual(db.job_row(self.jid)['status'],'USER_PAUSED')
        with db.connect() as con:con.execute("UPDATE jobs SET status='RUNNING' WHERE id=?",(self.jid,))
        self.assertEqual(client.put(endpoint,json={'models':{}}).status_code,409)
        self.assertEqual(client.put(endpoint,json={'models':{'reviewer':models['planner']}}).status_code,409)
        with db.connect() as con:con.execute("UPDATE jobs SET status='USER_PAUSED' WHERE id=?",(self.jid,))
        self.assertEqual(client.put(endpoint,json={'models':{'reviewer':models['planner']}}).status_code,200)
        self.assertEqual(client.get('/agent-models.js').status_code,200)
