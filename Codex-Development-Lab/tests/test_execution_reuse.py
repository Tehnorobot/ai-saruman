"""Execution identity/reuse fixtures only; synthetic XML is not product proof."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.acceptance import source_fingerprint
from worker import acceptance_runner as runner
from worker import verification_plan


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'Descriptor evidence collection requires POSIX')
class ExecutionReuseTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.ws=self.root/'workspace';self.ws.mkdir()
        (self.ws/'Source.java').write_text('class Source {}')
        self.build=self.root/'build';self.xml=self.build/'test-results/test/TEST-one.xml'
        self.xml.parent.mkdir(parents=True)
        self.output=self.root/'output';self.output.mkdir()
        self.calls=[]
        self.xml_bytes=b'<testsuite><testcase classname="Example" name="alpha"/></testsuite>'
        self.exit_status='PASS'

    def scenario(self,sid,*,shared=True):
        value={'id':sid,'kind':'unit','task':'test','requirement_ids':['R1'],
               'expected_tests':[{'classname':'Example','name':'alpha'}]}
        if shared:value['execution_reuse']={'group':'suite-1','profile':'unit','initial_state':'workspace-snapshot'}
        return value

    def request(self,scenarios):
        return {'schema_version':2,'spec_sha256':'a'*64,'catalog_ids':['R1'],'scenarios':scenarios}

    def command(self,argv,cwd,log,timeout,**kwargs):
        self.calls.append(argv)
        self.xml.parent.mkdir(parents=True,exist_ok=True);self.xml.write_bytes(self.xml_bytes)
        log.write_text('fresh command output')
        return {'status':self.exit_status,'exit_code':0 if self.exit_status=='PASS' else 1,'duration_seconds':1}

    def execute(self,scenarios,**kwargs):
        with patch.object(runner.os,'geteuid',return_value=1000):
            return runner.run_scenarios(self.request(scenarios),{'gradle':['gradle']},self.ws,self.output,
                10,self.command,self.command,build=self.build,eula=True,**kwargs)

    def test_seven_opted_in_identical_scenarios_share_one_fresh_run(self):
        rows=self.execute([self.scenario('suite-'+str(i)) for i in range(7)])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(rows['execution_runs']),1)
        self.assertEqual(len({row['execution_id'] for row in rows['scenarios']}),1)
        self.assertEqual([row['measurement_reused'] for row in rows['scenarios']],[False]+[True]*6)
        self.assertTrue(all(row['status']=='TEST_PASSED' for row in rows['scenarios']))
        for row in rows['scenarios']:
            proof=row['assertion_evidence']['reports'][0]
            self.assertEqual((self.output/proof['file']).read_bytes(),self.xml_bytes)
            self.assertEqual(proof['sha256'],hashlib.sha256(self.xml_bytes).hexdigest())
            self.assertIn('/'+row['id']+'/',proof['file'])
            record=row['execution_record']
            raw=(self.output/record['file']).read_bytes()
            self.assertEqual(record['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(json.loads(raw)['execution_id'],row['execution_id'])

    def test_absent_declaration_does_not_infer_shareability_from_same_task(self):
        rows=self.execute([self.scenario('a',shared=False),self.scenario('b',shared=False)])
        self.assertEqual(len(self.calls),2)
        self.assertEqual(len({row['execution_id'] for row in rows['scenarios']}),2)

    def test_different_profile_initial_state_group_or_task_never_share(self):
        for field in ['profile','initial_state','group','task']:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as output:
                a,b=self.scenario('a'),self.scenario('b')
                if field=='task':b[field]='integrationTest'
                else:b['execution_reuse'][field]='different'
                self.output=Path(output);self.calls=[]
                self.execute([a,b]);self.assertEqual(len(self.calls),2)

    def test_inventory_change_never_shares_and_order_only_change_remaps_expected_inventory(self):
        self.xml_bytes=b'<testsuite><testcase classname="Example" name="alpha"/><testcase classname="Example" name="beta"/></testsuite>'
        a,b=self.scenario('a'),self.scenario('b')
        b['expected_tests'].append({'classname':'Example','name':'beta'})
        self.execute([a,b]);self.assertEqual(len(self.calls),2)

    def test_source_change_during_first_run_does_not_reuse_it(self):
        original=self.command
        def command(*args,**kwargs):
            result=original(*args,**kwargs)
            (self.ws/'Source.java').write_text('class Source { /* changed */ }')
            return result
        self.command=command
        self.execute([self.scenario('a'),self.scenario('b')]);self.assertEqual(len(self.calls),2)

    def test_parent_environment_change_does_not_reuse_a_different_environment(self):
        original=self.command
        def command(*args,**kwargs):
            result=original(*args,**kwargs)
            os.environ['LAB_EXECUTION_REUSE_FIXTURE']='changed'
            return result
        self.command=command
        with patch.dict(os.environ,{'LAB_EXECUTION_REUSE_FIXTURE':'original'}):
            self.execute([self.scenario('a'),self.scenario('b')])
        self.assertEqual(len(self.calls),2)

    def test_distinct_intervening_physical_run_invalidates_old_initial_state(self):
        a,b,c=self.scenario('a'),self.scenario('b'),self.scenario('c')
        b['execution_reuse']['initial_state']='other'
        self.execute([a,b,c]);self.assertEqual(len(self.calls),3)

    def test_failed_command_and_real_failed_case_remain_failed_in_all_projections(self):
        self.exit_status='FAIL'
        self.xml_bytes=b'<testsuite failures="1"><testcase classname="Example" name="alpha"><failure message="actual failure"/></testcase></testsuite>'
        rows=self.execute([self.scenario('a'),self.scenario('b')])
        self.assertEqual(len(self.calls),1)
        self.assertEqual([row['status'] for row in rows['scenarios']],['FAIL','FAIL'])
        self.assertTrue(all(row['assertion_evidence']['failures'][0]['message']=='actual failure' for row in rows['scenarios']))

    def test_gameplay_has_distinct_commands_and_cannot_opt_in_to_reuse(self):
        a,b=self.scenario('a',shared=False),self.scenario('b',shared=False)
        for row in (a,b):row.update(kind='gameplay',task='runClientGameTest')
        rows=self.execute([a,b]);self.assertEqual(len(self.calls),2)
        self.assertNotEqual(rows['scenarios'][0]['execution_id'],rows['scenarios'][1]['execution_id'])
        a['execution_reuse']={'group':'g','profile':'p','initial_state':'s'}
        path=self.root/'request.json';path.write_text(json.dumps(self.request([a])))
        with self.assertRaisesRegex(ValueError,'Gameplay'):runner.load_request(path)

    def test_global_build_fresh_exact_test_inventory_is_one_shared_physical_measurement(self):
        self.xml.write_bytes(self.xml_bytes);(self.output/'build.log').write_text('global build output')
        scenarios=[self.scenario('suite-'+str(i)) for i in range(7)]
        build={'status':'PASS','exit_code':0,'argv':['gradle','build'],'log':'build.log'}
        seed=verification_plan._build_snapshot(self.request(scenarios),{},build,self.ws,self.output,self.build,
                                               source_revision=source_fingerprint(self.ws))
        rows=self.execute(scenarios,build_execution=seed)
        self.assertEqual(self.calls,[])
        self.assertEqual(len(rows['execution_runs']),1)
        self.assertEqual(rows['execution_runs'][0]['argv'],['gradle','build'])
        self.assertTrue(all(row['measurement_reused'] for row in rows['scenarios']))
        self.assertTrue(all(row['execution_id']==seed['execution_id'] for row in rows['scenarios']))
        self.assertEqual((self.output/seed['reports'][0]['file']).read_bytes(),self.xml_bytes)

    def test_global_snapshot_retains_failed_junit_before_later_scenario_cleanup(self):
        self.xml_bytes=b'<testsuite failures="1"><testcase classname="Example" name="alpha"><failure message="global failure"/></testcase></testsuite>'
        self.xml.write_bytes(self.xml_bytes);(self.output/'build.log').write_text('failed build')
        build={'status':'FAIL','exit_code':1,'argv':['gradle','build'],'log':'build.log'}
        seed=verification_plan._build_snapshot(self.request([self.scenario('a')]),{},build,self.ws,self.output,self.build)
        with patch.object(runner.os,'geteuid',return_value=1000):runner.clear_test_reports(self.build)
        self.assertEqual((self.output/seed['reports'][0]['file']).read_bytes(),self.xml_bytes)
        self.assertEqual(seed['result']['status'],'FAIL')
        self.assertTrue(seed['fresh'])

    def test_stale_or_partial_global_inventory_cannot_skip_a_fresh_scenario(self):
        self.xml.write_bytes(b'<testsuite><testcase classname="Example" name="old"/></testsuite>')
        (self.output/'build.log').write_text('global output')
        scenarios=[self.scenario('a')]
        build={'status':'PASS','exit_code':0,'argv':['gradle','build'],'log':'build.log'}
        seed=verification_plan._build_snapshot(self.request(scenarios),{},build,self.ws,self.output,self.build)
        rows=self.execute(scenarios,build_execution=seed)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(rows['scenarios'][0]['status'],'TEST_PASSED')
        self.assertFalse(rows['scenarios'][0]['measurement_reused'])

    def test_changed_shared_snapshot_cannot_create_passing_projection(self):
        original=runner._execution_key;calls=0
        def key(*args,**kwargs):
            nonlocal calls
            result=original(*args,**kwargs);calls+=1
            if calls==2:
                (self.output/'acceptance/a/assertions/report-000.xml').write_text('<testsuite/>')
            return result
        with patch.object(runner,'_execution_key',side_effect=key):
            rows=self.execute([self.scenario('a'),self.scenario('b')])
        self.assertEqual(rows['scenarios'][0]['status'],'TEST_PASSED')
        self.assertEqual(rows['scenarios'][1]['status'],'NOT_TESTED')
        self.assertIn('hash mismatch',rows['scenarios'][1]['detail'])

    def test_init_disables_only_test_result_reuse_and_preserves_compile_cache_flags(self):
        original={'gradle':['gradle','--build-cache'],'argv':['gradle','--build-cache','build']}
        directory=self.root/'init';directory.mkdir()
        plan=verification_plan._fresh_test_plan(original,directory)
        text=(directory/'fresh-tests.gradle').read_text()
        self.assertIn('withType(org.gradle.api.tasks.testing.Test)',text)
        self.assertIn('upToDateWhen { false }',text)
        self.assertIn('doNotCacheIf',text)
        self.assertIn('--build-cache',plan['argv'])
        self.assertEqual(plan['argv'][-1],'build')
        self.assertEqual(original['argv'],['gradle','--build-cache','build'])


if __name__=='__main__':unittest.main()
