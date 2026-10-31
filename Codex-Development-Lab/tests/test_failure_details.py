"""Partial failure explanations require explicit, independent testcase facts."""
import copy,hashlib,tempfile,unittest
from pathlib import Path
from common.failure_details import enrich_result,hydrate,summarize


class FailureDetailsTests(unittest.TestCase):
    def setUp(self):
        self.scenario={'id':'combat','requirement_ids':['REQ-1'],
            'expected_tests':[{'classname':'Combat','name':n} for n in ('select','hit','stop')]}
        self.result={'id':'combat','status':'FAIL','test_status':'FAIL','detail':'Player did not hit',
            'assertion_evidence':{'test_cases':[
                {'classname':'Combat','name':'select','status':'TEST_PASSED'},
                {'classname':'Combat','name':'hit','status':'FAIL','message':'No attributed hit'}],
                'failures':[{'classname':'Combat','name':'hit','message':'No attributed hit'}],
                'missing_tests':[{'classname':'Combat','name':'stop'}]}}

    def test_early_failure_preserves_passed_failed_and_not_executed(self):
        details=summarize(self.scenario,self.result)
        self.assertEqual([r['status'] for r in details['checks']],['TEST_PASSED','FAIL','NOT_EXECUTED'])
        self.assertEqual(details['counts'],{'total':3,'passed':1,'failed':1,'not_executed':1,'skipped':0,'unknown':0})
        self.assertEqual(details['checks'][1]['reason'],'No attributed hit')
        self.assertFalse(details['checks'][2]['cause_confirmed'])
        self.assertEqual(details['root_cause_status'],'NOT_ESTABLISHED')

    def test_old_missing_details_do_not_invent_passing_checks(self):
        self.result['assertion_evidence'].pop('test_cases')
        details=summarize(self.scenario,self.result)
        self.assertEqual([r['status'] for r in details['checks']],['UNKNOWN','FAIL','NOT_EXECUTED'])
        self.assertEqual(details['counts']['passed'],0)

    def test_skip_is_not_failure_success_or_missing(self):
        self.result['assertion_evidence']['test_cases'].append(
            {'classname':'Combat','name':'stop','status':'SKIPPED','message':'Capability absent'})
        details=summarize(self.scenario,self.result)
        self.assertEqual(details['checks'][2]['status'],'SKIPPED')
        self.assertEqual(details['checks'][2]['reason'],'Capability absent')

    def test_enrichment_never_changes_verdict_or_mutates_input(self):
        original=copy.deepcopy(self.result)
        enriched=enrich_result(self.scenario,self.result)
        self.assertEqual(self.result,original)
        self.assertEqual(enriched['status'],'FAIL')
        self.assertNotIn('failure_details',original)

    def test_duplicate_passes_are_not_unambiguous_success(self):
        self.result['assertion_evidence']['test_cases'].append(
            {'classname':'Combat','name':'select','status':'TEST_PASSED'})
        self.assertEqual(summarize(self.scenario,self.result)['checks'][0]['status'],'UNKNOWN')

    def test_lifecycle_failure_remains_visible_outside_expected_checks(self):
        self.result['assertion_evidence']['failures'].append(
            {'classname':'Combat','name':'clientLifecycle','message':'Unexpected process error'})
        details=summarize(self.scenario,self.result)
        self.assertEqual(details['additional_failures'][0]['name'],'clientLifecycle')
        self.assertEqual(details['counts']['total'],3)

    def test_invalid_evidence_fields_do_not_crash_or_invent_pass(self):
        self.result['assertion_evidence']={'test_cases':'wrong','failures':None,'missing_tests':True}
        details=summarize(self.scenario,self.result)
        self.assertEqual(details['counts']['unknown'],3)


class HistoricalFailureDetailsTests(unittest.TestCase):
    def setUp(self):
        FailureDetailsTests.setUp(self)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.pid='a'*14;self.jid='b'*14
        self.relative='acceptance/combat/assertions/report-000.xml'
        self.file=self.root/self.pid/self.jid/'round-05'/self.relative
        self.file.parent.mkdir(parents=True)
        self.raw=(b'<testsuite tests="2" failures="1"><testcase classname="Combat" name="select"/>'
                  b'<testcase classname="Combat" name="hit"><failure message="No attributed hit"/>'
                  b'</testcase></testsuite>')
        self.file.write_bytes(self.raw)
        self.result['artifact_round']='round-05'
        self.result['assertion_evidence'].pop('test_cases')
        self.result['assertion_evidence']['reports']=[{'file':self.relative,
            'sha256':hashlib.sha256(self.raw).hexdigest()}]

    def read(self,**kwargs):
        return hydrate(self.root,self.pid,self.jid,self.scenario,self.result,**kwargs)

    def test_old_xml_reconstructs_real_success_and_failure_without_writing(self):
        original=copy.deepcopy(self.result);before=self.file.read_bytes()
        result=self.read();details=result['failure_details']
        self.assertEqual([c['status'] for c in details['checks']],['TEST_PASSED','FAIL','NOT_EXECUTED'])
        self.assertEqual(details['checks'][0]['report'],self.relative)
        self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(self.result,original)

    def test_changed_report_does_not_authorize_pass(self):
        self.file.write_text('<testsuite><testcase classname="Combat" name="select"/></testsuite>')
        details=self.read()['failure_details']
        self.assertEqual(details['checks'][0]['status'],'UNKNOWN')
        self.assertIn('hash mismatch',details['evidence_errors'][0])

    def test_foreign_report_path_and_artifact_round_are_rejected(self):
        self.result['assertion_evidence']['reports'][0]['file']='../../secret.xml'
        details=self.read()['failure_details']
        self.assertEqual(details['counts']['passed'],0)
        self.assertIn('Invalid report path',details['evidence_errors'][0])
        self.result['artifact_round']='../../secret'
        self.assertIn('Artifact round',self.read()['failure_details']['evidence_errors'][0])

    def test_explicit_round_supports_raw_results_but_not_invalid_numbers(self):
        self.result.pop('artifact_round')
        self.assertEqual(self.read(round_number=5)['failure_details']['counts']['passed'],1)
        for value in (True,-1,0,'5'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.read(round_number=value)

    def test_safe_xml_checks_run_even_when_hash_matches(self):
        self.file.write_bytes(b'<!DOCTYPE testsuite [<!ENTITY leak SYSTEM "file:///secret">]><testsuite/>')
        self.result['assertion_evidence']['reports'][0]['sha256']=hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.assertIn('Unsafe JUnit',self.read()['failure_details']['evidence_errors'][0])

    def test_cross_scenario_identity_cannot_read_reports(self):
        self.result['id']='other'
        with self.assertRaisesRegex(ValueError,'identity'):self.read()

    def test_symlink_report_cannot_be_followed(self):
        actual=self.file.with_suffix('.actual');self.file.rename(actual)
        try:self.file.symlink_to(actual)
        except OSError:self.skipTest('Symlink creation unavailable')
        details=self.read()['failure_details']
        self.assertEqual(details['counts']['passed'],0)
        self.assertTrue(details.get('evidence_errors'))
