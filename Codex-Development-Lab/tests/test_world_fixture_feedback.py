"""Unstarted world setup is navigation/diagnostics, never product evidence."""
from copy import deepcopy
import hashlib
import json
import unittest

from common import condition_progress, evidence_access, pipeline_memory, role_context, verification_feedback
from common.verification import infrastructure_failure
from tests import test_pipeline_verification as fixtures


class WorldFixtureFeedbackTests(unittest.TestCase):
    setUp=fixtures.PipelineVerificationTests.setUp
    refresh_source=fixtures.PipelineVerificationTests.refresh_source
    run_verification=fixtures.PipelineVerificationTests.run_verification
    statuses=fixtures.PipelineVerificationTests.statuses

    def gameplay(self):
        self.manifest['scenarios'][0]['execution']['kind']='gameplay'
        self.manifest['scenarios'][0]['execution']['gradle_task']='runClientGameTest'

    def world_failure(self, *, receipt=True, fixture_status='ERROR', details=None, retain_proof=False):
        original=self.sandbox.verify
        reason=details or 'Vanilla world preparation did not close cleanly: server stderr contains the concrete cause'
        def blocked(ws,stage,*args,**kwargs):
            report=original(ws,stage,*args,**kwargs)
            payload=json.loads((stage/'acceptance_results.json').read_text(encoding='utf-8'))
            kinds={s['id']:s['execution']['kind'] for s in self.manifest['scenarios']}
            for row in payload['scenarios']:
                if kinds[row['id']]!='gameplay':continue
                row.update(status='NOT_TESTED',test_status='NOT_TESTED',attempted=False,
                           execution_stage='world_preparation',stopped_by='world_fixture',detail=reason)
                if not retain_proof:
                    for descriptor in row.pop('assertion_evidence',{}).get('reports',[]):
                        (stage/descriptor['file']).unlink()
            (stage/'acceptance_results.json').write_text(json.dumps(payload),encoding='utf-8')
            report['acceptance_results']=payload
            report['world_fixture']={'status':fixture_status,'reason':reason,
                                     'provenance':'World setup, not product PASS'}
            if receipt:
                target=stage/'world-fixture';target.mkdir(parents=True,exist_ok=True)
                (target/'world-fixture.json').write_text(json.dumps(report['world_fixture']),encoding='utf-8')
                (target/'prepare.log').write_bytes(('PRIMARY WORLD CAUSE\n'+'full generation output\n'*10000).encode())
                (target/'process.log').write_text('preparer result: '+reason,encoding='utf-8')
            return report
        self.sandbox.verify=blocked
        return reason

    def test_world_failure_is_not_executed_not_a_product_error_or_pause(self):
        self.gameplay();reason=self.world_failure()
        result,legacy=self.run_verification()
        row=result['scenario_results'][0]
        self.assertEqual(result['build_status'],'PASS')
        self.assertEqual(row['status'],'NOT_EXECUTED')
        self.assertEqual(self.statuses(result),['NOT_EXECUTED']*3)
        self.assertFalse(row['attempted'])
        self.assertEqual(row['execution_stage'],'not_started')
        self.assertEqual(result['infrastructure_errors'],[])
        self.assertIsNone(infrastructure_failure(legacy))
        self.assertTrue(all(o['actual']==reason and not o['evidence_refs'] for o in row['checkpoints']))
        diagnostic=next(d for d in result['execution_diagnostics'] if d['stage']=='world_preparation')
        self.assertEqual(row['stopped_by'],[diagnostic['diagnostic_id']])
        self.assertIn(reason,diagnostic['actual'])
        self.assertFalse(any('Exact executed-test inventory' in s for s in legacy.get('pipeline_diagnostics',[])))
        observed=condition_progress.observations(self.manifest,result)
        self.assertTrue(all(o['attempt_kind']=='not_attempted' for o in observed))
        self.assertTrue(all(o['stopped_by']==row['stopped_by'] for o in observed))

    def test_original_world_receipt_and_entire_log_are_reachable(self):
        self.gameplay();self.world_failure()
        result,legacy=self.run_verification()
        diagnostic=result['execution_diagnostics'][0]
        by_kind={evidence_access.read_artifact(self.ws,ref)['kind']:ref
                 for ref in diagnostic['evidence_refs']}
        self.assertIn('world_preparation_result',by_kind)
        self.assertIn('world_preparation_receipt',by_kind)
        log=by_kind['world_preparation_log']
        self.assertEqual(evidence_access.read_artifact(self.ws,log,start_line=1,end_line=1)['content'],
                         'PRIMARY WORLD CAUSE\n')
        original=(self.stage/'world-fixture/prepare.log').read_bytes()
        self.assertEqual(log['sha256'],hashlib.sha256(original).hexdigest())
        self.assertIn('round-03/world-fixture/prepare.log',{r['path'] for r in result['raw_report_refs']})
        # RoundSession.record_verification supplies this scope before exporting
        # diagnostics. Omitting it would intentionally request legacy scope.
        legacy['pipeline_result']=result
        index=verification_feedback.export_feedback(self.ws,self.stage.parent,legacy,3)
        self.assertEqual(index['last_failure']['stage'],'world_preparation')
        self.assertEqual(index['execution_diagnostics']['items'][0]['diagnostic_id'],diagnostic['diagnostic_id'])

    def test_role_context_contains_primary_world_cause_instead_of_missing_junit(self):
        self.gameplay();reason=self.world_failure()
        result,_=self.run_verification()
        view=role_context.build('planner','plan',result['scope'],{},full_input_ref={'path':'input.json'},
                                latest_result=result)
        self.assertEqual(view['facts'][0]['stage'],'world_preparation')
        self.assertIn(reason,view['facts'][0]['preview'])
        self.assertEqual(view['failed_checkpoint_count'],0)
        self.assertEqual(view['not_attempted'][0]['scenario_id'],self.manifest['scenarios'][0]['id'])

    def test_unit_scenario_still_has_actual_passes_when_world_preparation_fails(self):
        unit=deepcopy(self.manifest['scenarios'][0]);unit['id']='independent-unit'
        self.gameplay();self.manifest['scenarios'].append(unit)
        self.world_failure()
        result,legacy=self.run_verification()
        rows={s['scenario_id']:s for s in result['scenario_results']}
        self.assertEqual(rows['independent-unit']['status'],'PASS')
        self.assertEqual([c['status'] for c in rows['independent-unit']['checkpoints']],['PASS']*3)
        self.assertEqual(rows['s1']['status'],'NOT_EXECUTED')
        self.assertFalse(result['infrastructure_errors'])
        self.assertIsNone(infrastructure_failure(legacy))

    def test_world_setup_failure_does_not_erase_a_prior_real_product_failure(self):
        self.gameplay()
        self.sandbox.xml=self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="real placement failure"/></testcase>')
        first,_=self.run_verification()
        self.assertEqual(first['scenario_results'][0]['status'],'FAIL')
        archive=self.stage.parent
        def remember(result):
            record=pipeline_memory.append_event(archive,None,'verification',result,scope=result['scope'],producer='runner')
            pipeline_memory.append_event(archive,None,'reflection',{
                'artifact_type':'ConditionObservationMapping','verification_record_id':record['record_id'],
                'manifest':deepcopy(self.manifest)},scope=result['scope'],producer='controller')
        remember(first)
        self.stage=archive/'round-04';self.world_failure()
        second,_=self.run_verification();remember(second)
        progress=condition_progress.reconstruct(archive,self.catalog,second['scope'],lambda _:None)
        condition=next(c for c in progress['conditions'] if c['condition_id']=='C1')
        checkpoint=next(c for c in condition['checkpoints'] if c['checkpoint_id']=='CP2')
        self.assertEqual(checkpoint['failures_since_pass'],1)
        self.assertEqual(checkpoint['unresolved_observations'],1)
        self.assertEqual(checkpoint['unresolved_gaps'],1)
        self.assertEqual(checkpoint['last_measurement_status'],'FAIL')
        self.assertEqual(len(condition['history']),2)

    def test_real_hash_verified_failure_cannot_be_waived_by_unstarted_flags(self):
        self.gameplay()
        self.sandbox.xml=self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="real product failure"/></testcase>')
        self.world_failure(retain_proof=True)
        result,_=self.run_verification()
        self.assertEqual(self.statuses(result),['PASS','FAIL','PASS'])
        self.assertEqual(result['scenario_results'][0]['status'],'FAIL')
        self.assertNotEqual(result['scenario_results'][0].get('attempted'),False)

    def test_missing_preparation_files_still_preserve_exact_reason_and_summary(self):
        self.gameplay();reason=self.world_failure(receipt=False)
        result,_=self.run_verification()
        diagnostic=result['execution_diagnostics'][0]
        self.assertEqual(diagnostic['actual'],reason)
        self.assertEqual(len(diagnostic['evidence_refs']),1)
        content=evidence_access.read_artifact(self.ws,diagnostic['evidence_refs'][0])['content']
        self.assertIn(reason,content)
        self.assertEqual(result['scenario_results'][0]['status'],'NOT_EXECUTED')

    def test_clone_failure_is_diagnosed_even_with_successfully_prepared_template(self):
        self.gameplay();reason=self.world_failure(fixture_status='TEMPLATE_READY',details='Clone failed: no free disk space')
        result,_=self.run_verification()
        diagnostic=result['execution_diagnostics'][0]
        self.assertEqual(diagnostic['stage'],'world_preparation')
        self.assertEqual(diagnostic['status'],'NOT_TESTED')
        self.assertIn(reason,diagnostic['actual'])
        self.assertFalse(result['scenario_results'][0]['attempted'])

    def test_unit_results_are_not_reclassified_by_the_world_marker(self):
        original=self.sandbox.verify
        def mistaken(ws,stage,*args,**kwargs):
            report=original(ws,stage,*args,**kwargs)
            value=json.loads((stage/'acceptance_results.json').read_text())
            row=value['scenarios'][0]
            row.pop('assertion_evidence')
            row.update(status='NOT_TESTED',test_status='NOT_TESTED',attempted=False,
                execution_stage='world_preparation',stopped_by='world_fixture')
            (stage/'acceptance_results.json').write_text(json.dumps(value))
            return report
        self.sandbox.verify=mistaken
        result,_=self.run_verification()
        self.assertEqual(self.statuses(result),['ERROR']*3)

    def test_explicit_fixture_prerequisites_keep_world_failure_reason(self):
        self.gameplay()
        self.manifest['scenarios'][0]['execution']['precondition_tests']=[{
            'classname':'dev.example.PlantTests','name':'siteFound'}]
        reason=self.world_failure()
        result,_=self.run_verification()
        row=result['scenario_results'][0]
        self.assertEqual(row['status'],'NOT_EXECUTED')
        self.assertEqual(row['fixture_result']['status'],'NOT_EXECUTED')
        self.assertEqual(row['fixture_result']['checks'][0]['actual'],reason)
        self.assertTrue(all(c['actual']==reason for c in row['checkpoints']))


if __name__=='__main__':unittest.main()
