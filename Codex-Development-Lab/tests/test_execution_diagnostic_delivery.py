"""Build barriers remain readable without inventing product measurements."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from common import condition_progress,evidence_access,verification_feedback
from tests.test_pipeline_verification import PipelineVerificationTests


class EvidenceAccessTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.ws=self.root/'workspace';self.ws.mkdir()
        self.scope={'project_id':'project-one','spec_revision':'spec-one','source_revision':'source-one','round':1}

    def test_complete_text_is_reachable_in_pages_and_exact_ranges(self):
        raw=('first diagnostic\n'+'ordinary output\n'*500+'last diagnostic\n').encode()
        ref=evidence_access.register_bytes(self.ws,raw,scope=self.scope,kind='command_log')
        page=evidence_access.read_artifact(self.ws,ref,limit=101,scope={'project_id':'project-one'})
        text=page['content']
        while page['next_cursor']:
            page=evidence_access.read_artifact(self.ws,ref,cursor=page['next_cursor'],limit=101)
            text+=page['content']
        self.assertEqual(text,raw.decode())
        self.assertEqual(evidence_access.read_artifact(self.ws,ref,start_line=1,end_line=1)['content'],'first diagnostic\n')
        self.assertEqual(evidence_access.list_evidence(self.ws)['total'],1)

    def test_registered_content_and_scope_are_rechecked(self):
        ref=evidence_access.register_bytes(self.ws,b'original',scope=self.scope,kind='command_log')
        for bad in ({'project_id':'other'},{'spec_revision':'other'}):
            with self.assertRaises(ValueError):evidence_access.read_artifact(self.ws,ref,scope=bad)
        with self.assertRaises(ValueError):evidence_access.read_artifact(self.ws,{'evidence_id':ref['evidence_id'],'path':'../../secret'})
        (self.ws/ref['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'digest mismatch'):evidence_access.read_artifact(self.ws,ref)

    def test_json_pointer_and_cursor_cannot_select_other_evidence(self):
        first=evidence_access.register_bytes(self.ws,b'{"checks":["a","b"]}',scope=self.scope,kind='json')
        second=evidence_access.register_bytes(self.ws,b'other',scope=self.scope,kind='text')
        page=evidence_access.read_artifact(self.ws,first,json_pointer='/checks',limit=2)
        self.assertTrue(page['partial'])
        with self.assertRaises(ValueError):evidence_access.read_artifact(self.ws,second,cursor=page['next_cursor'])
        with self.assertRaises(ValueError):evidence_access.read_artifact(self.ws,first,json_pointer='/checks/-1')

    def test_symlink_and_hardlink_blobs_cannot_read_external_files(self):
        ref=evidence_access.register_bytes(self.ws,b'original',scope=self.scope,kind='text')
        outside=self.root/'outside';outside.write_bytes(b'private')
        blob=self.ws/ref['path'];blob.unlink()
        try:blob.symlink_to(outside)
        except OSError:self.skipTest('Symlinks unavailable')
        with self.assertRaises((OSError,ValueError)):evidence_access.read_artifact(self.ws,ref)
        blob.unlink()
        try:os.link(outside,blob)
        except OSError:self.skipTest('Hardlinks unavailable')
        with self.assertRaises((OSError,ValueError)):evidence_access.read_artifact(self.ws,ref)
        self.assertEqual(outside.read_bytes(),b'private')


class BuildDiagnosticTests(unittest.TestCase):
    setUp=PipelineVerificationTests.setUp
    refresh_source=PipelineVerificationTests.refresh_source
    run_verification=PipelineVerificationTests.run_verification
    statuses=PipelineVerificationTests.statuses

    def install_build_failure(self,*,fresh_xml=None):
        original=self.sandbox.verify
        def fail(ws,stage,*args,**kwargs):
            report=original(ws,stage,*args,**kwargs)
            (stage/'acceptance_results.json').unlink(missing_ok=True)
            (stage/'build.log').write_text('early build assertion\n'+'ordinary\n'*10000+'BUILD FAILED\n')
            report['checks']=[{'label':'gradle_build','status':'FAIL','exit_code':1,'argv':['gradle','build'],
                              'log':'build.log','tail':'BUILD FAILED'}]
            if fresh_xml is not None:
                relative='test-results/lab-global-build/report-000.xml';path=stage/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(fresh_xml)
                report['build_assertion_evidence']={'fresh':True,'execution_stage':'build','execution_id':'physical-build-1',
                    'reports':[{'file':relative,'sha256':hashlib.sha256(fresh_xml).hexdigest()}]}
            return report
        self.sandbox.verify=fail

    def test_build_before_scenarios_is_not_a_product_failure_and_log_is_reachable(self):
        self.install_build_failure()
        result,legacy=self.run_verification()
        row=result['scenario_results'][0]
        self.assertEqual(row['status'],'NOT_EXECUTED');self.assertFalse(row['attempted'])
        self.assertEqual(self.statuses(result),['NOT_EXECUTED']*3)
        self.assertTrue(row['stopped_by']);self.assertEqual(result['build_status'],'FAIL')
        log_ref=next(ref for diagnostic in result['execution_diagnostics'] for ref in diagnostic['evidence_refs']
                     if evidence_access.read_artifact(self.ws,ref)['kind']=='execution_log')
        self.assertIn('early build assertion',evidence_access.read_artifact(self.ws,log_ref,start_line=1,end_line=1)['content'])
        index=verification_feedback.export_feedback(self.ws,self.stage.parent,legacy,3)
        self.assertTrue(index['execution_diagnostics']['items'])
        self.assertEqual(index['last_failure']['stage'],'build')
        observations=condition_progress.observations(self.manifest,result)
        self.assertTrue(all(row['attempt_kind']=='not_attempted' for row in observations))

    def test_actual_unit_assertions_inside_failed_build_are_preserved_but_not_ready(self):
        xml=self.sandbox.xml.replace(b'name="saplingPlaced"/>',
            b'name="saplingPlaced"><failure message="actual product failed"/></testcase>')
        self.install_build_failure(fresh_xml=xml)
        result,_=self.run_verification()
        self.assertEqual(self.statuses(result),['PASS','FAIL','PASS'])
        self.assertEqual(result['build_status'],'FAIL')
        row=result['scenario_results'][0]
        self.assertTrue(row['attempted']);self.assertEqual(row['execution_stage'],'build')
        self.assertEqual(row['execution_id'],'physical-build-1')
        self.assertNotEqual(row['status'],'PASS')
        self.assertIn('actual product failed',result['execution_diagnostics'][0]['actual'])
        observations=condition_progress.observations(self.manifest,result)
        self.assertTrue(any(o['status']=='FAIL' and o['attempt_kind']=='measurement' for o in observations))
        self.assertTrue(any(o['raw_checkpoint_status']=='PASS' for o in observations))

    def test_unit_build_reports_never_establish_gameplay_observations(self):
        self.manifest['scenarios'][0]['execution']['kind']='gameplay'
        self.install_build_failure(fresh_xml=self.sandbox.xml)
        result,_=self.run_verification()
        self.assertEqual(self.statuses(result),['NOT_EXECUTED']*3)
        self.assertFalse(result['scenario_results'][0]['attempted'])


class CompleteFailureSummaryTests(unittest.TestCase):
    def test_all_109_checks_are_counted_and_accessible_with_paging(self):
        from common.failure_details import summarize
        expected=[{'classname':'Suite','name':str(i)} for i in range(109)]
        scenario={'id':'unit','expected_tests':expected}
        result={'assertion_evidence':{'test_cases':[dict(t,status='PASS') for t in expected]}}
        complete=summarize(scenario,result)
        self.assertEqual(complete['counts']['total'],109)
        self.assertEqual(len(complete['checks']),109)
        page=summarize(scenario,result,offset=100,limit=5)
        self.assertEqual(page['counts']['total'],109)
        self.assertEqual(page['page']['next_offset'],105)
        self.assertEqual([t['name'] for t in page['checks']],['100','101','102','103','104'])


if __name__=='__main__':unittest.main()
