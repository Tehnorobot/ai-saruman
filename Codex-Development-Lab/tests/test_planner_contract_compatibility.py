"""Planner shape preserves controller formats; provider translation keeps local refs."""
import copy
import tempfile
import unittest
from pathlib import Path

from common import coverage, planning
from common.output_contracts import (
    PlannerStructureError, load_planner_schema, validate_planner_structure,
)
from provider_gateway.main import openai_request


class PlannerContractCompatibilityTests(unittest.TestCase):
    def proposal(self, kind='diagnose_failure', target='build.gradle.kts / verification'):
        return {'summary': 'Investigate the observed infrastructure failure.',
                'action': {'kind': kind, 'target': target,
                           'reason': 'Inspect the existing failure and preserve passing checks.'}}

    def catalog(self):
        return {'spec_sha256': 'a' * 64, 'requirements': [{'id': 'REQ-example'}]}

    def test_controller_valid_infrastructure_and_manual_targets_remain_valid(self):
        for kind in ('diagnose_failure', 'repair_verification_failure',
                     'repair_acceptance_plan', 'await_user_review'):
            with self.subTest(kind=kind):
                value = self.proposal(kind)
                decision = planning.validate(value, self.catalog(),
                                             {'requirements': [], 'scenarios': []}, {}, 1, 'normal')
                self.assertEqual(decision['action']['target'], value['action']['target'])
                self.assertIs(validate_planner_structure(value), value)

    def test_primary_and_supplementary_targets_use_bounded_nonblank_text(self):
        for target in ('source / fixture: diagnosis', 'Проверить сборку', 'x' * 128):
            value = self.proposal(target=target)
            value['additional_actions'] = [{'kind': 'implement_and_map_requirement',
                                            'target': target, 'reason': 'Implement selected work.'}]
            with self.subTest(target=target):
                self.assertIs(validate_planner_structure(value), value)
        for target in ('', '   ', 'x' * 129, None, 12):
            for supplementary in (False, True):
                value = self.proposal()
                action = value['action']
                if supplementary:
                    action = {'kind': 'implement_and_map_requirement', 'reason': 'Implement selected work.'}
                    value['additional_actions'] = [action]
                action['target'] = target
                with self.subTest(target=target, supplementary=supplementary):
                    with self.assertRaises(PlannerStructureError):
                        validate_planner_structure(value)

    def test_review_can_enumerate_more_than_1024_immutable_source_conditions(self):
        catalog = self.catalog()
        catalog['requirements'][0]['source'] = {
            'text': '\n'.join('Required behavior %d.' % index for index in range(1025))}
        inventory = coverage.obligations(catalog)['REQ-example']
        self.assertEqual(len(inventory), 1025)
        review = {'requirement_id': 'REQ-example', 'obligations': [
            {'id': condition['id'], 'verdict': 'missing',
             'reason': 'No executable assertion covers this source condition.'}
            for condition in inventory]}
        value = self.proposal('review_coverage', 'REQ-example')
        value['coverage_reviews'] = [review]
        validate_planner_structure(value)
        plan = {'requirements': [], 'scenarios': []}
        planning.validate(value, catalog, plan, {}, 1, 'normal')
        with tempfile.TemporaryDirectory() as directory:
            result = coverage.apply_reviews(Path(directory), Path(directory), catalog, plan,
                                            'b' * 64, [review], round_number=1)
        self.assertEqual(result['accepted'], ['REQ-example'])
        self.assertEqual(result['rejected'], [])

    def test_scenario_reference_count_is_owned_by_mapping_semantics(self):
        # Shape does not invent a 128-item limit. This does not approve a mapping:
        # acceptance.load_plan and coverage.apply_reviews still check its limits
        # and exact identities independently.
        value = self.proposal('review_coverage', 'REQ-example')
        value['coverage_reviews'] = [{'requirement_id': 'REQ-example', 'obligations': [{
            'id': 'condition-' + 'a' * 16, 'verdict': 'covered',
            'reason': 'Executable assertions cover every clause of this condition.',
            'scenario_ids': ['scenario-%d' % index for index in range(129)]}]}]
        self.assertIs(validate_planner_structure(value), value)
        with tempfile.TemporaryDirectory() as directory:
            result = coverage.apply_reviews(Path(directory), Path(directory), self.catalog(),
                                            {'requirements': [], 'scenarios': []}, 'b' * 64,
                                            value['coverage_reviews'], round_number=1)
        self.assertEqual(result['accepted'], [])
        self.assertEqual(len(result['rejected']), 1)

    def test_openai_translation_preserves_full_schema_and_resolvable_local_refs(self):
        schema = load_planner_schema()
        original = copy.deepcopy(schema)
        request = {'messages': [{'role': 'user', 'content': 'Plan selected work.'}],
                   'max_tokens': 8192,
                   'tools': [{'name': 'submit_planner_plan', 'description': 'Submit a proposal.',
                              'input_schema': schema}]}
        translated = openai_request(request, {'model': 'fixture-model'})
        forwarded = translated['tools'][0]['function']['parameters']
        self.assertEqual(forwarded, original)
        self.assertEqual(request['tools'][0]['input_schema'], original)
        self.assertIn('$defs', forwarded)
        references = []

        def walk(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    reference = value['$ref']
                    references.append(reference)
                    self.assertTrue(reference.startswith('#/$defs/'))
                    resolved = forwarded
                    for component in reference[2:].split('/'):
                        resolved = resolved[component.replace('~1', '/').replace('~0', '~')]
                    self.assertIsInstance(resolved, dict)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(forwarded)
        self.assertTrue(references)


if __name__ == '__main__':
    unittest.main()
