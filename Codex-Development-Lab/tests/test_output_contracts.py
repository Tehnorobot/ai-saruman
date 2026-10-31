"""Trusted planner JSON shape, separate from controller evidence and scope."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import planning
from common.output_contracts import (
    MAX_ERROR_CHARS, MAX_ERRORS, PlannerStructureError,
    load_planner_schema, validate_planner_structure,
)


SCHEMAS = Path(__file__).resolve().parents[1] / 'schemas'


class PlannerOutputContractTests(unittest.TestCase):
    def minimal(self, kind='implement_and_map_requirement'):
        return {'summary': 'Implement the current requirement.',
                'action': {'kind': kind, 'target': 'REQ-example',
                           'reason': 'The requirement needs executable coverage.'}}

    def example(self, name):
        return json.loads((SCHEMAS / 'examples' / (name + '.json')).read_text(encoding='utf-8'))

    def test_all_documented_examples_validate_and_cover_all_action_kinds(self):
        kinds = set()
        for path in sorted((SCHEMAS / 'examples').glob('*.json')):
            with self.subTest(example=path.name):
                value = json.loads(path.read_text(encoding='utf-8'))
                self.assertIs(validate_planner_structure(value), value)
                kinds.add(value['action']['kind'])
        self.assertEqual(kinds, planning.KINDS)
        self.assertEqual(set(load_planner_schema()['$defs']['action']['properties']['kind']['enum']), planning.KINDS)

    def test_minimal_legacy_shape_and_optional_fields_are_not_forced_to_null(self):
        value = self.minimal()
        self.assertIs(validate_planner_structure(value), value)
        self.assertEqual(set(value), {'summary', 'action'})
        value['action']['verification_kind'] = None
        with self.assertRaises(PlannerStructureError):
            validate_planner_structure(value)

    def test_root_fields_accidentally_nested_in_action_are_rejected(self):
        for key, payload in [('hypotheses', []), ('next_steps', []), ('manual_reviews', []),
                             ('coverage_reviews', []), ('additional_actions', []),
                             ('bundle_reason', 'Related work'), ('verification_limit', 1)]:
            with self.subTest(field=key):
                value = self.minimal()
                value['action'][key] = payload
                with self.assertRaisesRegex(PlannerStructureError, r'\$\.action'):
                    validate_planner_structure(value)

    def test_missing_brace_is_not_repaired(self):
        raw = '{"summary":"Plan","action":{"kind":"diagnose_failure","target":"movement","reason":"Inspect failure","hypotheses":[]}'
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)

    def test_unknown_root_and_nested_fields_are_rejected(self):
        for path in [('implement', ['action', 'expected_progress']),
                     ('repair', ['action', 'evidence_refs', 0]),
                     ('manual', ['manual_reviews', 0, 'manual_review']),
                     ('review_coverage', ['coverage_reviews', 0, 'obligations', 0])]:
            name, components = path
            with self.subTest(path=components):
                value = self.example(name)
                row = value
                for component in components:
                    row = row[component]
                row['planner_verdict'] = 'PASS'
                with self.assertRaises(PlannerStructureError):
                    validate_planner_structure(value)
        value = self.minimal()
        value['status'] = 'PASS'
        with self.assertRaises(PlannerStructureError):
            validate_planner_structure(value)

    def test_action_kinds_types_limits_and_identifiers(self):
        changes = [({'kind': 'invented'},), ({'condition_ids': ['not-a-source-condition']},),
                   ({'verification_scenario_ids': ['UPPER_CASE']},),
                   ({'requirement_ids': 'REQ-example'},), ({'reason': ' '},),
                   ({'evidence_refs': [{}]},), ({'expected_progress': {'kind': 'repair'}},)]
        for (change,) in changes:
            with self.subTest(change=change):
                value = self.minimal()
                value['action'].update(change)
                with self.assertRaises(PlannerStructureError):
                    validate_planner_structure(value)
        for limit in (True, 0, 5, '2'):
            value = self.minimal()
            value['verification_limit'] = limit
            with self.assertRaises(PlannerStructureError):
                validate_planner_structure(value)

    def test_packages_have_strict_supplementary_actions_and_dependencies(self):
        for change in ({'kind': 'diagnose_failure'}, {'depends_on': ['x', 'x']},
                       {'depends_on': ['a', 'b', 'c', 'd']}, {'hypotheses': []}):
            value = self.example('bundle')
            value['additional_actions'][0].update(change)
            with self.subTest(change=change), self.assertRaises(PlannerStructureError):
                validate_planner_structure(value)
        value = self.example('bundle')
        value['additional_actions'] *= 4
        with self.assertRaises(PlannerStructureError):
            validate_planner_structure(value)

    def test_citation_paths_and_line_types_are_structural(self):
        for path in ('../secret', '/absolute', 'src//test.java', 'src/../test.java',
                     'C:/secret', 'src\\test.java', 'src/test.java/'):
            value = self.example('repair')
            value['action']['evidence_refs'][0]['path'] = path
            with self.subTest(path=path), self.assertRaises(PlannerStructureError):
                validate_planner_structure(value)
        for field, invalid in [('start_line', True), ('end_line', 0), ('excerpt', 'too short')]:
            value = self.example('repair')
            value['action']['evidence_refs'][0][field] = invalid
            with self.subTest(field=field), self.assertRaises(PlannerStructureError):
                validate_planner_structure(value)

    def test_trusted_schema_is_copied_and_workspace_cannot_override_it(self):
        schema = load_planner_schema()
        schema['additionalProperties'] = True
        schema['$defs']['action']['additionalProperties'] = True
        self.assertFalse(load_planner_schema()['additionalProperties'])
        self.assertFalse(load_planner_schema()['$defs']['action']['additionalProperties'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'schemas').mkdir()
            (root / 'schemas' / 'planner_output.schema.json').write_text('{}', encoding='utf-8')
            with patch('pathlib.Path.cwd', return_value=root):
                self.assertEqual(load_planner_schema(), json.loads((SCHEMAS / 'planner_output.schema.json').read_text(encoding='utf-8')))

    def test_errors_are_bounded_and_do_not_echo_untrusted_values(self):
        value = self.minimal()
        value['action'].update({str(i): 'SENSITIVE_SENTINEL' * 100 for i in range(100)})
        value['summary'] = 'SENSITIVE_SENTINEL' * 1000
        with self.assertRaises(PlannerStructureError) as caught:
            validate_planner_structure(value)
        self.assertLessEqual(len(str(caught.exception)), MAX_ERROR_CHARS)
        self.assertLessEqual(len(caught.exception.paths), MAX_ERRORS)
        self.assertNotIn('SENSITIVE_SENTINEL', str(caught.exception))

    def test_cycles_and_deep_values_fail_with_bounded_errors(self):
        value = self.minimal()
        value['loop'] = value
        with self.assertRaisesRegex(PlannerStructureError, 'complexity limit'):
            validate_planner_structure(value)

    def test_structure_never_waives_controller_scope(self):
        value = self.minimal()
        validate_planner_structure(value)
        catalog = {'requirements': [{'id': 'REQ-allowed'}]}
        plan = {'requirements': [], 'scenarios': []}
        with self.assertRaisesRegex(ValueError, 'unknown.*requirement'):
            planning.validate(value, catalog, plan, {}, 1, 'normal')

    def test_structure_never_waives_evidence_identity_or_review_semantics(self):
        value = self.example('review_coverage')
        # Shape cannot know whether an excerpt was actually observed or a
        # condition belongs to the current immutable source inventory.
        value['coverage_reviews'][0]['obligations'][0]['evidence_refs'][0]['excerpt'] = 'assertTrue(fabricatedAssertion);'
        validate_planner_structure(value)
        from common import coverage
        with tempfile.TemporaryDirectory() as directory:
            citation = value['coverage_reviews'][0]['obligations'][0]['evidence_refs'][0]
            with self.assertRaises((ValueError, OSError)):
                coverage.read_evidence(Path(directory), citation, '')


if __name__ == '__main__':
    unittest.main()
