"""Real parser, diagnostic export and dashboard/memory projections of partial FAIL."""
import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from common import acceptance,db,memory_view,verification_feedback
from worker import acceptance_runner


class FailureDetailsIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.artifacts=self.root/'artifacts';self.projects=self.root/'projects'
        for context in (patch.object(db,'DB',self.root/'db.sqlite'),
                        patch.dict(os.environ,{'LAB_PROJECTS':str(self.projects)}),
                        patch.object(main,'PROJECTS',self.projects),patch.object(main,'ARTIFACTS',self.artifacts)):
            context.start();self.addCleanup(context.stop)
        self.pid,self.jid=db.create_project('Partial failure','spec.md',b'# Mod\n\n- Attack a hostile and stop.')
        self.ws=self.projects/self.pid/'workspace';self.job=self.artifacts/self.pid/self.jid
        self.stage=self.job/'round-03';self.stage.mkdir(parents=True)
        self.catalog=acceptance.build_catalog(acceptance.read_spec(self.projects/self.pid))
        self.rid=self.catalog['requirements'][0]['id']
        self.scenario={'id':'combat','kind':'unit','task':'testCombat','requirement_ids':[self.rid],
            'description':'Approach, hit and stop.',
            'expected_tests':[{'classname':'CombatTest','name':n} for n in ('approach','hit','stop')]}
        self.xml=(b'<testsuite tests="2" failures="1"><testcase classname="CombatTest" name="approach"/>'
                  b'<testcase classname="CombatTest" name="hit"><failure message="No attributed hit"/>'
                  b'</testcase></testsuite>')

    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def result(self,case_details=False):
        directory=self.stage/'acceptance/combat';directory.mkdir(parents=True,exist_ok=True)
        xml=directory/'assertions/report-000.xml';xml.parent.mkdir();xml.write_bytes(self.xml)
        (directory/'test.log').write_text('LAB_FAIL: No attributed hit\n',encoding='utf-8')
        proof={'reports':[{'file':'acceptance/combat/assertions/report-000.xml',
            'sha256':hashlib.sha256(self.xml).hexdigest()}],
            'expected_tests':self.scenario['expected_tests'],
            'failures':[{'classname':'CombatTest','name':'hit','message':'No attributed hit'}],
            'missing_tests':[{'classname':'CombatTest','name':'stop'}]}
        if case_details:proof['test_cases']=[{'classname':'CombatTest','name':'approach','status':'TEST_PASSED'},
            {'classname':'CombatTest','name':'hit','status':'FAIL','message':'No attributed hit'}]
        return {'id':'combat','requirement_ids':[self.rid],'kind':'unit','status':'FAIL','test_status':'FAIL',
                'log':'acceptance/combat/test.log','detail':'No attributed hit','artifact_round':'round-03',
                'assertion_evidence':proof}

    @unittest.skipUnless(os.name=='posix','Descriptor-relative JUnit collection requires Linux')
    def test_real_scenario_runner_preserves_partial_case_facts(self):
        build=self.root/'build';xml=build/'test-results/test/TEST-combat.xml';xml.parent.mkdir(parents=True)
        def command(argv,workspace,log,timeout,**kwargs):
            xml.write_bytes(self.xml);log.write_text('No attributed hit')
            return {'status':'FAIL','exit_code':1,'tail':'No attributed hit'}
        request={'schema_version':1,'spec_sha256':self.catalog['spec_sha256'],'scenarios':[self.scenario]}
        with patch.object(acceptance_runner.os,'geteuid',return_value=1000):
            report=acceptance_runner.run_scenarios(request,{'gradle':['gradle']},self.ws,self.stage,
                10,command,None,build=build)
        result=report['scenarios'][0]
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual([c['status'] for c in result['assertion_evidence']['test_cases']],['TEST_PASSED','FAIL'])
        self.assertEqual(result['failure_details']['counts'],
            {'total':3,'passed':1,'failed':1,'not_executed':1,'skipped':0,'unknown':0})

    def test_feedback_export_adds_classified_checks_without_losing_logs_or_xml(self):
        result=self.result(case_details=True)
        report={'acceptance':{'source_sha256':'a'*64},'acceptance_results':{'scenarios':[result]}}
        index=verification_feedback.export_feedback(self.ws,self.job,report,3)
        self.assertEqual({f['kind'] for f in index['files']},{'checks','log','junit-0'})
        text=(self.ws/'.lab-feedback/scenario-0-checks.txt').read_text(encoding='utf-8')
        facts=json.loads(text.split('\n',1)[1])
        self.assertEqual(facts['counts']['passed'],1)
        self.assertEqual(facts['counts']['failed'],1)
        self.assertEqual(facts['counts']['not_executed'],1)
        self.assertEqual(facts['root_cause_status'],'NOT_ESTABLISHED')
        self.assertIn('No attributed hit',(self.ws/'.lab-feedback/scenario-0-log.txt').read_text())
        self.assertIn('<testsuite',(self.ws/'.lab-feedback/scenario-0-junit-0.txt').read_text())

    def test_historical_memory_round_reconstructs_case_status_from_real_xml(self):
        result=self.result()
        self.write(self.stage/'acceptance.json',{'requirements':[{'id':self.rid,'scenarios':[result]}],
            'summary':{'total':1,'failed':1}})
        before=(self.stage/'acceptance.json').read_bytes()
        response=memory_view.round_detail(self.artifacts,self.pid,self.jid,3)
        details=response['acceptance']['data']['scenarios'][0]['failure_details']
        self.assertEqual([c['status'] for c in details['checks']],['TEST_PASSED','FAIL','NOT_EXECUTED'])
        self.assertEqual((self.stage/'acceptance.json').read_bytes(),before)

    def test_dashboard_acceptance_reconstructs_old_xml_without_mutating_job_or_verdict(self):
        result=self.result()
        raw={'schema_version':1,'spec_sha256':self.catalog['spec_sha256'],
            'requirements':[{'id':self.rid,'title':'Attack','expected':'Approach, hit and stop',
                'kind':'unit','scenario_ids':['combat']}],'scenarios':[self.scenario]}
        acceptance.write_workspace_json(self.ws,'acceptance-plan.json',raw)
        plan=acceptance.load_plan(self.ws,self.catalog)
        self.assertFalse(plan['notes'])
        source=acceptance.source_fingerprint(self.ws)
        results={'schema_version':1,'spec_sha256':self.catalog['spec_sha256'],'scenarios':[result]}
        state=acceptance.build_state(self.catalog,plan,results,source,3)
        # Simulate a historical report made before test_cases/failure_details existed.
        for item in state['requirements']:
            for s in item['scenarios']:s.pop('failure_details',None)
        self.write(self.stage/'acceptance.json',state)
        self.write(self.job/'latest_acceptance.json',state)
        before=db.job_row(self.jid)
        response=TestClient(main.app).get('/api/jobs/'+self.jid+'/acceptance')
        self.assertEqual(response.status_code,200,response.text)
        scenario=response.json()['requirements'][0]['scenarios'][0]
        self.assertEqual(scenario['status'],'FAIL')
        self.assertEqual(scenario['failure_details']['counts']['passed'],1)
        self.assertEqual(scenario['failure_details']['counts']['not_executed'],1)
        self.assertEqual(db.job_row(self.jid),before)
        # A changed source may show NOT_TESTED/STALE, but the last unresolved
        # failure must remain available as explicitly historical evidence.
        self.write(self.stage/'acceptance_results.json',results)
        (self.ws/'changed.java').write_text('class Changed {}')
        response=TestClient(main.app).get('/api/jobs/'+self.jid+'/acceptance')
        self.assertEqual(response.status_code,200,response.text)
        scenario=response.json()['requirements'][0]['scenarios'][0]
        self.assertNotEqual(scenario['status'],'TEST_PASSED')
        self.assertTrue(scenario['failure_details']['historical'])
        self.assertEqual(scenario['failure_details']['source_round'],3)
        self.assertEqual(scenario['failure_details']['counts']['failed'],1)
        self.assertEqual(db.job_row(self.jid),before)
