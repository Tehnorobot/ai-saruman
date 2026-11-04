"""Bad model fixture declarations return to their owner, never fake a PASS."""
from copy import deepcopy
import json
import unittest

from common import acceptance, pipeline_memory, task_graph
from common.pipeline_contracts import ContractError, load_example
from worker import pipeline
from worker.pipeline_verification import _prepare
from tests import test_pipeline_cycle as fixtures


def conflicting_prerequisites(manifest):
    value=deepcopy(manifest)
    scenario=value['scenarios'][0]
    scenario['execution']['precondition_tests']=[
        {'classname':binding['classname'],'name':binding['name'],
         'checkpoint_ids':[binding['checkpoint_id']]}
        for binding in scenario['execution']['checkpoint_tests'][1:]]
    return value


class FixtureContractTests(unittest.TestCase):
    def setUp(self):
        self.catalog=load_example('RequirementsCatalog')
        self.graph=load_example('TaskGraph')
        self.manifest=load_example('ScenarioManifest')
        self.decision=load_example('PlannerDecision')

    def test_submission_and_runner_report_all_conflicts_with_the_same_paths(self):
        manifest=conflicting_prerequisites(self.manifest)
        original=deepcopy(manifest)
        results=[]
        for validate in (lambda:task_graph.validate_scenarios(manifest,self.catalog,self.graph),
                         lambda:_prepare(self.catalog,manifest,self.decision)):
            with self.assertRaises(ContractError) as caught:validate()
            issues=caught.exception.issues
            self.assertEqual(len(issues),2)
            self.assertTrue(all(i['code']=='product_assertion_as_fixture' for i in issues))
            self.assertTrue(all('Scenario s1' in i['expected'] for i in issues))
            self.assertIn('CP2 -> C1',issues[0]['expected'])
            self.assertIn('CP3 -> C2',issues[1]['expected'])
            self.assertTrue(all('Preserve product checkpoints' in i['instruction'] for i in issues))
            results.append(issues)
        self.assertEqual(results[0],results[1])
        self.assertEqual(manifest,original)

    def test_same_product_test_may_be_a_fixture_in_another_scenario(self):
        manifest=deepcopy(self.manifest)
        first=manifest['scenarios'][0]
        second=deepcopy(first);second['id']='independent'
        product=first['execution']['checkpoint_tests'][1]
        second['checkpoints']=[second['checkpoints'][2]]
        second['condition_ids']=['C2'];second['task_ids']=['T2']
        second['execution']['checkpoint_tests']=[second['execution']['checkpoint_tests'][2]]
        second['execution']['precondition_tests']=[
            {'classname':product['classname'],'name':product['name'],'checkpoint_ids':['CP3']}]
        manifest['scenarios'].append(second)
        self.assertIs(task_graph.validate_scenarios(manifest,self.catalog,self.graph),manifest)
        _prepare(self.catalog,manifest,self.decision,full=True)


class FixtureRecoveryTests(unittest.TestCase):
    setUp=fixtures.CycleTest.setUp
    verified=fixtures.CycleTest.verified
    execute=fixtures.CycleTest.execute

    def payloads(self):
        return [pipeline_memory.read_payload(self.out,row['record_id'])
                for row in pipeline_memory.query(self.out,page_size=100)['records']]

    def corrupt_report_manifest(self,report):
        ref=report['scenario_manifest_ref']
        value=conflicting_prerequisites(json.loads(pipeline.source_bytes(self.ws,ref)))
        report['scenario_manifest_ref']=pipeline.export_json(self.ws,ref['path'].removeprefix('.lab-pipeline/'),value)
        return value

    def prepared_development(self):
        cycle=pipeline.Cycle(self.engine,self.job,self.ws,self.out,1)
        catalog,graph=cycle.decomposition()
        decision,graph=cycle.plan(catalog,graph,{})
        _,manifest,review=cycle.code_and_review(catalog,graph,decision)
        pipeline.write_json(cycle.stage,'pipeline/session.json',
            {'scope':cycle.scope,'catalog':catalog,'graph':graph,'decision':decision})
        pipeline.write_json(cycle.stage,'pipeline/development-ready.json',
            {'source_revision':acceptance.source_fingerprint(self.ws),'manifest':manifest,'review':review})
        return cycle

    def test_conflicting_external_manifest_is_corrected_before_review_or_runner(self):
        original=self.engine.request_agent
        injected=False
        def request(*args,**kwargs):
            nonlocal injected
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='CoderReport' and not injected:
                injected=True
                logfile=args[4];events=[json.loads(line) for line in logfile.read_text().splitlines()]
                report=json.loads(events[-1]['item']['text'])
                self.corrupt_report_manifest(report)
                events[-1]['item']['text']=json.dumps(report)
                logfile.write_text('\n'.join(json.dumps(event) for event in events),encoding='utf-8')
            return result
        self.engine.request_agent=request
        self.execute()
        names=[name for name,_,_ in self.engine.requests]
        self.assertEqual(names.count('CoderReport'),2)
        self.assertNotIn('SubmissionPatch',names)  # the external file needs writer tools
        coders=[context for name,_,context in self.engine.requests if name=='CoderReport']
        feedback=coders[1]['correction']
        self.assertEqual(feedback['issue_count'],2)
        self.assertTrue(all(issue['path']=='$' for issue in feedback['issues']))
        self.assertTrue(all('scenarios-proposed.json $.scenarios[0]' in issue['expected']
                            for issue in feedback['issues']))
        self.assertEqual(names[names.index('CoderReport')+1],'CoderReport')
        self.assertEqual(self.engine.verifications,2)
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        self.assertEqual((self.ws/'existing.java').read_text(),'preserve existing source')

    def test_old_ready_checkpoint_is_archived_then_valid_coder_checkpoint_is_reused(self):
        self.prepared_development()
        stage=self.out/'round-01'
        ready=pipeline.read_json(stage,'pipeline/development-ready.json')
        ready['manifest']=conflicting_prerequisites(ready['manifest'])
        pipeline.write_json(stage,'pipeline/development-ready.json',ready)
        before=len(self.engine.requests)
        self.execute()
        # Only the final semantic review is new; the valid coder/code review is reused.
        self.assertEqual(len(self.engine.requests),before+1)
        self.assertEqual(self.engine.requests[-1][0],'ReviewResult')
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')
        rejected=[p for p in self.payloads() if p.get('checkpoint')=='development-ready']
        self.assertEqual(rejected[0]['original'],ready)
        self.assertIn('product_assertion_as_fixture',rejected[0]['error'])

    def test_missing_manifest_or_test_file_returns_to_writer_with_diagnostics(self):
        original=self.engine.request_agent
        injected=0
        def request(*args,**kwargs):
            nonlocal injected
            result=original(*args,**kwargs)
            if kwargs['output_contract']=='CoderReport' and injected<2:
                logfile=args[4];events=[json.loads(line) for line in logfile.read_text().splitlines()]
                report=json.loads(events[-1]['item']['text'])
                if injected==0:
                    report['scenario_manifest_ref']['path']='.lab-pipeline/missing-scenarios.json'
                else:
                    ref=report['scenario_manifest_ref']
                    manifest=json.loads(pipeline.source_bytes(self.ws,ref))
                    manifest['scenarios'][0]['test_file']['path']='src/gametest/MissingTest.java'
                    report['scenario_manifest_ref']=pipeline.export_json(self.ws,'scenarios-proposed.json',manifest)
                injected+=1
                events[-1]['item']['text']=json.dumps(report)
                logfile.write_text('\n'.join(json.dumps(event) for event in events),encoding='utf-8')
            return result
        self.engine.request_agent=request
        self.execute()
        coders=[context for name,_,context in self.engine.requests if name=='CoderReport']
        self.assertEqual(len(coders),3)
        self.assertIn('missing-scenarios.json',coders[1]['correction']['issues'][0]['expected'])
        self.assertIn('MissingTest.java',coders[2]['correction']['issues'][0]['expected'])
        self.assertTrue(all(context['correction']['issues'][0]['code']=='scenario_manifest' for context in coders[1:]))
        self.assertTrue(all('external scenario file' in context['correction']['instruction'] for context in coders[1:]))
        self.assertFalse(any(name=='SubmissionPatch' for name,_,_ in self.engine.requests))
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')

    def test_saved_report_and_accepted_phase_are_rechecked_with_actionable_feedback(self):
        self.prepared_development()
        stage=self.out/'round-01'
        coding=pipeline.read_json(stage,'pipeline/coding-state.json')
        self.corrupt_report_manifest(coding['report'])
        pipeline.write_json(stage,'pipeline/coding-state.json',coding)
        complete=pipeline.read_json(stage,'pipeline/coder-0/complete.json')
        complete['value']=coding['report']
        pipeline.write_json(stage,'pipeline/coder-0/complete.json',complete)
        ready=pipeline.read_json(stage,'pipeline/development-ready.json')
        ready['manifest']=json.loads(pipeline.source_bytes(self.ws,coding['report']['scenario_manifest_ref']))
        pipeline.write_json(stage,'pipeline/development-ready.json',ready)
        before=sum(name=='CoderReport' for name,_,_ in self.engine.requests)
        self.execute()
        requests=[context for name,_,context in self.engine.requests if name=='CoderReport']
        self.assertEqual(len(requests),before+1)
        feedback=requests[-1]['correction']
        self.assertEqual(feedback['issue_count'],2)
        self.assertTrue(all('scenarios-proposed.json' in i['expected'] for i in feedback['issues']))
        rejected={p.get('checkpoint') for p in self.payloads()}
        self.assertTrue({'development-ready','coder-report-0','coder-0'}<=rejected)
        self.assertEqual(self.engine.updates[-1]['phase'],'acceptance_verified')


if __name__=='__main__':unittest.main()
