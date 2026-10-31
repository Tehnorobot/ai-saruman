"""Bounded native Claude history without paid requests or protocol surgery."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent import dslab


def exchange(number, text='small'):
    return [
        {'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'private reasoning', 'signature': 'signed-%d' % number},
            {'type': 'tool_use', 'id': str(number), 'name': 'read_file', 'input': {'path': 'file-%d' % number}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': str(number), 'content': text}]},
    ]


class ConversationWindowTests(unittest.TestCase):
    def stream_public_answer(self, texts):
        events = [{'type': 'content_block_start', 'index': 0, 'content_block': {
            'type': 'thinking', 'thinking': 'PRIVATE_THINKING', 'signature': 'PRIVATE_SIGNATURE'}}]
        for index, text in enumerate(texts, start=1):
            events.append({'type': 'content_block_start', 'index': index,
                           'content_block': {'type': 'text', 'text': text}})
        events.extend([{'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}},
                       {'type': 'message_stop'}])
        response = io.BytesIO(b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in events))
        response.status = 200
        connection = MagicMock()
        connection.getresponse.return_value = response
        return dslab.streamed_message({'model': 'example'}, 'fixture',
                                      connection_factory=lambda **kwargs: connection)

    def test_final_json_larger_than_tool_excerpt_is_logged_whole(self):
        text = json.dumps({'action': {'kind': 'inspect', 'reason': 'evidence ' * 10000}})
        output = io.StringIO()
        with redirect_stdout(output):
            content, reason = self.stream_public_answer([text])
        self.assertEqual(reason, 'end_turn')
        logged = [json.loads(line)['item']['text'] for line in output.getvalue().splitlines()]
        self.assertEqual(logged, [text])
        self.assertEqual(json.loads(logged[0]), json.loads(text))
        self.assertNotIn('PRIVATE_THINKING', output.getvalue())
        self.assertNotIn('PRIVATE_SIGNATURE', output.getvalue())

    def test_oversized_public_answer_fails_explicitly_without_partial_log(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(RuntimeError, 'character limit.*not truncated'):
            self.stream_public_answer(['x' * (dslab.MAX_MESSAGE_TEXT // 2),
                                       'y' * (dslab.MAX_MESSAGE_TEXT // 2)])
        self.assertEqual(output.getvalue(), '')

    def test_retained_signature_and_matching_results_remain_exact(self):
        latest = exchange(2)
        original = copy.deepcopy(latest)
        messages = [{'role': 'user', 'content': 'normative prompt'}] + exchange(1, 'x' * 10000) + latest
        with redirect_stdout(io.StringIO()):
            dslab.ConversationWindow('normative prompt', limit=4000).compact(messages, 'main')
        self.assertEqual(messages[1:], original)
        self.assertIn('normative prompt', messages[0]['content'])
        self.assertIn('file-1', messages[0]['content'])
        self.assertNotIn('private reasoning', messages[0]['content'])
        self.assertNotIn('signed-1', messages[0]['content'])

    def test_multibyte_history_is_bounded_in_bytes_and_ledger_is_bounded(self):
        window = dslab.ConversationWindow('original requirements')
        messages = [{'role': 'user', 'content': window.prompt}]
        with redirect_stdout(io.StringIO()):
            for n in range(120):
                messages.extend(exchange(n, 'тест' * 6000))
                window.compact(messages, 'reviewer')
                self.assertLessEqual(dslab.json_size(messages), dslab.MAX_HISTORY_BYTES)
        self.assertLessEqual(len(window.activity), 16)
        self.assertGreater(window.removed, 100)

    def test_original_instructions_above_window_target_remain_complete(self):
        prompt = 'x' * (dslab.MAX_HISTORY_BYTES + 1)
        requests = []
        def send(request, *args, **kwargs):
            requests.append(copy.deepcopy(request))
            return [{'type': 'text', 'text': 'Findings retained.'}], 'end_turn'
        executor = dslab.Executor({'model': 'claude-sonnet-5-5',
                                  'dslab_capability': 'fixture'}, Path('.'), send=send)
        self.assertEqual(executor.run(prompt), 'Findings retained.')
        self.assertEqual(requests[0]['messages'][0]['content'], prompt)

    def test_complete_oversized_write_pair_is_kept_without_reverting_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            requests = []
            source = '/* ' + 'ж' * 50000 + ' */'
            def send(payload, capability, **kwargs):
                requests.append(copy.deepcopy(payload))
                if len(requests) == 1:
                    return [{'type': 'tool_use', 'id': 'write1', 'name': 'write_file',
                             'input': {'path': 'Example.java', 'content': source}}], 'tool_use'
                return [{'type': 'text', 'text': 'Source saved; independent verification still needed.'}], 'end_turn'
            with redirect_stdout(io.StringIO()):
                dslab.Executor({'model': 'claude-sonnet-5-5', 'dslab_capability': 'fixture',
                                'dslab_context_bytes': 65536}, root, send=send).run('Implement')
            self.assertEqual((root / 'Example.java').read_text(encoding='utf-8'), source)
            self.assertEqual(len(requests[1]['messages']), 3)
            self.assertEqual(requests[1]['messages'][1]['content'][0]['input']['content'], source)
            self.assertEqual(requests[1]['messages'][2]['content'][0]['tool_use_id'], 'write1')

    def test_large_prompt_keeps_recent_reads_in_larger_default_window(self):
        prompt = 'p' * (41 * 1024)
        messages = [{'role': 'user', 'content': prompt}]
        window = dslab.ConversationWindow(prompt)
        with redirect_stdout(io.StringIO()):
            for n in range(128):
                messages.extend(exchange(n, 'r' * 4096))
                window.compact(messages, 'planner')
        self.assertLess(window.removed, 30)
        self.assertGreater(len(messages), 190)
        self.assertLessEqual(dslab.json_size(messages), dslab.MAX_HISTORY_BYTES)

    def test_oversized_latest_exchange_and_extra_user_instruction_survive(self):
        newest = exchange(2, 'z' * 10000)
        messages = [{'role': 'user', 'content': 'prompt'},
                    {'role': 'user', 'content': 'Read diagnostic evidence first'}] + exchange(1) + newest
        window = dslab.ConversationWindow('prompt', limit=2000, readonly=True)
        with redirect_stdout(io.StringIO()):
            window.compact(messages, 'planner')
        self.assertEqual(messages[-2:], newest)
        self.assertEqual(messages[1]['content'], 'Read diagnostic evidence first')
        self.assertNotIn('workspace notes', messages[0]['content'])

    def test_ledger_retains_precise_ranges_search_and_requirement_ids(self):
        inputs = [('read_lines', {'path': 'spec.md', 'start_line': 31, 'max_lines': 17}),
                  ('read_file', {'path': 'spec.md', 'offset': 1024, 'limit': 500}),
                  ('search_text', {'path': 'spec.md', 'text': 'required condition', 'start_line': 18,
                                   'context_lines': 3, 'max_matches': 7}),
                  ('read_requirements', {'ids': ['REQ-7', 'REQ-9']})]
        window = dslab.ConversationWindow('prompt', limit=12000, readonly=True)
        messages = [{'role': 'user', 'content': 'prompt'}]
        for n, (name, args) in enumerate(inputs):
            pair = exchange(n, 'x' * 20000)
            pair[0]['content'][1].update(name=name, input=args)
            messages.extend(pair)
        messages.extend(exchange(10))
        with redirect_stdout(io.StringIO()):
            window.compact(messages, 'planner')
        self.assertEqual([record['input'] for record in window.activity], [args for _, args in inputs])
        self.assertNotIn('workspace notes', messages[0]['content'])

    def test_context_budget_environment_and_task_override_are_bounded(self):
        with patch.dict(dslab.os.environ, {'DSLAB_CONTEXT_BYTES': '1048576'}, clear=True):
            self.assertEqual(dslab.context_limit({}), 1048576)
            self.assertEqual(dslab.context_limit({'dslab_context_bytes': 65536}), 65536)
        for value in (True, 10, 5 * 1024 * 1024, 'bad'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                dslab.context_limit({'dslab_context_bytes': value})

    def test_api_context_budget_precedence_and_legacy_compatibility(self):
        with patch.dict(dslab.os.environ, {'API_CONTEXT_BYTES': '2097152',
                                         'DSLAB_CONTEXT_BYTES': '1048576'}, clear=True):
            self.assertEqual(dslab.context_limit({}), 2097152)
            self.assertEqual(dslab.context_limit({'dslab_context_bytes': 65536}), 65536)
            self.assertEqual(dslab.context_limit({'api_context_bytes': 131072,
                                                  'dslab_context_bytes': 65536}), 131072)
        with patch.dict(dslab.os.environ, {}, clear=True):
            self.assertEqual(dslab.context_limit({}), dslab.MAX_HISTORY_BYTES)
        for value in (True, 10, 5 * 1024 * 1024, 'bad'):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'API context budget'):
                dslab.context_limit({'api_context_bytes': value})

    def test_failed_turn_identifies_selected_provider_without_transport_details(self):
        task = {'provider': 'minimax', 'model': 'MiniMax-M3.1-Flash', 'role': 'planner',
                'prompt': 'Inspect', 'dslab_max_calls': 1}
        for failure, message in ((RuntimeError('API context budget exceeded'), 'API context budget exceeded'),
                                 (ValueError('private transport body'), 'ValueError')):
            output = io.StringIO()
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(dslab.sys, 'stdin', io.StringIO(json.dumps(task))), \
                    patch.object(dslab.Executor, 'run', side_effect=failure), redirect_stdout(output):
                self.assertEqual(dslab.main(), 1)
            failed = [json.loads(line) for line in output.getvalue().splitlines()
                      if json.loads(line)['type'] == 'turn.failed'][0]
            self.assertEqual((failed['provider'], failed['model'], failed['role']),
                             ('minimax', 'MiniMax-M3.1-Flash', 'planner'))
            self.assertEqual(failed['error']['message'], message)
            self.assertNotIn('private transport body', output.getvalue())

    def test_configured_call_budget_above_100_is_honored_and_repeat_read_is_advisory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'file.txt').write_text('actual evidence', encoding='utf-8')
            requests = []
            def send(payload, capability, **kwargs):
                requests.append(copy.deepcopy(payload))
                self.assertNotIn('workspace notes', payload['system'][0]['text'])
                if len(requests) == 105:
                    return [{'type': 'text', 'text': 'done'}], 'end_turn'
                return [{'type': 'tool_use', 'id': str(len(requests)), 'name': 'read_lines',
                         'input': {'path': 'file.txt', 'start_line': 1, 'max_lines': 1}}], 'tool_use'
            with redirect_stdout(io.StringIO()):
                result = dslab.Executor({'model': 'example', 'dslab_capability': 'fixture',
                    'dslab_max_calls': 110}, root, send=send).run('Plan', agent='planner', readonly=True)
            self.assertEqual(result, 'done')
            results = [message['content'][0]['content'] for message in requests[-1]['messages']
                       if message['role'] == 'user' and isinstance(message['content'], list)]
            self.assertEqual(sum('LOCAL ADVISORY' in result for result in results), 1)
            self.assertTrue(all('actual evidence' in result for result in results))

    def test_retained_oversized_exchange_cannot_exceed_gateway_body_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'file.txt').write_text('evidence', encoding='utf-8')
            calls = []
            def send(payload, capability, **kwargs):
                calls.append(payload)
                return [{'type': 'thinking', 'thinking': 'x' * dslab.MAX_REQUEST_BYTES,
                         'signature': 'never-log-this-signature'},
                        {'type': 'tool_use', 'id': 'read1', 'name': 'read_file',
                         'input': {'path': 'file.txt'}}], 'tool_use'
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaisesRegex(RuntimeError, '8 MiB body limit'):
                dslab.Executor({'model': 'example', 'dslab_capability': 'fixture'}, root,
                               send=send).run('Plan', readonly=True)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('never-log-this-signature', output.getvalue())

    def test_many_large_reads_and_writes_do_not_regrow_unbounded_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'large.txt').write_text('r' * 30000, encoding='utf-8')
            requests = []
            def send(payload, capability, **kwargs):
                requests.append(copy.deepcopy(payload))
                self.assertLessEqual(dslab.json_size(payload['messages']), dslab.MAX_HISTORY_BYTES)
                if len(requests) == 50:
                    return [{'type': 'text', 'text': 'Done editing, NOT_TESTED gameplay.'}], 'end_turn'
                n = len(requests)
                name = 'write_file' if n % 2 else 'read_file'
                args = {'path': 'Source-%d.java' % n, 'content': 'x' * 16000} if n % 2 else {'path': 'large.txt', 'limit': 30000}
                return [{'type': 'thinking', 'thinking': 'private', 'signature': 'signature-%d' % n},
                        {'type': 'tool_use', 'id': 'tool-%d' % n, 'name': name, 'input': args}], 'tool_use'
            with redirect_stdout(io.StringIO()):
                result = dslab.Executor({'model': 'claude-sonnet-5-5', 'dslab_capability': 'fixture'}, root, send=send).run('Original requirements')
            self.assertIn('NOT_TESTED', result)
            self.assertEqual(len(list(root.glob('Source-*.java'))), 25)
            for request in requests:
                messages = request['messages']
                for index in range(1, len(messages), 2):
                    uses = {b['id'] for b in messages[index]['content'] if b['type'] == 'tool_use'}
                    results = {b['tool_use_id'] for b in messages[index + 1]['content']}
                    self.assertEqual(uses, results)
                self.assertIn('Original requirements', messages[0]['content'])

    def test_removed_errors_remain_visible_without_claiming_success(self):
        pair = exchange(1, 'missing expected source fragment')
        pair[1]['content'][0]['is_error'] = True
        messages = [{'role': 'user', 'content': 'instructions'}] + pair + exchange(2, 'x' * 5000)
        window = dslab.ConversationWindow('instructions', limit=3000)
        with redirect_stdout(io.StringIO()):
            window.compact(messages, 'main')
        self.assertIn('missing expected source fragment', messages[0]['content'])
        self.assertTrue(window.errors[0]['tool_error'])


if __name__ == '__main__':
    unittest.main()
