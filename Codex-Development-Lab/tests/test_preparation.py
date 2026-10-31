"""Soft handoff is not semantic approval and cannot bypass dependencies."""
from copy import deepcopy
import unittest
from common import preparation
from common.pipeline_contracts import load_example


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.catalog=load_example('RequirementsCatalog');self.graph=load_example('TaskGraph')
        self.handoff=load_example('PreparationHandoff');self.decision=load_example('PlannerDecision')
        for task in self.handoff['tasks']:task.update(status='ready',experiment=None)

    def check(self):
        return preparation.validate_handoff(self.handoff,self.graph,self.catalog,
            self.handoff['catalog_ref'],self.handoff['graph_ref'],[])

    def test_classify_every_task_once_and_preserve_review_refs(self):
        self.check();self.handoff['tasks'].pop()
        with self.assertRaises(ValueError):self.check()

    def test_upstream_user_block_propagates_to_dependent_tasks(self):
        self.handoff['tasks'][0]['status']='needs_user'
        self.assertEqual(set(preparation.blocked_tasks(self.handoff,self.graph,self.catalog)),{'T1','T2'})
        with self.assertRaises(ValueError):self.check()
        self.handoff['tasks'][1]['status']='needs_user';self.check()

    def test_user_ambiguity_cannot_be_guessed_by_marking_task_ready(self):
        self.catalog['open_questions']=['Conflicting product behavior?']
        self.catalog['question_resolutions']=[{'question':'Conflicting product behavior?','kind':'user_clarification',
            'status':'open','owner':'user','condition_ids':['C1'],'answer':None,'reason':'Actual contradiction',
            'source_refs':self.catalog['requirements'][0]['source_refs']}]
        with self.assertRaises(ValueError):self.check()

    def test_independent_ready_task_can_run_while_other_task_is_blocked(self):
        self.handoff['tasks'][1]['status']='external';self.check()
        self.decision['actions']=self.decision['actions'][:1]
        self.decision['actions'][0].update(task_ids=['T1'],condition_ids=['C1'])
        preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)
        self.decision['actions'][0]['task_ids']=['T2']
        with self.assertRaises(ValueError):preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)

    def test_experiment_preserves_observation_but_allows_research_and_implementation(self):
        self.handoff['tasks'][0].update(status='experiment',experiment=None)
        with self.assertRaises(ValueError):self.check()
        self.handoff['tasks'][0]['experiment']='Record actual planting result';self.check()
        self.decision['actions']=self.decision['actions'][:1]
        self.decision['actions'][0].update(task_ids=['T1'],condition_ids=['C1'],kind='implement')
        preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)
        self.decision['actions'][0]['kind']='repair'
        preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)
        self.decision['actions'][0]['kind']='diagnose'
        preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)
        following=deepcopy(self.decision['actions'][0]);following.update(id='A2',kind='implement',depends_on_action_ids=['A1'])
        self.decision['actions'].append(following)
        preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)

    def test_ready_with_open_technical_question_does_not_resolve_or_erase_it(self):
        question={'question':'Does the current API expose cancellation?','kind':'experiment','status':'open','owner':'coder',
            'condition_ids':['C1'],'answer':None,'reason':'Inspect and measure actual API behavior.',
            'source_refs':self.catalog['requirements'][0]['source_refs']}
        self.catalog['open_questions']=[question['question']];self.catalog['question_resolutions']=[question]
        before=deepcopy(self.catalog)
        self.check()
        preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)
        self.assertEqual(self.catalog,before)

    def test_experiment_permission_does_not_bypass_real_user_or_external_dependency(self):
        self.handoff['tasks'][1].update(status='experiment',experiment='Inspect actual API')
        for status in ('needs_user','external'):
            with self.subTest(status=status):
                self.handoff['tasks'][0].update(status=status,experiment=None)
                self.assertEqual(set(preparation.blocked_tasks(self.handoff,self.graph,self.catalog)),{'T1','T2'})
                for kind in ('diagnose','implement','repair'):
                    self.decision['actions'][0].update(kind=kind,task_ids=['T2'])
                    with self.assertRaises(ValueError):preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)

    def test_no_available_work_requires_wait_not_empty_coder_cycle(self):
        for task in self.handoff['tasks']:task['status']='external'
        with self.assertRaises(ValueError):preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)
        self.decision.update(strategy='wait',actions=[])
        preparation.validate_package(self.decision,self.handoff,self.graph,self.catalog)
