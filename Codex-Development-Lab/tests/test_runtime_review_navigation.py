"""Deterministic archived-style observations, never model or game verdicts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from common import evidence_access,gameplay_execution,role_context,verification_feedback
from worker import pipeline
from tests import test_pipeline_cycle as cycle_fixture


class RuntimeReviewNavigationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.ws=self.root/'workspace';self.ws.mkdir()
        self.output=self.root/'job';self.stage=self.output/'round-06'
        self.sid='client-route';self.directory=self.stage/'acceptance'/self.sid
        self.directory.mkdir(parents=True)
        self.scope={'project_id':'project-one','spec_revision':'s'*64,'source_revision':'a'*64,'round':6}
        self.item={'id':self.sid,'status':'FAIL','test_status':'FAIL','exit_code':1,
            'log':f'acceptance/{self.sid}/test.log'}
        self.report={'acceptance_results':{'scenarios':[self.item]}}

    def raw(self,ref,**selection):
        return evidence_access.read_artifact(self.ws,ref,limit=100000,**selection)['content']

    def log(self):
        lines=['ordinary startup']*260
        lines.extend([f'LAB_ACCEPTANCE_BEGIN:{self.sid}',
            f'LAB_ACCEPTANCE_TEST:{self.sid}:task assigned',
            f'LAB_ACCEPTANCE_TEST:{self.sid}:task started',
            'ordinary tick','ordinary tick','java.lang.StackOverflowError'])
        text='\n'.join(lines)+'\n'
        (self.directory/'test.log').write_text(text,encoding='utf-8')
        return text

    def sources(self):
        folder=self.stage/'builds';folder.mkdir()
        path=folder/'sample-1.0-sources.jar'
        with zipfile.ZipFile(path,'w') as bundle:
            bundle.writestr('example/Tree.java','class Tree {\n  Object root() { return this.root; }\n}\n')
            bundle.writestr('../unsafe.java','DO NOT EXTRACT')
            bundle.writestr('icon.bin',b'\x00\xff')
        self.report['jars']=[{'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]
        return path

    def navigation(self):
        return verification_feedback.observation_navigation(self.ws,self.stage,self.report,self.scope)

    def test_running_skip_retains_exact_started_crash_fragments_and_frozen_sources(self):
        original_log=self.log();archive=self.sources()
        assertions=self.directory/'assertions';assertions.mkdir()
        raw=b'<testsuite><testcase name="checkpoint"><skipped message="scenario is running"/></testcase></testsuite>'
        (assertions/'report-000.xml').write_bytes(raw)
        self.item['assertion_evidence']={'reports':[{'file':f'acceptance/{self.sid}/assertions/report-000.xml',
            'sha256':hashlib.sha256(raw).hexdigest()}]}
        nav=self.navigation();row=nav['scenarios'][0]
        self.assertTrue(row['scenario_entered']);self.assertEqual(row['terminal']['status'],'command_failed')
        observed={item['preview']:item for item in row['observations']}
        for marker,number in (('task assigned',262),('task started',263),('StackOverflowError',266)):
            fact=next(item for text,item in observed.items() if marker in text)
            ref=fact['evidence_refs'][0]
            self.assertEqual(ref['selector'],{'start_line':number,'end_line':number})
            self.assertIn(marker,self.raw(ref,selector=ref['selector']))
        self.assertEqual(self.raw(row['log_ref']),original_log)
        catalog=json.loads(self.raw(nav['scenario_evidence_ref']))
        self.assertTrue(any('scenario is running' in self.raw(ref) for ref in catalog[0]['refs']))
        built=json.loads(self.raw(nav['build_artifacts_ref']))['artifacts'][0]
        member=next(item for item in built['source_members'] if item['path']=='example/Tree.java')
        (self.ws/'Tree.java').write_text('class Tree { Object root() { return root(); } }')
        self.assertIn('return this.root;',self.raw(member['ref'],start_line=2,end_line=2))
        self.assertEqual(built['archive_ref']['sha256'],hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertNotIn('../unsafe.java',[item['path'] for item in built['source_members']])
        self.assertTrue(built['inspection_errors'])
        self.assertEqual(next(item for item in built['source_members'] if item['path']=='icon.bin')['omitted'],
            'Binary member; complete bytes remain in archive_ref')
        result={'scope':self.scope,'build_status':'PASS','execution_diagnostics':[],
            'scenario_results':[{'scenario_id':self.sid,'attempted':False,'stopped_by':['old-fixture-classification']}],
            'observation_navigation':nav}
        original=deepcopy(result)
        for role in ('planner','coder','reviewer'):
            view=role_context.build(role,'repair',self.scope,{},full_input_ref={},latest_result=result)
            self.assertIn('StackOverflowError',json.dumps(view['runtime_facts']))
            self.assertIn(self.sid,view['historical_lifecycle_conflicts']['scenario_ids'])
            self.assertEqual(view['links']['build_artifacts_ref'],nav['build_artifacts_ref'])
            self.assertIn('not evidence that no action',view['runtime_evidence_policy'])
            self.assertIn('executable setup',view['fixture_implementation_policy'])
        self.assertEqual(result,original)

    def test_action_journal_is_navigable_but_never_promoted_to_pass(self):
        self.log();execution='RUN-'+'a'*32
        self.item['execution_id']=execution
        events=[{'schema_version':1,'seq':index,'scenario_id':self.sid,'execution_id':execution,
                 'stage':stage,'type':kind,'detail':detail}
            for index,(stage,kind,detail) in enumerate([
                ('scenario','started','entered'),('setup','completed','fixture prepared'),
                ('action','started','product enabled'),('action','crashed','failure during action')],1)]
        raw=('\n'.join(json.dumps(item) for item in events)+'\n').encode()
        journal=self.directory/'gameplay-journal.jsonl';journal.write_bytes(raw)
        log,legacy,traces=gameplay_execution.log_observations(self.stage,self.item['log'],self.sid)
        parsed=gameplay_execution.parse_journal(raw,self.sid,execution)
        parsed.update(file=f'acceptance/{self.sid}/gameplay-journal.jsonl',sha256=hashlib.sha256(raw).hexdigest())
        receipt={'schema_version':1,'scenario_id':self.sid,'execution_id':execution,
            'command':{'status':'FAIL','exit_code':1},
            'terminal':{'status':'command_failed','command_status':'FAIL','exit_code':1},
            'scenario_entered':True,'journal':parsed,'legacy_observations':legacy,
            'trace_observations':traces,'log':log}
        content=(json.dumps(receipt)+'\n').encode();(self.directory/'gameplay-execution.json').write_bytes(content)
        self.item['execution_lifecycle']={'file':f'acceptance/{self.sid}/gameplay-execution.json',
            'sha256':hashlib.sha256(content).hexdigest()}
        original=deepcopy(self.report);nav=self.navigation()
        action=next(item for item in nav['scenarios'][0]['observations'] if item.get('stage')=='action')
        self.assertEqual(action['event'],'started')
        ref=action['evidence_refs'][0]
        self.assertIn('product enabled',self.raw(ref,selector=ref['selector']))
        self.assertEqual(self.report,original)
        self.assertNotIn('PASS',[item.get('status') for item in nav['scenarios'][0]['observations']])
        self.assertEqual(self.raw(nav['scenarios'][0]['receipt_ref']),content.decode())

    def test_backfill_after_passed_build_preserves_canonical_and_ignores_other_run(self):
        self.log();self.sources()
        (self.stage/'acceptance_results.json').write_text(json.dumps(self.report['acceptance_results']))
        previous={'scope':self.scope,'runner_run_id':'historical-run','build_status':'PASS',
            'execution_diagnostics':[],'scenario_results':[],
            'request_ref':{'path':'round-06/pipeline-verification-request.json'}}
        pipeline.write_json(self.output,'pipeline/latest-result.json',previous)
        pipeline.write_json(self.output,'latest_verification.json',{'pipeline_result':{
            'scope':{**self.scope,'round':7},'runner_run_id':'new-run'},'checks':[{
                'label':'gradle_build','status':'FAIL','detail':'DO NOT ATTRIBUTE NEW RUN'}]})
        cycle=object.__new__(pipeline.Cycle);cycle.output=self.output;cycle.ws=self.ws
        cycle.scope=self.scope;cycle.state={};cycle.event=Mock()
        view=cycle.diagnostic_result()
        self.assertEqual(view['execution_diagnostics'],[])
        self.assertTrue(view['observation_navigation']['scenarios'][0]['scenario_entered'])
        self.assertNotIn('DO NOT ATTRIBUTE NEW RUN',json.dumps(view))
        self.assertEqual(pipeline.read_json(self.output,'pipeline/latest-result.json'),previous)
        self.assertEqual(cycle.diagnostic_result(),view)
        self.assertEqual(cycle.event.call_count,1)

    def test_invalid_receipt_never_invents_runtime_observations(self):
        self.log();self.item['execution_lifecycle']={'file':'../../other/receipt.json','sha256':'0'*64}
        nav=self.navigation()
        self.assertEqual(nav['scenarios'],[])
        self.assertEqual(nav['errors'][0]['scenario_id'],self.sid)
        catalog=json.loads(self.raw(nav['scenario_evidence_ref']))
        self.assertEqual(self.raw(catalog[0]['refs'][0]),(self.directory/'test.log').read_text())

    def test_verbose_protocol_does_not_hide_late_exception_in_context_preview(self):
        text='\n'.join(f'LAB_ACCEPTANCE_TEST:{self.sid}:observation {number}' for number in range(40))
        (self.directory/'test.log').write_text(text+'\njava.lang.StackOverflowError\n',encoding='utf-8')
        nav=self.navigation()
        view=role_context.build('reviewer','code',self.scope,{},full_input_ref={},
            latest_result={'scope':self.scope,'build_status':'PASS','observation_navigation':nav})
        self.assertTrue(view['runtime_facts_partial'])
        self.assertGreater(view['runtime_fact_count'],len(view['runtime_facts']))
        self.assertIn('StackOverflowError',json.dumps(view['runtime_facts']))
        self.assertEqual(view['runtime_facts'][0]['scenario_entry_observation'],
            'not_observed_in_available_markers')
        self.assertIn('observation 39',self.raw(nav['full_ref']))


class ExecutableFixtureReviewTests(unittest.TestCase):
    setUp=cycle_fixture.CycleTest.setUp

    def test_full_assignment_and_independent_review_keep_executable_fixture_guidance(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition();decision,graph=cycle.plan(catalog,graph,{})
        cycle.code_and_review(catalog,graph,decision)
        coder=next(context for name,_,context in self.engine.requests if name=='CoderReport')
        self.assertIn(role_context.FIXTURE_POLICY,coder['input']['assignment']['instructions'])
        request=next(context for name,_,context in self.engine.requests
            if name=='ReviewResult' and context['input']['subject']=='code')
        review=json.loads(pipeline.source_bytes(self.ws,request['input']['context_ref']))
        self.assertEqual(review['runtime_evidence_review'],role_context.RUNTIME_POLICY)
        self.assertEqual(review['executable_fixture_review'],role_context.FIXTURE_POLICY)
        self.assertIn('Product actions', ' '.join(coder['input']['assignment']['instructions']))
        self.assertIn('open_finding_ledger_ref',review)


if __name__=='__main__':unittest.main()
