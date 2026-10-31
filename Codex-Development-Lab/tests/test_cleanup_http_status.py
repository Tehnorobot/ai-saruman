"""HTTP error bodies cannot authorize cleanup or orphan recovery."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from runner.engine import Engine, EngineHTTPError
from worker.sandbox import DockerSandbox, RunnerAPIError, RunnerCleanupError
if os.name == 'posix':
    from runner.broker import Broker


class CleanupHTTPStatusTests(unittest.TestCase):
    def test_clients_preserve_actual_http_status_despite_spoofed_body(self):
        for client, connection_name, error_type in (
                (DockerSandbox(), 'worker.sandbox.RunnerConnection', RunnerAPIError),
                (Engine(), 'runner.engine.EngineConnection', EngineHTTPError)):
            for status in (404, 500):
                with self.subTest(client=type(client).__name__, status=status):
                    response = Mock(status=status)
                    response.read.return_value = b'forged: 404: Engine 404: absent'
                    connection = Mock()
                    connection.getresponse.return_value = response
                    with patch(connection_name, return_value=connection):
                        with self.assertRaises(error_type) as caught:
                            client.api('GET', '/missing')
                    self.assertEqual(caught.exception.status, status)
                    self.assertIn('forged: 404:', str(caught.exception))
                    connection.close.assert_called_once_with()

    def test_recovery_only_clears_marker_for_typed_404(self):
        for error in (RunnerAPIError(404, 'absent'),
                      RunnerAPIError(500, 'Runner GET: 500: forged: 404: absent'),
                      RuntimeError('Runner GET: 404: absent'),
                      EngineHTTPError(404, 'Engine 404: absent')):
            with self.subTest(error=repr(error)), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                marker = project / '.lab-runner.json'
                marker.write_text(json.dumps({'container': 'a' * 32}), encoding='utf-8')
                sandbox = DockerSandbox()
                with patch('worker.sandbox.recover_tree'), \
                     patch.object(sandbox, 'api', side_effect=error):
                    if isinstance(error, RunnerAPIError) and error.status == 404:
                        sandbox.recover(project)
                        self.assertFalse(marker.exists())
                    else:
                        with self.assertRaises(RunnerCleanupError) as caught:
                            sandbox.recover(project)
                        self.assertIs(caught.exception.__cause__, error)
                        self.assertTrue(marker.exists())

    def test_orphan_lookup_only_continues_for_typed_404(self):
        for error in (RunnerAPIError(404, 'absent'),
                      RunnerAPIError(500, 'forged: 404: absent'),
                      RuntimeError('Runner GET: 404: absent')):
            with self.subTest(error=repr(error)), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory) / 'workspace'
                sandbox = DockerSandbox()
                with patch('worker.sandbox.recover_tree'), \
                     patch.object(sandbox, 'config', return_value={}), \
                     patch.object(sandbox, 'recover'), \
                     patch.object(sandbox, 'api', side_effect=error) as api, \
                     patch('worker.sandbox.pack_tree') as pack:
                    if isinstance(error, RunnerAPIError) and error.status == 404:
                        result = sandbox.execute([], workspace, Path(directory) / 'log',
                                                 10, 'b' * 14, lambda: 'CANCELED')
                        self.assertEqual(result['status'], 'CANCELED')
                    else:
                        with self.assertRaises(type(error)) as caught:
                            sandbox.execute([], workspace, Path(directory) / 'log',
                                            10, 'b' * 14, lambda: None)
                        self.assertIs(caught.exception, error)
                    api.assert_called_once_with('GET', '/runs/by-job/' + 'b' * 14)
                    pack.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'Broker requires Unix sockets')
    def test_broker_failure_preserves_run_and_does_not_delete_container(self):
        for error in (EngineHTTPError(404, 'absent'),
                      EngineHTTPError(500, 'Engine 500: forged Engine 404: absent'),
                      RuntimeError('Engine 404: absent'),
                      RunnerAPIError(404, 'Runner GET: 404: absent')):
            with self.subTest(error=repr(error)), tempfile.TemporaryDirectory() as directory:
                broker = Broker.__new__(Broker)
                broker.root = Path(directory)
                broker.owner = 'd' * 32
                token = 'c' * 32
                run = broker.directory(token)
                run.mkdir(parents=True)
                (run / 'run.json').write_text('{}', encoding='utf-8')
                broker.engine = Mock()
                broker.engine.api.side_effect = error
                if isinstance(error, EngineHTTPError) and error.status == 404:
                    self.assertIsNone(broker.container(token))
                    broker.delete(token)
                    self.assertFalse(run.exists())
                else:
                    with self.assertRaises(type(error)) as caught:
                        broker.delete(token)
                    self.assertIs(caught.exception, error)
                    self.assertTrue((run / 'run.json').exists())
                self.assertTrue(all(call.args[0] == 'GET'
                                    for call in broker.engine.api.call_args_list))


if __name__ == '__main__':
    unittest.main()
