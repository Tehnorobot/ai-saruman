"""Typed terminal failures cross the adapter boundary without executing partial work."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from agent import dslab
from common.provider_failures import ProviderFailure


class TypedAdapterFailures(unittest.TestCase):
    def stream(self, body, status=200):
        response = io.BytesIO(body)
        response.status = status
        connection = Mock()
        connection.getresponse.return_value = response
        with redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(ProviderFailure) as raised:
                dslab.streamed_message({}, 'synthetic-capability',
                    connection_factory=lambda **_: connection)
        connection.close.assert_called_once_with()
        return raised.exception, output.getvalue()

    @staticmethod
    def events(*values):
        return b''.join(('data: ' + json.dumps(value) + '\n\n').encode() for value in values)

    def test_http_classification_uses_upstream_status_and_retains_diagnostics(self):
        for status, code in ((400, 'invalid_request'), (401, 'authentication'),
                             (429, 'rate_limit'), (503, 'provider_unavailable')):
            detail = {'message': 'Provider rejected request', 'diagnostic': {
                'upstream_status': status, 'response_body': 'arbitrary stream interrupted prose'}}
            with self.subTest(status=status):
                failure, output = self.stream(json.dumps({'detail': detail}).encode(), 502)
                self.assertEqual(failure.metadata()['code'], code)
                self.assertEqual(failure.metadata()['http_status'], 502)
                self.assertEqual(failure.metadata()['upstream_status'], status)
                self.assertEqual(json.loads(output)['diagnostic'], detail)

    def test_http_body_prose_does_not_reclassify_gateway_failure(self):
        failure, _ = self.stream(b'{"detail":"configuration invalid request"}', 502)
        self.assertEqual(failure.code, 'provider_unavailable')

    def test_stream_failures_have_typed_interruption(self):
        streams = [b'data: {bad-json\n\n', self.events([]), self.events({'type': 'error'}),
                   self.events({'type': 'message_stop'}),
                   self.events({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}}),
                   self.events({'type': 'content_block_delta', 'index': 99,
                                'delta': {'type': 'text_delta', 'text': 'orphan'}}),
                   self.events({'type': 'content_block_start', 'index': 0,
                                'content_block': {'type': 'tool_use', 'id': 'partial',
                                                  'name': 'write_file', 'input': {}}},
                               {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}},
                               {'type': 'message_stop'})]
        for body in streams:
            with self.subTest(body=body):
                failure, _ = self.stream(body)
                self.assertEqual(failure.code, 'stream_interrupted')

    def test_read_failure_is_typed_and_does_not_expose_transport_text(self):
        response = Mock(status=200)
        response.readline.side_effect = OSError('private transport payload')
        connection = Mock()
        connection.getresponse.return_value = response
        with self.assertRaises(ProviderFailure) as raised:
            dslab.streamed_message({}, 'synthetic-capability',
                connection_factory=lambda **_: connection)
        self.assertEqual(raised.exception.code, 'stream_interrupted')
        self.assertNotIn('private transport payload', str(raised.exception))
        connection.close.assert_called_once_with()

    def test_terminal_reasons_do_not_execute_partial_tool_calls(self):
        for reason, code in (('error', 'provider_generation_error'),
                             ('max_tokens', 'output_limit'), ('unexpected', 'protocol_error')):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as root:
                send = Mock(return_value=([{'type': 'tool_use', 'id': 'partial',
                    'name': 'write_file', 'input': {'path': 'should-not-exist', 'content': 'partial'}}], reason))
                executor = dslab.Executor({'model': 'synthetic', 'dslab_capability': 'fixture'}, root, send=send)
                with patch.object(executor, 'tool') as tool, redirect_stdout(io.StringIO()):
                    with self.assertRaises(ProviderFailure) as raised:
                        executor.run('Inspect')
                self.assertEqual(raised.exception.code, code)
                tool.assert_not_called()
                send.assert_called_once()

    def test_request_and_step_limits_are_typed_without_extra_requests(self):
        for calls, turns in ((1, 2), (2, 1)):
            with self.subTest(calls=calls, turns=turns), tempfile.TemporaryDirectory() as root:
                send = Mock(return_value=([{'type': 'tool_use', 'id': 'read',
                    'name': 'list_files', 'input': {}}], 'tool_use'))
                executor = dslab.Executor({'model': 'synthetic', 'dslab_capability': 'fixture',
                                           'dslab_max_calls': calls}, root, send=send)
                with redirect_stdout(io.StringIO()), self.assertRaises(ProviderFailure) as raised:
                    executor.run('Inspect', turns=turns)
                self.assertEqual(raised.exception.code, 'request_limit')
                self.assertEqual(send.call_count, 1)

    def test_failed_turn_serializes_typed_metadata(self):
        task = {'model': 'synthetic', 'prompt': 'Inspect', 'provider': 'litellm'}
        failure = ProviderFailure('public failure', 'authentication', 502, 401)
        with patch.object(dslab.sys, 'stdin', io.StringIO(json.dumps(task))), \
                patch.object(dslab.Executor, 'run', side_effect=failure), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(dslab.main(), 1)
        failed = [json.loads(line) for line in output.getvalue().splitlines()
                  if json.loads(line)['type'] == 'turn.failed'][0]
        self.assertEqual(failed['error'], failure.metadata())
        self.assertEqual(failed['provider'], 'litellm')


if __name__ == '__main__':
    unittest.main()
