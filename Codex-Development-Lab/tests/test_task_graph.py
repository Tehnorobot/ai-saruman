"""LLM-authored task relationships are checked independently of prose meaning."""
from copy import deepcopy
import hashlib
import unittest

from common.pipeline_contracts import ContractError, load_example
from common.task_graph import topological_order, validate_catalog, validate_decision, validate_graph, validate_scenarios


class TaskGraphTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_example('RequirementsCatalog')
        self.graph = load_example('TaskGraph')
        self.decision = load_example('PlannerDecision')

    def assertRejected(self, callback, code):
        with self.assertRaises(ContractError) as caught: callback()
        self.assertIn(code, [i['code'] for i in caught.exception.issues])

    def test_catalog_and_graph_examples_validate_without_mutation(self):
        before = deepcopy(self.catalog)
        self.assertIs(validate_catalog(self.catalog), self.catalog)
        self.assertIs(validate_graph(self.graph,self.catalog),self.graph)
        self.assertEqual(before,self.catalog)
        self.assertEqual(topological_order(self.graph['tasks']),['T1','T2'])

    def test_duplicate_requirement_and_condition_ids_rejected(self):
        self.catalog['requirements'].append(deepcopy(self.catalog['requirements'][0]))
        self.assertRejected(lambda:validate_catalog(self.catalog),'duplicate_id')

    def test_unknown_condition_is_not_treated_as_covered(self):
        self.catalog['requirements'][0]['condition_ids'] = ['UNKNOWN']
        self.assertRejected(lambda:validate_catalog(self.catalog),'unknown_condition')

    def test_condition_parent_links_are_bidirectional(self):
        self.catalog['conditions'][0]['requirement_id'] = 'R2'
        self.assertRejected(lambda:validate_catalog(self.catalog),'condition_owner')

    def test_missing_literal_source_rejected(self):
        self.catalog['requirements'][0]['source_refs'] = []
        self.assertRejected(lambda:validate_catalog(self.catalog),'missing_source')

    def cited_catalog(self):
        text = 'Посадить саженец.\nВырастить дерево костной мукой.\n'.encode('utf-8')
        for req in self.catalog['requirements']:
            for source in req['source_refs']:
                source['artifact']['sha256'] = hashlib.sha256(text).hexdigest()
        return text

    def test_real_hash_and_literal_line_citations_checked(self):
        text = self.cited_catalog()
        self.assertIs(validate_catalog(self.catalog,lambda artifact:text),self.catalog)
        self.catalog['requirements'][0]['source_refs'][0]['excerpt'] = 'Invented text'
        self.assertRejected(lambda:validate_catalog(self.catalog,lambda artifact:text),'source_excerpt')

    def test_outdated_source_hash_and_invalid_line_ranges_rejected(self):
        text = self.cited_catalog()
        self.assertRejected(lambda:validate_catalog(self.catalog,lambda artifact:text+b'changed'),'source_hash')
        source=self.catalog['requirements'][0]['source_refs'][0]
        source['start_line']=2
        source['end_line']=1
        self.assertRejected(lambda:validate_catalog(self.catalog),'line_range')

    def test_source_paths_confined_before_reader_invocation(self):
        for path in ('../secret','C:/secret','/secret','src\\secret','x//y','x/./y'):
            with self.subTest(path=path):
                source=self.catalog['requirements'][0]['source_refs'][0]
                source['artifact']['path']=path
                with self.assertRaises(ContractError): validate_catalog(self.catalog)

    def test_cycles_self_dependency_and_unknown_dependencies_rejected(self):
        self.graph['tasks'][0]['depends_on']=['T2']
        self.assertRejected(lambda:validate_graph(self.graph,self.catalog),'dependency_cycle')
        self.graph['tasks'][0]['depends_on']=['T1']
        self.assertRejected(lambda:validate_graph(self.graph,self.catalog),'self_dependency')
        self.graph['tasks'][0]['depends_on']=['T9']
        self.assertRejected(lambda:validate_graph(self.graph,self.catalog),'unknown_dependency')

    def test_all_conditions_must_remain_mapped(self):
        self.graph['tasks'].pop()
        self.assertRejected(lambda:validate_graph(self.graph,self.catalog),'uncovered_conditions')

    def test_no_fixed_package_or_task_count(self):
        tasks = []
        actions = []
        for i in range(25):
            task=deepcopy(self.graph['tasks'][0])
            task['id']='T'+str(i+1)
            task['requirement_ids']=['R1','R2']
            task['condition_ids']=['C1','C2']
            task['depends_on']=[] if i==0 else ['T'+str(i)]
            tasks.append(task)
            action=deepcopy(self.decision['actions'][0])
            action['id']='A'+str(i+1)
            action['task_ids']=[task['id']]
            action['depends_on_action_ids']=[] if i==0 else ['A'+str(i)]
            actions.append(action)
        self.graph['tasks']=tasks
        self.decision['actions']=actions
        self.decision['deferred']=[]
        self.assertIs(validate_decision(self.decision,self.graph,self.catalog,{}),self.decision)

    def test_dependency_must_be_verified_or_in_selected_package(self):
        self.decision['actions'][0]['task_ids']=['T2']
        self.decision['actions'][0]['condition_ids']=['C2']
        self.decision['deferred']=[]
        self.assertRejected(lambda:validate_decision(self.decision,self.graph,self.catalog,{}),'unsatisfied_dependency')
        self.assertIs(validate_decision(self.decision,self.graph,self.catalog,{'T1':'verified'}),self.decision)

    def test_task_bundle_can_cover_related_dependency_chain_in_one_action(self):
        self.decision['actions'][0]['task_ids']=['T1','T2']
        self.decision['actions'][0]['condition_ids']=['C1','C2']
        self.decision['deferred']=[]
        self.assertIs(validate_decision(self.decision,self.graph,self.catalog,{}),self.decision)

    def test_diagnosis_can_inspect_blocked_task_without_claiming_dependency_pass(self):
        action=self.decision['actions'][0]
        action.update(kind='diagnose',task_ids=['T2'],condition_ids=['C2'])
        self.decision['deferred']=[]
        self.assertIs(validate_decision(self.decision,self.graph,self.catalog,{}),self.decision)

    def test_action_dependencies_cannot_reference_later_or_unknown_actions(self):
        self.decision['actions'][0]['depends_on_action_ids']=['A9']
        self.assertRejected(lambda:validate_decision(self.decision,self.graph,self.catalog,{}),'action_order')

    def test_formal_source_scope_mismatch_rejected(self):
        self.graph['scope']['spec_revision']='f'*64
        self.assertRejected(lambda:validate_graph(self.graph,self.catalog),'stale_scope')

    def test_unknown_task_and_unrelated_condition_rejected(self):
        self.decision['actions'][0]['task_ids']=['T9']
        self.assertRejected(lambda:validate_decision(self.decision,self.graph,self.catalog,{}),'unknown_task')
        self.decision['actions'][0]['task_ids']=['T1']
        self.decision['actions'][0]['condition_ids']=['C2']
        self.assertRejected(lambda:validate_decision(self.decision,self.graph,self.catalog,{}),'unrelated_condition')

    def test_deferred_work_needs_existing_id_and_cannot_also_be_selected(self):
        self.decision['deferred'][0]['task_id']='T1'
        self.assertRejected(lambda:validate_decision(self.decision,self.graph,self.catalog,{}),'scheduled_and_deferred')

    def test_empty_wait_package_preserves_history_instead_of_fake_progress(self):
        self.decision['actions']=[]
        self.assertRejected(lambda:validate_decision(self.decision,self.graph,self.catalog,{}),'empty_package')
        self.decision['strategy']='wait'
        self.assertIs(validate_decision(self.decision,self.graph,self.catalog,{}),self.decision)

    def test_scenario_example_has_observable_junit_binding(self):
        value = load_example('ScenarioManifest')
        self.assertIs(validate_scenarios(value,self.catalog,self.graph),value)

    def test_each_checkpoint_needs_one_real_declared_test_case(self):
        value=load_example('ScenarioManifest')
        value['scenarios'][0]['execution']['checkpoint_tests'].pop()
        self.assertRejected(lambda:validate_scenarios(value,self.catalog,self.graph),'checkpoint_mapping')
        value=load_example('ScenarioManifest')
        value['scenarios'][0]['execution']['checkpoint_tests'][0]['name']='undeclared'
        self.assertRejected(lambda:validate_scenarios(value,self.catalog,self.graph),'unknown_test')

    def test_one_junit_result_cannot_claim_independent_partial_checkpoint_states(self):
        value=load_example('ScenarioManifest')
        mappings=value['scenarios'][0]['execution']['checkpoint_tests']
        mappings[1]['name']=mappings[0]['name']
        self.assertRejected(lambda:validate_scenarios(value,self.catalog,self.graph),'duplicate_id')

    def test_scenario_cannot_claim_unasserted_or_unrelated_condition(self):
        value=load_example('ScenarioManifest')
        value['scenarios'][0]['checkpoints'][1]['condition_ids']=[]
        self.assertRejected(lambda:validate_scenarios(value,self.catalog,self.graph),'unasserted_conditions')
        value=load_example('ScenarioManifest')
        value['scenarios'][0]['task_ids']=['T1']
        self.assertRejected(lambda:validate_scenarios(value,self.catalog,self.graph),'unrelated_condition')

    def test_scenario_ids_and_expected_cases_are_unique(self):
        value=load_example('ScenarioManifest')
        value['scenarios'].append(deepcopy(value['scenarios'][0]))
        self.assertRejected(lambda:validate_scenarios(value,self.catalog,self.graph),'duplicate_id')
        value=load_example('ScenarioManifest')
        expected=value['scenarios'][0]['execution']['expected_tests']
        expected.append(deepcopy(expected[0]))
        self.assertRejected(lambda:validate_scenarios(value,self.catalog,self.graph),'duplicate_id')


if __name__ == '__main__': unittest.main()
