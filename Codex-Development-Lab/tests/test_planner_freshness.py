"""Planner facts retain their verifier origin; old plans/notes cannot look new."""
import json
import unittest
from unittest.mock import patch

from common import acceptance, acceptance_progress
from worker import engine
from worker.round_context import _handoff, planner_input_revision, scenario_facts
from tests import test_round_context as context_fixture
from tests import test_planner_workflow as planner_fixture


class PlannerFactFreshnessTests(unittest.TestCase):
    setUp=context_fixture.RoundContextTests.setUp

    def test_unknown_handoff_age_never_claims_fresh(self):
        digest=acceptance.build_catalog(acceptance.read_spec(self.ws.parent))['spec_sha256']
        (self.ws/'agent-handoff.json').write_text(json.dumps(
            {'spec_sha256':digest,'summary':'Shield remains unverified'}),encoding='utf-8')
        value=_handoff(self.ws,digest,self.output,39)
        self.assertIsNone(value['stale'])
        self.assertEqual(value['freshness'],'unknown')
        self.assertIsNone(value['source_round'])

    def test_old_handoff_keeps_round_after_verification_only(self):
        digest=acceptance.build_catalog(acceptance.read_spec(self.ws.parent))['spec_sha256']
        source=acceptance.source_fingerprint(self.ws)
        (self.ws/'agent-handoff.json').write_text(json.dumps(
            {'spec_sha256':digest,'summary':'Shield remains unverified'}),encoding='utf-8')
        folder=self.output/'round-36';folder.mkdir()
        (folder/'agent-handoff.json').write_text(json.dumps({
            'summary':'Shield remains unverified','source_round':36,'source_sha256':source}),encoding='utf-8')
        value=_handoff(self.ws,digest,self.output,39)
        self.assertEqual(value['source_round'],36)
        self.assertEqual(value['freshness'],'older_round')
        self.assertTrue(value['stale'])
        (self.ws/'mod.java').write_text('changed')
        self.assertEqual(_handoff(self.ws,digest,self.output,39)['freshness'],'different_source')

    def test_current_and_historical_results_are_distinct_and_bounded(self):
        scenarios=[{'id':f'check-{i}','kind':'unit','task':'test','requirement_ids':['REQ-1'],
                    'expected_tests':[{'classname':'Suite','name':'check'}]} for i in range(70)]
        old={'round':37,'source_sha256':'a'*64,'definition_sha256':acceptance_progress.definition_digest(scenarios[0]),
             'result':{'status':'TEST_PASSED','test_status':'PASS'}}
        ledger={'scenarios':{'check-0':{'latest':old,'last_pass':old}}}
        state={'requirements':[{'scenarios':[{'id':'check-0','status':'STALE'}]}]}
        facts=scenario_facts({'scenarios':scenarios},ledger,state)
        self.assertEqual((len(facts['items']),facts['total'],facts['omitted']),(64,70,6))
        first=next(x for x in facts['items'] if x['id']=='check-0')
        self.assertEqual(first['status'],'STALE')
        self.assertEqual(first['last_pass']['round'],37)
        self.assertEqual(first['latest']['source_sha256'],'a'*64)
        self.assertTrue(all(x['status']!='TEST_PASSED' for x in facts['items']))

    def test_revision_changes_on_new_verification_or_source(self):
        def revision(ledger,source='a'*64):
            return planner_input_revision({'spec_sha256':'b'*64},{'scenarios':[]},
                                          ledger,source,{'kind':'normal'},'task')
        self.assertEqual(revision({'scenarios':{}}),revision({'timestamp':'ignored','scenarios':{}}))
        self.assertNotEqual(revision({'scenarios':{}}),revision({'scenarios':{}},'c'*64))
        self.assertNotEqual(revision({'scenarios':{}}),revision({'scenarios':{'shield':{'latest':{'round':38}}}}))


class PlannerCheckpointFreshnessTests(unittest.TestCase):
    setUp=planner_fixture.PlannerWorkflowTests.setUp
    response=planner_fixture.PlannerWorkflowTests.response
    plan=planner_fixture.PlannerWorkflowTests.plan
    verify=planner_fixture.PlannerWorkflowTests.verify
    complete=planner_fixture.PlannerWorkflowTests.complete

    def test_new_facts_archive_cached_plan_before_coder_dispatch(self):
        with patch.object(engine,'request_agent',side_effect=self.response):
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS')
        self.context['planner_input_revision']='changed-ledger'
        with patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['status'],'PASS',result)
        self.assertTrue((self.stage/'planner/plan-stale.json').exists())
        self.assertTrue((self.stage/'planner/plan-stale.jsonl').exists())
        self.assertTrue((self.stage/'planner/plan.json').exists())

    def test_changed_source_invalidates_saved_plan_even_with_old_context(self):
        with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
        (self.workspace/'new.java').write_text('changed source')
        with patch.object(engine,'request_agent',side_effect=self.response) as request:result,context=self.plan()
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual((self.workspace/'new.java').read_text(),'changed source')
        saved=json.loads((self.stage/'planner/plan.json').read_text())
        self.assertEqual(saved['source_sha256'],acceptance.source_fingerprint(self.workspace))
        self.assertEqual(saved['input_revision'],context['planner_input_revision'])

    def test_failed_replan_stops_after_one_request_and_keeps_archives(self):
        with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
        self.context['planner_input_revision']='changed-ledger'
        with patch.object(engine,'request_agent',return_value={'status':'FAIL','tail':'provider capacity'}) as request:
            result,_=self.plan()
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['status'],'FAIL')
        self.assertTrue((self.stage/'planner/plan-stale.json').exists())
        self.assertTrue((self.stage/'planner/plan-stale.jsonl').exists())
        self.assertFalse((self.stage/'planner/plan.json').exists())

    def test_wrong_round_or_spec_is_archived_before_automatic_replan(self):
        for key,value in [('round',999),('spec_sha256','wrong-spec')]:
            with self.subTest(key=key):
                with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
                path=self.stage/'planner/plan.json'
                cached=json.loads(path.read_text());cached[key]=value
                path.write_text(json.dumps(cached))
                with patch.object(engine,'request_agent',side_effect=self.response) as request:result,_=self.plan()
                self.assertEqual(request.call_count,1)
                self.assertEqual(result['status'],'PASS')
                restored=json.loads(path.read_text())
                self.assertEqual(restored['round'],1)
                self.assertEqual(restored['spec_sha256'],self.catalog['spec_sha256'])
                path.unlink()

    def test_replanning_preserves_diagnostics_and_multiple_archives(self):
        with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
        for iteration in range(2):
            self.context.update(planner_input_revision=f'changed-{iteration}',
                                acceptance_queue={'pending':3},failed_scenarios=['existing trace'])
            with patch.object(engine,'request_agent',side_effect=self.response) as request:
                result,context=self.plan()
            self.assertEqual(result['status'],'PASS',result)
            self.assertEqual(request.call_count,1)
            self.assertEqual(context['acceptance_queue'],{'pending':3})
            self.assertEqual(context['failed_scenarios'],['existing trace'])
        self.assertEqual(len(list((self.stage/'planner').glob('plan-stale*.json'))),2)
        self.assertEqual(len(list((self.stage/'planner').glob('plan-stale*.jsonl'))),2)

    def test_unfinished_planner_transport_is_never_retired_for_replan(self):
        with patch.object(engine,'request_agent',side_effect=self.response):self.plan()
        marker=self.stage/'planner/agent_attempt.json'
        marker.write_text(json.dumps({'id':'unfinished-planner'}))
        self.context['planner_input_revision']='changed-ledger'
        with patch.object(engine,'request_agent') as request:result,_=self.plan()
        request.assert_not_called()
        self.assertEqual(result['status'],'ERROR')
        self.assertIn('unfinished',result['tail'])
        self.assertTrue(marker.exists())
        self.assertTrue((self.stage/'planner/plan.json').exists())

    def test_existing_coder_checkpoint_recovers_without_replanning_stale_inputs(self):
        (self.stage/'agent_attempt.json').write_text(json.dumps({'id':'existing-coder-task'}))
        (self.workspace/'new.java').write_text('coder already changed source')
        with patch.object(engine,'plan_round') as planning, \
                patch.object(engine,'poll_job',return_value=None), \
                patch.object(engine,'request_agent',return_value={'status':'PASS','tail':''}) as request:
            result=engine.develop_round(self.jid,self.pid,1,self.workspace,{},self.stage,0)
        planning.assert_not_called()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(request.call_count,1)


if __name__=='__main__':unittest.main()
