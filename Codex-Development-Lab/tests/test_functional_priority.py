"""Bootstrap success must lead to a real goal slice, not packaging expansion."""
import copy,json,os,unittest
from pathlib import Path
from unittest.mock import patch
from common import acceptance,acceptance_progress,coverage,functional_priority,planning
from worker import engine
from tests import test_round_context as fixture
from tests import test_planner_workflow as workflow


class FunctionalPriorityTests(unittest.TestCase):
    setUp=fixture.RoundContextTests.setUp

    def setup_goal(self):
        self.spec.write_text('# Addon\n\n## Цель\n\nСоздать клиентский аддон. Реализовать атаку через UnionClef. UnionClef устанавливается вместе с аддоном.\n',encoding='utf-8')
        self.catalog=acceptance.build_catalog(acceptance.read_spec(self.ws.parent))
        self.rid=self.catalog['requirements'][0]['id']
        self.conditions=coverage.obligations(self.catalog)[self.rid]
        self.boot={'id':'boot','kind':'gameplay','task':'acceptBoot','requirement_ids':[self.rid],
                   'expected_tests':[{'classname':'Boot','name':'loaded'}]}
        self.core={'id':'attack','kind':'gameplay','task':'acceptAttack','requirement_ids':[self.rid],
                   'expected_tests':[{'classname':'Attack','name':'attacks'}]}
        self.plan={'requirements':[{'id':self.rid,'obligation_scenarios':{self.conditions[0]['id']:['boot']}}],
                   'scenarios':[self.boot,self.core]}
        self.ledger={'scenarios':{'boot':{'last_pass':{'definition_sha256':acceptance_progress.definition_digest(self.boot),
            'source_sha256':'a'*64,'result':{'status':'TEST_PASSED','test_status':'PASS'}}}}}
        self.assignment={'current_task':{'requirement_ids':[self.rid]},'unresolved_failures':[]}
        self.previous={'checks':[{'label':'gradle_build','status':'PASS'}]}
        self.normal={'kind':'normal'}

    def select(self):
        return functional_priority.select(self.catalog,self.plan,self.assignment,self.ledger,'a'*64,self.previous,self.normal)

    def test_bootstrap_without_behavior_enforces_exact_source_goal(self):
        self.setup_goal();before=copy.deepcopy((self.catalog,self.plan,self.ledger))
        directive=self.select()
        self.assertEqual(directive['kind'],'implement_functional_goal')
        self.assertEqual(directive['required_target'],self.rid)
        self.assertEqual(directive['required_condition_ids'],[self.conditions[1]['id']])
        self.assertEqual(directive['source_condition'],self.conditions[1]['text'])
        self.assertEqual(before,(self.catalog,self.plan,self.ledger))

    def test_missing_bootstrap_ambiguous_goal_or_other_group_do_not_guess(self):
        self.setup_goal();self.ledger={'scenarios':{}}
        self.assertEqual(self.select(),self.normal)
        self.setup_goal();self.assignment['current_task']['requirement_ids']=[]
        self.assertEqual(self.select(),self.normal)
        self.setup_goal();self.catalog['requirements'][0]['section']='Other section'
        self.assertEqual(self.select(),self.normal)

    def test_nullable_progress_records_do_not_crash_or_invent_bootstrap_success(self):
        for value in (None,{'last_pass':None},{'last_pass':{'result':None}},'malformed'):
            self.setup_goal();self.ledger['scenarios']['boot']=value
            self.assertEqual(self.select(),self.normal)

    def test_mapping_without_execution_does_not_release_goal(self):
        self.setup_goal();self.plan['requirements'][0]['obligation_scenarios'][self.conditions[1]['id']]=['attack']
        self.assertEqual(self.select()['kind'],'implement_functional_goal')

    def test_executed_behavior_slice_releases_goal_for_independent_review(self):
        for test_status in ('PASS','FAIL'):
            self.setup_goal();self.plan['requirements'][0]['obligation_scenarios'][self.conditions[1]['id']]=['attack']
            self.ledger['scenarios']['attack']={'latest':{'definition_sha256':acceptance_progress.definition_digest(self.core),
                'result':{'test_status':test_status}}}
            self.assertEqual(self.select(),self.normal)

    def test_schema_build_failures_and_mandatory_routing_keep_priority(self):
        for blocker in ('schema','build','failure','directive'):
            self.setup_goal()
            if blocker=='schema':self.plan['notes']=['Invalid acceptance text']
            elif blocker=='build':self.previous['checks'].append({'label':'gradle_build','status':'FAIL'})
            elif blocker=='failure':self.assignment['unresolved_failures']=[{'id':'broken'}]
            else:self.normal={'kind':'return_to_failure','required_target':'broken'}
            self.assertEqual(self.select(),self.normal)

    def test_planner_cannot_replace_functional_goal_with_review_or_packaging(self):
        self.setup_goal();directive=self.select()
        context={'current_task':self.assignment['current_task'],'scheduling_directive':directive}
        value={'summary':'Implement first working attack','action':{
            'kind':'implement_and_map_requirement','target':self.rid,'requirement_ids':[self.rid],
            'reason':'Bootstrap completed; behavior is still missing',
            'condition_ids':directive['required_condition_ids'],
            'verification_kind':'gameplay',
            'verification_objective':'Real UnionClef task attacks one hostile entity and stops safely'}}
        self.assertEqual(planning.validate(value,self.catalog,self.plan,context,3,'normal')['action']['condition_ids'],directive['required_condition_ids'])
        for change in ({'condition_ids':[]},{'condition_ids':[self.conditions[2]['id']]},
                       {'verification_objective':'Build ZIP'}, {'verification_kind':'unit'}, {'kind':'diagnose_failure'}):
            candidate=copy.deepcopy(value);candidate['action'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                planning.validate(candidate,self.catalog,self.plan,context,3,'normal')


class RejectedCoverageRecoveryTests(unittest.TestCase):
    setUp=workflow.PlannerWorkflowTests.setUp
    response=workflow.PlannerWorkflowTests.response
    plan=workflow.PlannerWorkflowTests.plan
    complete=workflow.PlannerWorkflowTests.complete
    verify=workflow.PlannerWorkflowTests.verify

    def rejected(self):
        folder=self.stage/'planner';folder.mkdir()
        (folder/'plan-coverage-rejected.json').write_text('{}')
        (folder/'agent_attempt.json').write_text(json.dumps({'id':'old-completed-review'}))
        (folder/'codex.jsonl').write_text('old rejected review log')
        return folder

    def test_completed_rejected_review_is_not_replayed_on_resume(self):
        folder=self.rejected();transport=self.root/'planner-transport'
        (transport/'results').mkdir(parents=True)
        (transport/'results/old-completed-review.json').write_text('{}')
        with patch.dict(os.environ,{'PLANNER_TASKS':str(transport)}), \
                patch.object(engine,'request_agent',side_effect=self.response) as request:
            result,_=self.plan()
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(request.call_count,1)
        self.assertFalse((folder/'agent_attempt.json').exists())
        archives=list(folder.glob('coverage-rejected-*.jsonl'))
        self.assertEqual(len(archives),1)
        self.assertEqual(archives[0].read_text(),'old rejected review log')

    def test_unfinished_rejected_transport_is_not_removed(self):
        folder=self.rejected()
        with patch.dict(os.environ,{'PLANNER_TASKS':str(self.root/'absent-transport')}), \
                patch.object(engine,'request_agent') as request:
            result,_=self.plan()
        request.assert_not_called();self.assertEqual(result['status'],'ERROR')
        self.assertTrue((folder/'agent_attempt.json').exists())
