"""Public v1 contracts reject malformed shapes without guessing their meaning."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.pipeline_contracts import (
    ContractError, contract_names, feedback, load_example, load_schema, validate,
    submission_schema, load_submission_schema, check_submission_schema,
    validate_submission, format_guide,
)


class PipelineContractsTests(unittest.TestCase):
    def test_all_generated_contracts_meet_strict_provider_object_rules(self):
        from jsonschema import Draft202012Validator
        def inspect(node):
            if isinstance(node,dict):
                if node.get('type')=='object' or 'properties' in node:
                    self.assertEqual(set(node.get('required',[])),set(node.get('properties',{})))
                    self.assertEqual(len(node.get('required',[])),len(node.get('properties',{})))
                    self.assertIs(node.get('additionalProperties'),False)
                for value in node.values():inspect(value)
            elif isinstance(node,list):
                for value in node:inspect(value)
        for name in contract_names():
            with self.subTest(contract=name):
                generated=submission_schema(name)
                inspect(generated)
                Draft202012Validator(generated).validate(load_example(name))
                self.assertEqual(load_submission_schema(name),generated)

    def test_legacy_catalog_is_readable_but_new_answers_require_explicit_question_array(self):
        value=load_example('RequirementsCatalog');del value['question_resolutions']
        self.assertIs(validate('RequirementsCatalog',value),value)
        self.assertNotIn('question_resolutions',load_schema('RequirementsCatalog')['required'])
        with self.assertRaises(ContractError) as caught:validate_submission('RequirementsCatalog',value)
        self.assertIn('question_resolutions',str(caught.exception))
        value['question_resolutions']=[]
        self.assertIs(validate_submission('RequirementsCatalog',value),value)

    def test_preflight_rejects_actual_pre_fix_optional_property_regression(self):
        from jsonschema import Draft202012Validator
        broken=submission_schema('RequirementsCatalog')
        broken['required'].remove('question_resolutions')
        # Ordinary JSON Schema considers the broken provider grammar valid.
        Draft202012Validator.check_schema(broken)
        with self.assertRaisesRegex(ValueError,'required must include every property'):
            check_submission_schema(broken)

    def test_strict_generation_also_checks_nested_definitions_and_open_objects(self):
        for mutate in ('nested','open'):
            schema=submission_schema('RequirementsCatalog')
            if mutate=='nested':schema['$defs']['RequirementsCatalog']['required'].remove('question_resolutions')
            else:schema['additionalProperties']=True
            with self.subTest(case=mutate),self.assertRaises(ValueError):check_submission_schema(schema)

    def test_instructions_and_feedback_describe_the_generation_contract(self):
        guide=format_guide('RequirementsCatalog')
        self.assertIn('question_resolutions',guide['shape']['required'])
        self.assertEqual(guide['example']['question_resolutions'],[])
        error=ValueError('fixture')
        self.assertTrue(feedback(error,'RequirementsCatalog')['schema_path'].endswith('.submission.schema.json'))

    def test_stale_image_artifact_is_rejected_before_launch(self):
        stale=submission_schema('RequirementsCatalog');stale['description']='Stale generation'
        with patch('pathlib.Path.read_text',return_value=json.dumps(stale)):
            with self.assertRaisesRegex(ValueError,'Stale image-owned generation schema'):
                load_submission_schema('RequirementsCatalog')

    def test_every_runtime_example_matches_its_strict_contract(self):
        self.assertEqual(len(contract_names()), 34)
        for name in contract_names():
            with self.subTest(name=name):
                value = load_example(name)
                self.assertEqual(value['version'], 'v1')
                self.assertIs(validate(name, value), value)

    def test_unknown_names_are_not_paths(self):
        for name in ('../PlannerDecision','PlannerDecision.schema.json','Missing'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_schema(name)

    def test_schemas_are_image_owned_independent_copies(self):
        schema = load_schema('PlannerDecision')
        schema['$defs']['PlannerDecision']['additionalProperties'] = True
        self.assertFalse(load_schema('PlannerDecision')['$defs']['PlannerDecision']['additionalProperties'])
        with tempfile.TemporaryDirectory() as directory:
            with patch('pathlib.Path.cwd', return_value=Path(directory)):
                self.assertEqual(load_schema('PlannerDecision')['title'], 'PlannerDecision — runtime v1')

    def test_input_provider_values_are_not_echoed_on_type_error(self):
        value = load_example('PlannerDecision')
        value['actions'][0]['task_ids'] = 'PRIVATE_SENTINEL'
        with self.assertRaises(ContractError) as caught:
            validate('PlannerDecision', value)
        public = feedback(caught.exception, 'PlannerDecision')
        issue = next(i for i in public['issues'] if i['path'] == '$.actions[0].task_ids')
        self.assertEqual(issue['expected'], 'array<string>')
        self.assertEqual(issue['received_type'], 'string')
        self.assertEqual(json.loads(issue['example_json']), ['value'])
        self.assertNotIn('PRIVATE_SENTINEL', str(caught.exception))
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(public))

    def test_enum_and_required_feedback_list_exact_trusted_constraints(self):
        value = load_example('PlannerDecision')
        value['strategy'] = 'INVALID_SENTINEL'
        del value['actions'][0]['goal']
        with self.assertRaises(ContractError) as caught:
            validate('PlannerDecision', value)
        issues = caught.exception.issues
        self.assertTrue(any('required fields: goal' == i['expected'] for i in issues))
        enum_issue = next(i for i in issues if i['path'] == '$.strategy')
        self.assertIn('reflect', enum_issue['expected'])
        self.assertNotIn('INVALID_SENTINEL', str(caught.exception))

    def test_unknown_root_and_nested_properties_rejected(self):
        for name, field in [('PlannerDecision','actions'),('ScenarioManifest','scenarios')]:
            value = load_example(name)
            value[field][0]['claimed_pass'] = True
            with self.subTest(name=name), self.assertRaises(ContractError):
                validate(name, value)
        value = load_example('CoderReport')
        value['status'] = 'PASS'
        with self.assertRaises(ContractError): validate('CoderReport', value)

    def test_proposal_version_is_not_silently_migrated(self):
        value = load_example('PlannerDecision')
        value['version'] = 'proposal-v1'
        with self.assertRaises(ContractError): validate('PlannerDecision', value)

    def test_null_and_empty_arrays_remain_explicit(self):
        value = load_example('PlannerDecision')
        self.assertIsNone(value['replacement_graph_ref'])
        value['actions'] = None
        with self.assertRaises(ContractError): validate('PlannerDecision', value)

    def test_repeated_correction_changes_material_without_fixed_stop(self):
        exc = json.JSONDecodeError('PRIVATE_SENTINEL', '{}', 1)
        first = feedback(exc, 'PlannerDecision', 1)
        second = feedback(exc, 'PlannerDecision', 2)
        later = feedback(exc, 'PlannerDecision', 100)
        self.assertNotEqual(first['instruction'], second['instruction'])
        self.assertNotEqual(second['instruction'], later['instruction'])
        self.assertEqual(later['attempt'], 100)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(later))
        self.assertEqual(later['example']['contract'], 'PlannerDecision')

    def test_nonfinite_and_cyclic_inputs_fail_without_recursive_crash(self):
        for bad in (float('nan'), float('inf'), object()):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(ContractError):
                validate('PlannerDecision', bad)
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaises(ContractError): validate('PlannerDecision', cyclic)

    def test_execution_schema_binds_control_points_to_junit_cases(self):
        manifest = load_example('ScenarioManifest')
        execution = manifest['scenarios'][0]['execution']
        self.assertEqual(execution['kind'], 'gameplay')
        self.assertEqual(len(execution['checkpoint_tests']), 3)
        for task in ('../gradlew', 'runClientGameTest;rm', '-Dtoken=secret', 'x y'):
            value = deepcopy(manifest)
            value['scenarios'][0]['execution']['gradle_task'] = task
            with self.subTest(task=task), self.assertRaises(ContractError):
                validate('ScenarioManifest', value)

    def test_artifact_paths_are_relative_canonical_names(self):
        for path in ('/host/file', '../secret', 'a/../b', 'C:/secret', 'x\\y', 'x//y', 'x/./y', 'x/'):
            value = load_example('ProjectInput')
            value['spec_file']['path'] = path
            with self.subTest(path=path), self.assertRaises(ContractError):
                validate('ProjectInput', value)
        value = load_example('ProjectInput')
        value['spec_file']['path'] = '.lab-pipeline/input/ТЗ.md'
        self.assertIs(validate('ProjectInput', value), value)

    def test_observation_status_is_not_freeform_model_confidence(self):
        value = load_example('VerificationResult')
        value['scenario_results'][0]['checkpoints'][0]['status'] = 'LIKELY_PASS'
        with self.assertRaises(ContractError): validate('VerificationResult', value)

    def test_review_requires_condition_scope_but_citations_are_optional(self):
        value = load_example('ReviewResult')
        value['disposition'] = 'ready_for_verification'
        value['findings'] = []
        self.assertIs(validate('ReviewResult', value), value)
        value['evidence_refs'] = []
        self.assertIs(validate('ReviewResult', value),value)
        value = load_example('ReviewResult')
        value['reviewed_condition_ids'] = []
        with self.assertRaises(ContractError): validate('ReviewResult', value)


if __name__ == '__main__': unittest.main()
