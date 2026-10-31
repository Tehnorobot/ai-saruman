"""Saved JSON repair reads and event diagnostics; no provider requests."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent import dslab
from common import submission_patch
from common.pipeline_contracts import load_example


def tool_use(identifier, args):
    return {'type': 'tool_use', 'id': identifier, 'name': 'read_patch_field', 'input': args}


def logged_items(output, kind):
    return [record['item'] for line in output.getvalue().splitlines()
            if (record := json.loads(line)).get('type') == 'item.completed'
            and record['item']['type'] == kind]


class PatchReaderDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.base = {'tasks': [{'task_id': 'T1-A'}], 'notes': 'keep'}
        self.issues = [{'path': '$.tasks[0].task_id', 'code': 'enum', 'expected': 'T1'}]
        self.context = submission_patch.context(self.base, self.issues, 'PreparationHandoff')
        self.context['task_index'] = [{'task_id': 'T1'}]

    def test_schema_allows_context_and_helper_reads_correct_namespace(self):
        sections = dslab.PATCH_FIELD_TOOL['input_schema']['properties']['section']['enum']
        self.assertIn('context', sections)
        saved = copy.deepcopy(self.base)
        for args, expected in [({'path': '$.tasks[0].task_id'}, 'T1-A'),
                               ({'path': '$.task_index', 'section': 'context'}, [{'task_id': 'T1'}])]:
            result = submission_patch.read_field(self.base, 'PreparationHandoff', args, self.context)
            self.assertEqual(result['value'], expected)
        format_result = submission_patch.read_field(self.base, 'PreparationHandoff',
            {'path': '$.tasks[0].task_id', 'section': 'format'}, self.context)
        self.assertEqual(format_result['value']['type'], 'string')
        self.assertEqual(self.base, saved)

    def test_invalid_paths_include_full_recovery_diagnostic(self):
        for section, path in [('value', '$.task_index'), ('context', '$.missing'),
                              ('format', '$.missing'), ('value', 'tasks[0]')]:
            with self.subTest(section=section, path=path):
                with self.assertRaises(submission_patch.PatchFieldError) as caught:
                    submission_patch.read_field(self.base, 'PreparationHandoff',
                        {'path': path, 'section': section}, self.context)
                diagnostic = caught.exception.diagnostic
                self.assertEqual(diagnostic['requested_path'], path)
                self.assertEqual(diagnostic['section'], section)
                self.assertEqual(diagnostic['allowed_patch_paths'], self.context['allowed_paths'])
                self.assertEqual(set(diagnostic['value_root_fields']), set(self.base))
                self.assertEqual(set(diagnostic['context_root_fields']), set(self.context))
                self.assertIn('context', json.dumps(diagnostic))

    def test_direct_tool_logs_actual_ranges_without_copying_extra_args(self):
        executor = dslab.Executor({'output_contract': 'SubmissionPatch'}, Path('.'))
        executor.patch_base, executor.patch_target = self.base, 'PreparationHandoff'
        executor.patch_context = self.context
        args = {'path': '$.notes', 'section': 'value', 'offset': 1, 'limit': 2,
                'private_extra': 'must not enter metadata'}
        output = io.StringIO()
        with redirect_stdout(output):
            result = json.loads(executor.tool('read_patch_field', args, call_id='read-1'))
        self.assertEqual(result['offset'], 1)
        record, = logged_items(output, 'tool_result')
        self.assertEqual({key: record[key] for key in ('tool', 'path', 'section', 'offset', 'limit', 'callID')},
            {'tool': 'read_patch_field', 'path': '$.notes', 'section': 'value',
             'offset': 1, 'limit': 2, 'callID': 'read-1'})
        self.assertNotIn('private_extra', output.getvalue())

    def test_internal_repair_logs_failed_read_and_context_recovery(self):
        requests = []
        patch_value = {'contract': 'SubmissionPatch', 'version': 'v1',
            'submission_sha256': submission_patch.fingerprint(self.base), 'changes': [
                {'path': '$.tasks[0].task_id', 'operation': 'set', 'expected_exists': True,
                 'expected_old_json': '"T1-A"', 'new_json': '"T1"'}]}

        def send(request, *_args, **_kwargs):
            requests.append(copy.deepcopy(request))
            if len(requests) == 1:
                return [tool_use('bad', {'path': '$.task_index'})], 'tool_use'
            if len(requests) == 2:
                return [tool_use('good', {'path': '$.task_index', 'section': 'context'})], 'tool_use'
            return [{'type': 'tool_use', 'id': 'submit', 'name': 'submit_patch', 'input': patch_value}], 'tool_use'

        executor = dslab.Executor({'model': 'fixture', 'dslab_capability': 'fixture'}, Path('.'), send=send)
        executor.current_prompt = 'Input JSON:\n' + json.dumps({'task_index': [{'task_id': 'T1'}]})
        output = io.StringIO()
        with redirect_stdout(output), patch('common.pipeline_contracts.feedback', return_value={'issues': self.issues}), \
                patch('common.pipeline_contracts.validate_submission'):
            result = executor.repair_submission('PreparationHandoff', self.base, ValueError('invalid'), 'main')
        self.assertEqual(result['tasks'][0]['task_id'], 'T1')
        error, = logged_items(output, 'tool_error')
        self.assertEqual(error['tool'], 'read_patch_field')
        self.assertEqual(error['callID'], 'bad')
        self.assertEqual(error['requested_path'], '$.task_index')
        self.assertIn('allowed_patch_paths', json.loads(error['text']))
        success, = logged_items(output, 'tool_result')
        self.assertEqual(success['section'], 'context')
        self.assertEqual(json.loads(success['text'])['value'], [{'task_id': 'T1'}])

    def test_run_preserves_large_diagnostic_and_logs_one_error_per_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = load_example('CoderReport')
            raw = json.dumps(base).encode()
            base_path = root / '.lab-pipeline' / 'repair-base-fixture.json'
            base_path.parent.mkdir()
            base_path.write_bytes(raw)
            context = {'base_ref': {'path': '.lab-pipeline/repair-base-fixture.json',
                                   'sha256': hashlib.sha256(raw).hexdigest()},
                'target_contract': 'CoderReport', 'submission_sha256': submission_patch.fingerprint(base),
                'allowed_paths': ['$.notes_md'], 'task_index': [{'task_id': 'T1'}]}
            context.update({'diagnostic_field_%03d' % index: index for index in range(80)})
            requests = []

            def send(request, *_args, **_kwargs):
                requests.append(copy.deepcopy(request))
                if len(requests) == 1:
                    return [tool_use('bad-run', {'path': '$.task_index'})], 'tool_use'
                raise RuntimeError('fixture complete')

            executor = dslab.Executor({'output_contract': 'SubmissionPatch', 'role': 'coder',
                'model': 'fixture', 'dslab_capability': 'fixture'}, root, send=send)
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaisesRegex(RuntimeError, 'fixture complete'):
                executor.run('Input JSON:\n' + json.dumps({'input': context}))
            self.assertEqual(executor.patch_context, context)
            error, = logged_items(output, 'tool_error')
            self.assertEqual(error['callID'], 'bad-run')
            self.assertEqual(error['section'], 'value')
            self.assertEqual(set(error['context_root_fields']), set(context))
            self.assertGreater(len(error['text']), 1000)
            tool_result = requests[1]['messages'][-1]['content'][0]
            self.assertTrue(tool_result['is_error'])
            self.assertEqual(json.loads(tool_result['content'])['context_root_fields'], error['context_root_fields'])


if __name__ == '__main__':
    unittest.main()
