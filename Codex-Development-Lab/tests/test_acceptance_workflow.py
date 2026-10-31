"""Controller integration: no model/network calls, real checkpoints and DB."""
import json
import unittest
from unittest.mock import patch
from common import acceptance, db
from worker import engine
from tests import test_timeout_recovery as fixture


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.actual_checks=engine.checks
        fixture.TimeoutRecoveryTests.setUp(self)
    complete = fixture.TimeoutRecoveryTests.complete
    verify = fixture.TimeoutRecoveryTests.verify

    def limit(self,number):
        with db.connect() as con:
            con.execute('UPDATE jobs SET max_rounds=? WHERE id=?',(number,self.jid))

    def test_unverified_assertions_return_to_model_instead_of_infinite_recheck(self):
        from common import acceptance_progress as progress
        catalog=acceptance.build_catalog('# Mod\n\n## Combat\n\n- Attack')
        rid=catalog['requirements'][0]['id'];source='a'*64
        scenario={'id':'combat','kind':'unit','task':'checkCombat','requirement_ids':[rid],
            'expected_tests':[{'classname':'CombatTest','name':'attack'}]}
        plan={'requirements':[{'id':rid,'kind':'unit','scenario_ids':['combat']}],
              'scenarios':[scenario],'notes':[]}
        ledger=progress.load_progress(self.stage.parent,catalog,plan)
        state=acceptance.build_state(catalog,plan,{'spec_sha256':catalog['spec_sha256'],
            'scenarios':[{'id':'combat','requirement_ids':[rid],'status':'NOT_TESTED',
                'test_status':'PASS','detail':'Missing named assertion'}]},source,1)
        ledger=progress.record_results(ledger,catalog,plan,state,1,source)
        assignment={'current_task':{'requirement_ids':[rid]}}
        queue=engine.work_queue(catalog,plan,ledger,source,assignment,self.stage.parent)
        self.assertEqual(queue['selected'],1)
        self.assertEqual(queue['pending_current'],0)

    def test_all_verified_pauses_for_review_without_another_model_call(self):
        self.limit(3);self.outcomes=['PASS']
        report={'status':'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',
            'checks':[{'label':'gradle_build','status':'PASS'}],
            'next_task_assignment':{'status':'all_verified_requires_review'}}
        with patch.object(engine,'checks',return_value=report):engine.replay_legacy(self.jid)
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(db.job_row(self.jid)['phase'],'acceptance_review_required')

    def test_provider_error_prefers_structured_failure_over_noisy_tool_output(self):
        error='DSLab request stopped (HTTP 429): Insufficient remaining task token budget'
        result={'status':'FAIL','duration_seconds':10,'tail':'unrelated output\n'+
            json.dumps({'type':'turn.failed','error':{'message':error}})}
        self.assertIn(error,engine.agent_error(result))
        self.assertNotIn('unrelated output',engine.agent_error(result))

    def test_acceptance_batches_do_not_dispatch_another_model(self):
        self.limit(3)
        source=acceptance.source_fingerprint(self.workspace)
        calls=[]
        def verify(ws,stage,jid):
            calls.append(stage.name)
            return {'status':'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED',
                'checks':[{'label':'gradle_build','status':'PASS'}],
                'acceptance':{'source_sha256':source,
                    'queue_summary':{'pending_current':4 if len(calls)<3 else 0,'unseen':4}}}
        self.outcomes=['PASS']
        with patch.object(engine,'checks',side_effect=verify):
            engine.replay_legacy(self.jid)
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual(calls,['round-01','round-02','round-03'])
        self.assertEqual(db.job_row(self.jid)['status'],'FINISHED')
        for number in (2,3):
            stage=self.stage.parent/f'round-{number:02d}'
            report=json.loads((stage/'verification_complete.json').read_text())
            self.assertEqual(report['development_kind'],'verification_only')
            self.assertFalse(report['progress_guard']['model_ran'])

    def test_unchanged_rounds_reach_limit_and_replay_does_not_repeat_model(self):
        self.limit(4)
        self.outcomes=['PASS']*4
        engine.replay_legacy(self.jid)
        row=db.job_row(self.jid)
        self.assertEqual((row['status'],row['phase'],row['round']),('FINISHED','max_rounds_reached',4))
        self.assertEqual(len(self.dispatched),4)
        self.assertTrue(acceptance.read_json(self.stage.parent,'progress-guard.json')['stalled'])
        # Simulate a crash before the final DB commit, retaining the checkpoint.
        db.update(self.jid,status='QUEUED',worker=None,lease_until=0)
        db.claim('crash-replay')
        engine.replay_legacy(self.jid)
        self.assertEqual(db.job_row(self.jid)['phase'],'max_rounds_reached')
        self.assertEqual(len(self.dispatched),4)

    def test_reused_stage_cannot_promote_old_acceptance_output(self):
        self.stage.mkdir(parents=True)
        old={'spec_sha256':'old','scenarios':[{'id':'old-pass','status':'TEST_PASSED'}]}
        result=self.stage/'acceptance_results.json'
        result.write_text(json.dumps(old))
        def verify(*args,**kwargs):
            self.assertFalse(result.exists())
            return {'status':'FAIL','checks':[{'label':'gradle_build','status':'FAIL'}]}
        # Override fixture's checks patch to exercise actual controller code.
        with patch.object(engine.DockerSandbox,'verify',side_effect=verify):
            report=self.actual_checks(self.workspace,self.stage,self.jid)
        self.assertEqual(report['acceptance']['summary']['test_passed'],0)

    def test_invalid_acceptance_plan_cannot_launch_generic_gameplay_fallback(self):
        self.stage.mkdir(parents=True)
        (self.workspace/'acceptance-plan.json').write_text('{"schema_version":1}')
        (self.stage/'acceptance_results.json').write_text('{"scenarios":[{"status":"TEST_PASSED"}]}')
        with patch.object(engine.DockerSandbox,'verify') as verify:
            report=self.actual_checks(self.workspace,self.stage,self.jid)
        verify.assert_not_called()
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['checks'][0]['label'],'acceptance_plan')
        self.assertTrue(report['checks'][0]['detail'])
        self.assertEqual(report['acceptance']['summary']['test_passed'],0)
        self.assertFalse((self.stage/'acceptance_results.json').exists())

    def test_valid_alias_plan_schedules_named_scenario_and_closes_schema_error(self):
        from worker.round_context import prepare_context
        self.stage.mkdir(parents=True)
        (self.workspace.parent/'ТЗ.md').write_text('# Mod\n\n- Attack hostile targets\n',encoding='utf-8')
        (self.workspace/'acceptance-plan.json').write_text('{"schema_version":1}')
        failed=self.actual_checks(self.workspace,self.stage,self.jid)
        prepare_context(self.workspace,self.stage.parent,2,failed)
        catalog=acceptance.build_catalog(acceptance.read_spec(self.workspace.parent))
        rid=catalog['requirements'][0]['id']
        plan={'schema_version':1,'spec_sha256':catalog['spec_sha256'],
            'requirements':[{'id':rid,'title':'Goal','expected':'assertion','kind':'unit','scenario_ids':['combat']}],
            'scenarios':[{'id':'combat','kind':'unit','gradle_task':'checkCombat',
                'requirement_ids':[rid],'expected_tests':[{'classname':'Combat','name':'attack'}]}]}
        (self.workspace/'acceptance-plan.json').write_text(json.dumps(plan))
        def verify(*args,**kwargs):
            request=acceptance.read_json(self.workspace,'acceptance-request.json')
            self.assertEqual(request['scenarios'][0]['task'],'checkCombat')
            return {'status':'FAIL','checks':[{'label':'gradle_build','status':'FAIL','detail':'compile error'}]}
        with patch.object(engine.DockerSandbox,'verify',side_effect=verify):
            checked=self.actual_checks(self.workspace,self.stage,self.jid)
        prepare_context(self.workspace,self.stage.parent,2,checked)
        memory=json.loads((self.stage.parent/'round-memory.json').read_text())
        schema_errors=[e for e in memory['errors'] if e['label']=='acceptance_plan']
        self.assertTrue(schema_errors)
        self.assertTrue(all(e['status']=='RESOLVED' for e in schema_errors))
        self.assertEqual(checked['checks'][0]['label'],'gradle_build')

