"""The agent preserves complete public diagnostics from its trusted gateway."""
import io
import json
import unittest
from unittest.mock import Mock, patch

from agent import dslab


class ProviderDiagnosticClientTests(unittest.TestCase):
    def request_error(self, body, agent='main'):
        response = io.BytesIO(body)
        response.status = 502
        connection = Mock()
        connection.getresponse.return_value = response
        with patch('sys.stdout', new_callable=io.StringIO) as public:
            with self.assertRaises(RuntimeError) as raised:
                dslab.streamed_message({}, 'private-capability', agent=agent,
                    connection_factory=lambda **_: connection)
        connection.close.assert_called_once_with()
        self.assertNotIn('private-capability', str(raised.exception) + public.getvalue())
        return str(raised.exception), public.getvalue()

    def test_structured_detail_survives_old_read_and_excerpt_limits(self):
        detail = {'message': 'Provider rejected request (HTTP 400)', 'diagnostic': {
            'body': {'error': {'message': 'Invalid tool schema: ' + 'x' * 6000 + ' tail-marker'}},
            'request': {'provider': 'litellm', 'model': 'example/model',
                        'authorization': '[REDACTED]'}}}
        message, public = self.request_error(json.dumps({'detail': detail}).encode(), agent='reviewer')
        prefix = 'API request stopped (HTTP 502): '
        self.assertTrue(message.startswith(prefix))
        self.assertEqual(json.loads(message[len(prefix):]), detail)
        events = [json.loads(line) for line in public.splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['type'], 'provider.error')
        self.assertEqual(events[0]['agent_id'], 'reviewer')
        self.assertEqual(events[0]['diagnostic'], detail)

    def test_legacy_string_detail_is_complete(self):
        detail = 'Gateway rejected request: ' + 'x' * 6000 + ' tail-marker'
        message, public = self.request_error(json.dumps({'detail': detail}).encode())
        self.assertEqual(message, 'API request stopped (HTTP 502): ' + detail)
        self.assertEqual(public, '')

    def test_invalid_error_body_stays_generic(self):
        for body in (b'not JSON', b'[]', b'{"detail": null}'):
            with self.subTest(body=body):
                message, public = self.request_error(body)
                self.assertEqual(message, 'API request stopped (HTTP 502)')
                self.assertEqual(public, '')


if __name__ == '__main__':
    unittest.main()
