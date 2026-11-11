"""Slow local archive operations must not inherit a short control deadline."""
import contextlib
import io
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from worker import sandbox


@unittest.skipUnless(os.name=='posix','Runner uses Unix sockets')
class RunnerArchiveWaitTests(unittest.TestCase):
    @contextlib.contextmanager
    def delayed_runner(self, payload=b'{}'):
        with tempfile.TemporaryDirectory() as temporary:
            path=str(Path(temporary)/'runner.sock')
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            listener.bind(path);listener.listen(1)
            errors=[]
            def serve():
                try:
                    connection,_=listener.accept()
                    with connection:
                        stream=connection.makefile('rb')
                        stream.readline()
                        length=0
                        while line:=stream.readline():
                            if line==b'\r\n':break
                            if line.lower().startswith(b'content-length:'):length=int(line.split(b':',1)[1])
                        if length:stream.read(length)
                        time.sleep(.08)
                        try:
                            connection.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: '+str(len(payload)).encode()+b'\r\n\r\n'+payload)
                        except BrokenPipeError:pass
                except BaseException as exc:errors.append(exc)
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            original=sandbox.RunnerConnection
            def short_control(socket_path):
                connection=original(socket_path);connection.timeout=.02;return connection
            client=sandbox.DockerSandbox();client.socket_path=path
            try:
                with patch.object(sandbox,'RunnerConnection',side_effect=short_control):yield client
            finally:
                listener.close();thread.join(timeout=2)
                self.assertFalse(thread.is_alive(),'Test runner did not stop')
                self.assertEqual(errors,[])

    def test_slow_workspace_installation_finishes_without_control_timeout(self):
        with self.delayed_runner() as client:
            self.assertEqual(client.api('PUT','/runs/owned/workspace',upload=io.BytesIO(b'archive')), {})

    def test_slow_archive_creation_and_download_preserve_all_bytes(self):
        with self.delayed_runner(b'complete archive bytes') as client:
            target=io.BytesIO()
            client.api('GET','/runs/owned/workspace',download=target)
            self.assertEqual(target.read(),b'complete archive bytes')

    def test_cleanup_waits_for_confirmed_completion(self):
        with self.delayed_runner() as client:self.assertEqual(client.api('DELETE','/runs/owned'),{})

    def test_small_control_operations_still_report_an_unresponsive_service(self):
        with self.delayed_runner() as client:
            with self.assertRaises((TimeoutError,socket.timeout)):
                client.api('GET','/runs/owned')
