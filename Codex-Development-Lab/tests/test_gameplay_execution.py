"""A crashed client retains lifecycle facts; only fresh JUnit certifies checks."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from common import gameplay_execution as execution
from tests import test_pipeline_verification as fixtures
from worker import acceptance_runner


RUN = 'RUN-' + 'a' * 32


def event(seq=1, **values):
    return {'schema_version':1,'seq':seq,'scenario_id':'s1','execution_id':RUN,
            'stage':'scenario','type':'started',**values}


def jsonl(*events):
    return b''.join((json.dumps(value)+'\n').encode() for value in events)


class GameplayJournalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.build = self.root/'build';self.build.mkdir()
        self.output = self.root/'artifacts';self.directory = self.output/'acceptance'/'s1'
        self.directory.mkdir(parents=True)
        (self.directory/'test.log').write_text('LAB_ACCEPTANCE_BEGIN:s1\n'
            'LAB_ACCEPTANCE_TEST:s1.setup:PASS:world ready\n'
            'java.lang.StackOverflowError: Unexpected error\n',encoding='utf-8')
        self.item = {'execution_id':RUN,'test_status':'FAIL','exit_code':1}

    def collect(self, raw=None):
        folder = self.build/'lab-gameplay-execution';folder.mkdir(exist_ok=True)
        if raw is not None:(folder/(RUN+'.jsonl')).write_bytes(raw)
        self.item['execution_lifecycle'] = execution.collect_execution_lifecycle(self.build,self.directory,
            self.output,'s1',RUN,{'status':'FAIL','exit_code':1})
        return execution.load_execution_lifecycle(self.output,self.item,'s1')

    def test_valid_rows_survive_incomplete_append_after_crash(self):
        raw = jsonl(event(),event(2,stage='setup',type='completed',status='PASS',detail='world ready'))
        receipt = self.collect(raw+b'{"schema_version":1')
        self.assertEqual(receipt['journal']['status'],'PARTIAL')
        self.assertEqual(len(receipt['journal']['events']),2)
        self.assertTrue(receipt['scenario_entered'])
        self.assertEqual(receipt['terminal']['status'],'command_failed')
        self.assertIn('StackOverflowError',receipt['trace_observations'][-1]['detail'])
        self.assertEqual(receipt['legacy_observations'][1]['start_line'],2)

    def test_optional_timestamp_is_diagnostic_and_sequence_still_orders_events(self):
        parsed=execution.parse_journal(jsonl(event(timestamp='2026-11-10T06:08:46Z'),
            event(2,stage='setup',type='completed',timestamp='2026-11-10T06:08:45Z')),'s1',RUN)
        self.assertEqual(parsed['status'],'VALID')
        self.assertEqual([row['seq'] for row in parsed['events']],[1,2])
        self.assertEqual(execution.parse_journal(jsonl(event(timestamp='x'*81)),'s1',RUN)['status'],'INVALID')

    def test_schema_sequence_identity_and_text_bounds_are_checked(self):
        for changed in ({'seq':2},{'schema_version':True},{'scenario_id':'other'},
                        {'execution_id':'old'},{'stage':{}},{'type':[]},{'status':{}},
                        {'detail':'x'*4001},{'unexpected':'value'}):
            with self.subTest(changed=changed):
                parsed=execution.parse_journal(jsonl(event(**changed)),'s1',RUN)
                self.assertEqual(parsed['status'],'INVALID')
                self.assertEqual(parsed['events'],[])
        parsed=execution.parse_journal(jsonl(event(),event(seq=1)),'s1',RUN)
        self.assertEqual(parsed['status'],'INVALID');self.assertEqual(len(parsed['events']),1)
        duplicate=jsonl(event()).replace(b'"seq": 1',b'"seq": 99, "seq": 1')
        self.assertEqual(execution.parse_journal(duplicate,'s1',RUN)['status'],'INVALID')
        with self.assertRaisesRegex(ValueError,'bound'):
            execution.parse_journal(b' '*(execution.MAX_JOURNAL_BYTES+1),'s1',RUN)

    def test_receipt_and_original_journal_hashes_are_rechecked(self):
        self.collect(jsonl(event()))
        journal=self.directory/'gameplay-journal.jsonl'
        journal.write_bytes(jsonl(event(type='completed')))
        with self.assertRaisesRegex(ValueError,'journal hash mismatch'):
            execution.load_execution_lifecycle(self.output,self.item,'s1')

    def test_receipt_cannot_reference_another_scenario_or_escape_root(self):
        self.collect(jsonl(event()))
        for path in ('../outside.json','acceptance/other/gameplay-execution.json'):
            with self.subTest(path=path):
                self.item['execution_lifecycle']['file']=path
                with self.assertRaisesRegex(ValueError,'outside'):
                    execution.load_execution_lifecycle(self.output,self.item,'s1')

    def test_changed_log_invalidates_its_exact_observation_selectors(self):
        self.collect()
        (self.directory/'test.log').write_text('PASS\n')
        with self.assertRaisesRegex(ValueError,'changed'):
            execution.load_execution_lifecycle(self.output,self.item,'s1')

    def test_no_journal_retains_supervisor_terminal_and_diagnostic_text(self):
        receipt=self.collect()
        self.assertEqual(receipt['journal']['status'],'ABSENT')
        self.assertTrue(receipt['scenario_entered'])
        self.assertEqual(receipt['command'],{'status':'FAIL','exit_code':1})
        self.assertIn('Execution diagnostics only',receipt['authority_note'])

    @unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,
                         'Production supervisor UID/capability check')
    def test_root_supervisor_journal_allows_client_append_without_chown_or_uid_changes(self):
        path=execution.prepare_journal(RUN)
        self.addCleanup(lambda:execution.release_journal(path))
        self.assertEqual(path.stat().st_uid,0)
        self.assertEqual(path.parent.stat().st_mode & 0o777,0o755)
        script=('import os,sys\n'
                'with open(sys.argv[1],"a") as out: out.write("durable client observation\\n")\n'
                'try: os.chmod(sys.argv[1],0)\n'
                'except PermissionError: pass\n'
                'else: raise AssertionError("client changed supervisor journal permissions")\n')
        result=subprocess.run([sys.executable,'-c',script,str(path)],user=1000,group=1000,
                              extra_groups=[],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(path.read_text(),'durable client observation\n')
        self.assertEqual(os.geteuid(),0)

    def test_oversized_journal_retains_entire_original_and_bounded_parse_diagnostic(self):
        raw=jsonl(event())+b' '*(execution.MAX_JOURNAL_BYTES+5)
        receipt=self.collect(raw)
        self.assertEqual(receipt['journal']['status'],'TRUNCATED')
        self.assertEqual(receipt['journal']['source_size'],len(raw))
        self.assertEqual(len(receipt['journal']['events']),1)
        self.assertEqual(receipt['journal']['parsed_prefix_bytes'],execution.MAX_JOURNAL_BYTES)
        self.assertEqual((self.directory/'gameplay-journal.jsonl').read_bytes(),raw)
        self.assertEqual(receipt['journal']['sha256'],hashlib.sha256(raw).hexdigest())

    @unittest.skipUnless(os.name=='posix','POSIX descriptor security')
    def test_journal_symlink_never_reads_a_foreign_file(self):
        folder=self.build/'lab-gameplay-execution';folder.mkdir()
        outside=self.root/'outside.jsonl';outside.write_bytes(jsonl(event()))
        (folder/(RUN+'.jsonl')).symlink_to(outside)
        receipt=self.collect()
        self.assertEqual(receipt['journal']['status'],'INVALID')
        self.assertEqual(receipt['journal']['events'],[])

    @unittest.skipUnless(os.name=='posix','Linux JUnit collection')
    def test_runner_pass_text_and_journal_never_certify_skipped_assertions(self):
        request={'schema_version':2,'spec_sha256':'b'*64,'scenarios':[{'id':'s1','kind':'gameplay',
            'task':'runClientGameTest','requirement_ids':['R1'],
            'expected_tests':[{'classname':'Game','name':'product'}]}]}
        def run(argv,cwd,log,timeout,**options):
            self.assertEqual(options['env']['LAB_GAMEPLAY_EXECUTION_ID'][:4],'RUN-')
            identity=options['env']['LAB_GAMEPLAY_EXECUTION_ID']
            Path(options['env']['LAB_GAMEPLAY_EXECUTION_JOURNAL']).write_bytes(
                jsonl(event(execution_id=identity),event(2,execution_id=identity,stage='checkpoint',
                    type='completed',checkpoint_id='CP1',status='PASS')))
            log.write_text('LAB_ACCEPTANCE_BEGIN:s1\nLAB_ACCEPTANCE_TEST:s1.product:PASS:claimed\n')
            xml=self.build/'test-results'/'game'/'report.xml';xml.parent.mkdir(parents=True)
            xml.write_text('<testsuite><testcase classname="Game" name="product"><skipped message="running"/>'
                           '</testcase></testsuite>')
            return {'status':'PASS','exit_code':0}
        output=self.root/'runner-output';output.mkdir()
        with patch.object(acceptance_runner.os,'geteuid',return_value=1000):
            report=acceptance_runner.run_scenarios(request,{'gradle':['gradle']},self.root,output,
                20,run,run,build=self.build,eula=True)
        row=report['scenarios'][0]
        self.assertEqual(row['status'],'NOT_TESTED')
        self.assertEqual(row['assertion_evidence']['test_cases'][0]['status'],'SKIPPED')
        receipt=execution.load_execution_lifecycle(output,row,'s1')
        self.assertEqual(receipt['journal']['events'][-1]['status'],'PASS')

    def test_thrown_supervisor_error_has_terminal_receipt_even_without_process_log(self):
        request={'schema_version':2,'spec_sha256':'b'*64,'scenarios':[{'id':'s1','kind':'gameplay',
            'task':'runClientGameTest','requirement_ids':['R1'],
            'expected_tests':[{'classname':'Game','name':'product'}]}]}
        def broken(*args,**kwargs):raise OSError('recording process could not start')
        output=self.root/'runner-output';output.mkdir()
        with patch.object(acceptance_runner,'clear_test_reports'):
            report=acceptance_runner.run_scenarios(request,{'gradle':['gradle']},self.root,output,
                20,broken,broken,build=self.build,eula=True)
        row=report['scenarios'][0]
        self.assertEqual(row['status'],'FAIL');self.assertEqual(row['test_status'],'ERROR')
        receipt=execution.load_execution_lifecycle(output,row,'s1')
        self.assertEqual(receipt['terminal']['command_status'],'ERROR')
        self.assertEqual(receipt['journal']['status'],'ABSENT')
        self.assertIn('could not start',receipt['supervisor_error'])


class GameplayCrashVerificationTests(unittest.TestCase):
    refresh_source=fixtures.PipelineVerificationTests.refresh_source
    run_verification=fixtures.PipelineVerificationTests.run_verification
    statuses=fixtures.PipelineVerificationTests.statuses

    def setUp(self):
        fixtures.PipelineVerificationTests.setUp(self)
        self.scenario=self.manifest['scenarios'][0]
        self.scenario['execution']['kind']='gameplay'
        self.scenario['execution']['precondition_tests']=[{'classname':'dev.example.PlantTests','name':'siteFound'}]
        self.sandbox.xml=(b'<testsuite>'+b''.join(('<testcase classname="dev.example.PlantTests" name="'+name+
            '"><skipped message="the scenario is running"/></testcase>').encode()
            for name in ('siteFound','saplingPlaced','treeGrown'))+b'</testsuite>')

    def install_command(self, *, failed=True):
        original=self.sandbox.verify
        def run(ws,stage,*args,**kwargs):
            report=original(ws,stage,*args,**kwargs)
            path=stage/'acceptance_results.json';value=json.loads(path.read_text())
            row=value['scenarios'][0]
            row.update(test_status='FAIL' if failed else 'PASS',status='FAIL' if failed else 'NOT_TESTED',
                exit_code=1 if failed else 0,execution_id=RUN,attempted=True,log='acceptance/s1/test.log')
            directory=stage/'acceptance'/'s1'
            (directory/'test.log').write_text('LAB_ACCEPTANCE_BEGIN:s1\n'
                'LAB_ACCEPTANCE_TEST:s1.siteFound:PASS:world ready\n'
                'java.lang.StackOverflowError: Unexpected error\n')
            build=self.root/'journal-build';(build/'lab-gameplay-execution').mkdir(parents=True,exist_ok=True)
            (build/'lab-gameplay-execution'/(RUN+'.jsonl')).write_bytes(jsonl(event(),
                event(2,stage='setup',type='completed',status='PASS',detail='world ready'),
                event(3,stage='action',type='started',detail='enable')))
            row['execution_lifecycle']=execution.collect_execution_lifecycle(build,directory,stage,'s1',RUN,
                {'status':row['test_status'],'exit_code':row['exit_code']})
            path.write_text(json.dumps(value))
            return report
        self.sandbox.verify=run

    def test_crashed_process_retains_failure_after_initial_all_skipped_junit(self):
        self.install_command()
        result,_=self.run_verification()
        row=result['scenario_results'][0]
        self.assertEqual(row['status'],'FAIL');self.assertTrue(row['attempted'])
        self.assertEqual(self.statuses(result),['NOT_EXECUTED']*3)
        self.assertEqual(row['fixture_result']['status'],'NOT_EXECUTED')
        paths=[ref['path'] for ref in result['raw_report_refs']]
        self.assertTrue(any(path.endswith('gameplay-execution.json') for path in paths))
        self.assertTrue(any(path.endswith('gameplay-journal.jsonl') for path in paths))

    def test_durable_setup_junit_pass_survives_later_process_crash(self):
        self.sandbox.xml=self.sandbox.xml.replace(
            b'name="siteFound"><skipped message="the scenario is running"/></testcase>',b'name="siteFound"/>')
        self.install_command()
        result,_=self.run_verification()
        self.assertEqual(result['scenario_results'][0]['status'],'FAIL')
        self.assertEqual(self.statuses(result),['PASS','NOT_EXECUTED','NOT_EXECUTED'])
        self.assertEqual(result['scenario_results'][0]['fixture_result']['status'],'PASS')

    def test_failed_physical_execution_is_not_waived_by_inconsistent_unstarted_flag(self):
        self.install_command()
        original=self.sandbox.verify
        def execute(ws,stage,*args,**kwargs):
            report=original(ws,stage,*args,**kwargs)
            path=stage/'acceptance_results.json';value=json.loads(path.read_text())
            value['scenarios'][0]['attempted']=False
            path.write_text(json.dumps(value))
            return report
        self.sandbox.verify=execute
        result,_=self.run_verification()
        self.assertEqual(result['scenario_results'][0]['status'],'FAIL')
        self.assertTrue(result['scenario_results'][0]['attempted'])

    def test_setup_log_claim_alone_cannot_replace_skipped_fixture_junit(self):
        self.install_command(failed=False)
        result,_=self.run_verification()
        self.assertEqual(result['scenario_results'][0]['status'],'NOT_EXECUTED')
        self.assertEqual(self.statuses(result),['NOT_EXECUTED']*3)

    def test_actual_archived_round06_begin_setup_and_crash_do_not_upgrade_junit(self):
        archive=Path(__file__).resolve().parents[1]/'artifacts'/'7aa2e3592f1640'/'69496be60a264a'/'round-06'
        if not archive.is_dir():self.skipTest('Read-only archived round06 evidence is unavailable')
        data=json.loads((archive/'acceptance_results.json').read_text(encoding='utf-8'))
        item=next(row for row in data['scenarios'] if row['id']=='full-house-from-empty-inventory')
        receipt=execution.observed_execution_lifecycle(archive,item,item['id'])
        self.assertTrue(receipt['scenario_entered'])
        self.assertEqual(receipt['terminal']['status'],'command_failed')
        self.assertTrue(any('StackOverflowError' in row['detail'] for row in receipt['trace_observations']))
        setups=[row for row in receipt['legacy_observations'] if row['event']=='TEST']
        self.assertEqual(len(setups),3)
        source=(archive/item['log']).read_text(encoding='utf-8').splitlines()
        for row in setups:self.assertEqual(source[row['start_line']-1],row['detail'])
        # Reuse the real skipped snapshot under the fixture identities, then
        # reparse it through the independent verifier with the actual exit.
        raw=(archive/item['assertion_evidence']['reports'][0]['file']).read_bytes()
        self.assertIn(b'the scenario is running',raw)
        expected=item['assertion_evidence']['expected_tests']
        self.scenario['execution']['expected_tests']=deepcopy(expected)
        self.scenario['execution']['precondition_tests']=[deepcopy(expected[0])]
        for binding,index in zip(self.scenario['execution']['checkpoint_tests'],(0,3,4)):
            binding.update(deepcopy(expected[index]))
        self.sandbox.xml=raw
        self.install_command()
        result,_=self.run_verification()
        self.assertEqual(result['scenario_results'][0]['status'],'FAIL')
        self.assertEqual(self.statuses(result),['NOT_EXECUTED']*3)


if __name__=='__main__':unittest.main()
