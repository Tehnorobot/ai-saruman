"""Independent regressions for duplicate-resolution compare-and-set repairs."""
from copy import deepcopy
import json
import unittest

from common import agent_protocol, submission_patch
from common.pipeline_contracts import ContractError, load_example, validate_submission
from common.task_graph import validate_catalog


def catalog_fixture():
    catalog = load_example('RequirementsCatalog')
    catalog['open_questions'] = ['Which implementation?', 'Which fallback?']
    source = deepcopy(catalog['requirements'][0]['source_refs'])
    def row(question, answer, conditions):
        return {'question': question, 'kind': 'implementation_decision',
                'status': 'resolved', 'owner': 'planner', 'condition_ids': conditions,
                'answer': answer, 'reason': 'Recorded engineering decision.',
                'source_refs': deepcopy(source)}
    catalog['question_resolutions'] = [
        row(catalog['open_questions'][0], 'First implementation.', ['C1']),
        row(catalog['open_questions'][0], 'Alternative implementation.', ['C2']),
        row(catalog['open_questions'][1], 'Keep the fallback.', ['C2'])]
    return catalog


def patch_for(base, changes):
    entries = []
    for path, old, new in changes:
        entries.append({'path': path, 'operation': 'set', 'expected_exists': True,
                        'expected_old_json': json.dumps(old), 'new_json': json.dumps(new)})
    return {'contract': 'SubmissionPatch', 'version': 'v1',
            'submission_sha256': submission_patch.fingerprint(base), 'changes': entries}


def merged_rows(base):
    rows = deepcopy(base['question_resolutions'])
    rows[0]['condition_ids'] = ['C1', 'C2']
    rows[0]['answer'] = 'Explicitly reconciled implementation decision.'
    rows[0]['reason'] = 'Reconciled the recorded alternatives using the source.'
    return [rows[0], rows[2]]


class DuplicateResolutionRepairTests(unittest.TestCase):
    def issues(self, catalog):
        with self.assertRaises(ContractError) as caught:
            validate_catalog(catalog)
        return caught.exception.issues

    def test_duplicate_issue_names_the_array_and_every_group_index(self):
        base = catalog_fixture()
        base['question_resolutions'].append(deepcopy(base['question_resolutions'][2]))
        base['question_resolutions'].append(deepcopy(base['question_resolutions'][0]))
        saved = deepcopy(base)
        issues = self.issues(base)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['path'], '$.question_resolutions')
        self.assertEqual(issues[0]['code'], 'duplicate_question_resolution')
        self.assertIn('[0, 1, 4]', issues[0]['expected'])
        self.assertIn('[2, 3]', issues[0]['expected'])
        self.assertEqual(base, saved)

    def test_canonical_and_wire_array_repairs_preserve_other_fields_and_original(self):
        canonical = catalog_fixture()
        for base in (canonical, agent_protocol.to_wire(canonical)):
            with self.subTest(contract=base['contract']):
                saved = deepcopy(base)
                rows = merged_rows(base)
                rows[0]['source_refs'] = deepcopy(base['requirements'][1]['source_refs'])
                result = submission_patch.apply(base, patch_for(base, [
                    ('$.question_resolutions', base['question_resolutions'], rows)
                ]), ['$.question_resolutions'])
                self.assertEqual(base, saved)
                expected = deepcopy(saved)
                expected['question_resolutions'] = rows
                self.assertEqual(result, expected)
                self.assertEqual(result['question_resolutions'][1], saved['question_resolutions'][2])
                self.assertEqual(result['requirements'], saved['requirements'])
                validate_submission(result['contract'], result)
                if result['contract'] == 'RequirementsCatalog':
                    validate_catalog(result)

    def test_array_and_independent_link_error_can_be_repaired_in_one_patch(self):
        base = catalog_fixture()
        old_owner = base['conditions'][0]['requirement_id']
        base['conditions'][0]['requirement_id'] = 'UNKNOWN'
        issues = self.issues(base)
        self.assertIn('$.conditions[0].requirement_id', [i['path'] for i in issues])
        result = submission_patch.apply(base, patch_for(base, [
            ('$.question_resolutions', base['question_resolutions'], merged_rows(base)),
            ('$.conditions[0].requirement_id', 'UNKNOWN', old_owner)
        ]), submission_patch.allowed_paths(issues))
        validate_catalog(result)
        self.assertEqual(base['conditions'][0]['requirement_id'], 'UNKNOWN')

    def test_legacy_unique_question_link_expands_only_to_affected_array(self):
        base = catalog_fixture()
        issues = [{'path': '$.question_resolutions[1].question', 'code': 'question_link',
                   'expected': 'one unique exact question from open_questions'}]
        context = submission_patch.context(base, issues, 'RequirementsCatalog')
        self.assertEqual(context['allowed_paths'], ['$.question_resolutions'])
        self.assertEqual(context['duplicate_resolution_groups'], [
            {'question': base['open_questions'][0], 'indices': [0, 1]}])
        self.assertEqual(context['submission_sha256'], submission_patch.fingerprint(base))
        for path, old, new in (('$.open_questions', base['open_questions'], []),
                               ('$.requirements', base['requirements'], []),
                               ('$', base, {})):
            with self.subTest(path=path), self.assertRaises(ValueError):
                submission_patch.apply(base, patch_for(base, [(path, old, new)]), context['allowed_paths'])

    def test_current_unknown_question_error_keeps_child_scope(self):
        base = catalog_fixture()
        base['question_resolutions'] = [deepcopy(base['question_resolutions'][0])]
        base['question_resolutions'][0]['question'] = 'Unknown question'
        issues = self.issues(base)
        self.assertEqual([i['code'] for i in issues], ['question_link'])
        self.assertEqual(submission_patch.allowed_paths(issues), ['$.question_resolutions[0].question'])

    def test_array_repair_cannot_drop_either_distinct_valid_question(self):
        base = catalog_fixture()
        saved = deepcopy(base)
        rows = merged_rows(base)
        for replacement in ([], rows[:1], rows[1:]):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                submission_patch.apply(base, patch_for(base, [
                    ('$.question_resolutions', base['question_resolutions'], replacement)
                ]), ['$.question_resolutions'])
            self.assertEqual(base, saved)

    def test_descendant_edit_cannot_erase_a_distinct_valid_question(self):
        base = catalog_fixture()
        for path, old, new in (
            ('$.question_resolutions[2].question', base['open_questions'][1], base['open_questions'][0]),
            ('$.question_resolutions[2]', base['question_resolutions'][2], base['question_resolutions'][0])):
            with self.subTest(path=path), self.assertRaises(ValueError):
                submission_patch.apply(base, patch_for(base, [(path, old, new)]), ['$.question_resolutions'])

    def test_merge_preserves_union_of_valid_condition_links(self):
        base = catalog_fixture()
        rows = merged_rows(base)
        rows[0]['condition_ids'] = ['C1']
        with self.assertRaises(ValueError):
            submission_patch.apply(base, patch_for(base, [
                ('$.question_resolutions', base['question_resolutions'], rows)
            ]), ['$.question_resolutions'])
        base['question_resolutions'][1]['condition_ids'].append('UNKNOWN')
        result = submission_patch.apply(base, patch_for(base, [
            ('$.question_resolutions', base['question_resolutions'], merged_rows(base))
        ]), ['$.question_resolutions'])
        validate_catalog(result)

    def test_unknown_links_may_be_removed_without_losing_valid_links(self):
        base = catalog_fixture()
        unknown = deepcopy(base['question_resolutions'][0])
        unknown['question'] = 'Unknown historical link'
        base['question_resolutions'].append(unknown)
        result = submission_patch.apply(base, patch_for(base, [
            ('$.question_resolutions', base['question_resolutions'], merged_rows(base))
        ]), ['$.question_resolutions'])
        validate_catalog(result)

    def test_array_repair_retains_compare_and_set_and_index_removal_checks(self):
        base = catalog_fixture()
        saved = deepcopy(base)
        for variant in ('digest', 'old', 'indexed_remove'):
            patch = patch_for(base, [('$.question_resolutions', base['question_resolutions'], merged_rows(base))])
            if variant == 'digest':
                patch['submission_sha256'] = '0' * 64
            elif variant == 'old':
                patch['changes'][0]['expected_old_json'] = '[]'
            else:
                patch = patch_for(base, [('$.question_resolutions[1]', base['question_resolutions'][1], None)])
                patch['changes'][0]['operation'] = 'remove'
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                submission_patch.apply(base, patch, ['$.question_resolutions'])
            self.assertEqual(base, saved)
