"""Targeted reads preserve exact selections without leaking credentials or hidden tails."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent import dslab


class TargetedDocumentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executor = dslab.Executor({}, self.root)

    def call(self, name, args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return self.executor.tool(name, args, **kwargs)

    def write(self, name, content):
        (self.root / name).write_text(content, encoding='utf-8')

    def test_default_file_read_reports_exact_continuation(self):
        self.write('large.md', 'a' * 8000 + 'TAIL')
        first = self.call('read_file', {'path': 'large.md'})
        self.assertTrue(first.startswith('a' * 8000))
        self.assertIn('TRUNCATED: next_offset=8000 total_characters=8004', first)
        self.assertEqual(self.call('read_file', {'path': 'large.md', 'offset': 8000}), 'TAIL')
        full = self.call('read_file', {'path': 'large.md', 'limit': 32000})
        self.assertEqual(full, 'a' * 8000 + 'TAIL')

    def test_line_reads_are_numbered_bounded_and_pageable(self):
        self.write('spec.md', '\n'.join('line %d' % n for n in range(1, 251)))
        first = self.call('read_lines', {'path': 'spec.md', 'start_line': 20, 'max_lines': 999})
        self.assertTrue(first.startswith('20: line 20\n'))
        self.assertIn('139: line 139', first)
        self.assertNotIn('140: line 140', first)
        self.assertIn('next_line=140', first)
        end = self.call('read_lines', {'path': 'spec.md', 'start_line': 250})
        self.assertEqual(end, '250: line 250')

    def test_search_is_literal_and_pagination_preserves_next_match(self):
        self.write('spec.md', '\n'.join(['heading', 'a.*b', 'axxb', 'middle', 'a.*b', 'tail']))
        first = self.call('search_text', {'path': 'spec.md', 'text': 'a.*b', 'max_matches': 1, 'context_lines': 1})
        self.assertIn('1: heading\n2: a.*b\n3: axxb', first)
        self.assertIn('next_start_line=5 total_matches=2', first)
        second = self.call('search_text', {'path': 'spec.md', 'text': 'a.*b', 'start_line': 5, 'context_lines': 0})
        self.assertEqual(second, '5: a.*b')
        with self.assertRaises(ValueError):
            self.call('search_text', {'path': 'spec.md', 'text': ''})

    def test_long_lines_are_explicitly_truncated_within_bound(self):
        self.write('long.md', 'match' + 'x' * 60000)
        for tool, args in [('read_lines', {'start_line': 1}), ('search_text', {'text': 'match'})]:
            with self.subTest(tool=tool):
                result = self.call(tool, {'path': 'long.md', **args})
                self.assertLessEqual(len(result), dslab.MAX_TEXT)
                self.assertIn('TRUNCATED', result)

    def test_requirements_preserve_catalog_plan_and_linked_scenarios(self):
        original = {'id': 'R1', 'source': {'text': 'Exact source. Keep this.'}, 'scenario_ids': ['S1']}
        planned = {'id': 'R1', 'expected': 'A claim', 'scenario_ids': []}
        self.write('acceptance-catalog.json', json.dumps({'spec_sha256': 'abc', 'requirements': [original, {'id': 'R2'}]}))
        linked = {'id': 'S2', 'requirement_ids': ['R1'], 'test': 'real.test'}
        self.write('acceptance-plan.json', json.dumps({'requirements': [planned], 'scenarios': [
            {'id': 'S1', 'requirement_ids': []}, linked, {'id': 'S3', 'requirement_ids': ['R2']}]}))
        result = json.loads(self.call('read_requirements', {'ids': ['R1', 'ABSENT']}, readonly=True))
        self.assertEqual(result['documents']['acceptance-catalog.json']['requirements'], [original])
        self.assertEqual(result['documents']['acceptance-plan.json']['requirements'], [planned])
        # Linking by catalog scenario IDs must work even when plan entry omits them.
        self.assertEqual([x['id'] for x in result['documents']['acceptance-plan.json']['scenarios']], ['S1', 'S2'])
        self.assertEqual(result['missing_ids'], ['ABSENT'])
        self.assertFalse(result['truncated'])

    def test_oversized_requirement_is_omitted_honestly_not_partially_rewritten(self):
        self.write('acceptance-catalog.json', json.dumps({'requirements': [{'id': 'R1', 'text': 'x' * 60000}, {'id': 'R2', 'text': 'small'}]}))
        raw = self.call('read_requirements', {'ids': ['R1', 'R2']})
        self.assertLessEqual(len(raw), dslab.MAX_TEXT)
        result = json.loads(raw)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['omitted_entry_count'], 1)
        self.assertEqual(result['omitted_entries'][0]['id'], 'R1')
        self.assertEqual(result['documents']['acceptance-catalog.json']['requirements'], [{'id': 'R2', 'text': 'small'}])

    def test_new_reads_share_credential_and_path_guards(self):
        for name in ('.env', '.env.test', 'auth.json', 'credentials'):
            self.write(name, 'private')
            for tool, extra in [('read_file', {}), ('read_lines', {'start_line': 1}), ('search_text', {'text': 'private'})]:
                with self.subTest(name=name, tool=tool), self.assertRaisesRegex(ValueError, 'Credential'):
                    self.call(tool, {'path': name, **extra}, readonly=True)
        for tool, extra in [('read_lines', {'start_line': 1}), ('search_text', {'text': 'private'})]:
            with self.assertRaises(ValueError):
                self.call(tool, {'path': '../outside', **extra})
        for ids in ([], ['R'] * 13, ['x' * 161], 'R1'):
            with self.assertRaises(ValueError):
                self.call('read_requirements', {'ids': ids})

    def test_huge_document_metadata_does_not_bypass_output_bound(self):
        self.write('acceptance-catalog.json', json.dumps({'spec_sha256': 'x' * 90000, 'requirements': [{'id': 'R1'}]}))
        raw = self.call('read_requirements', {'ids': ['R1']})
        self.assertLessEqual(len(raw), dslab.MAX_TEXT)
        self.assertIsNone(json.loads(raw)['documents']['acceptance-catalog.json']['spec_sha256'])

    def test_targeted_reads_reject_contained_symlinks(self):
        self.write('source.md', 'private')
        try:
            (self.root / 'linked.md').symlink_to(self.root / 'source.md')
        except OSError:
            self.skipTest('OS account cannot create symbolic links')
        for tool, args in [('read_lines', {'start_line': 1}), ('search_text', {'text': 'private'})]:
            with self.assertRaises(ValueError):
                self.call(tool, {'path': 'linked.md', **args})


if __name__ == '__main__':
    unittest.main()
