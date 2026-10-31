"""Private Engine client. Imported only by the broker, never by worker/agent."""
import http.client
import json
import socket


class EngineHTTPError(RuntimeError):
    """Engine HTTP failure with a status independent of its response body."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class EngineConnection(http.client.HTTPConnection):
    def __init__(self, path='/var/run/docker.sock'):
        super().__init__('localhost', timeout=60)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Engine:
    def api(self, method, path, body=None, *, raw=False):
        connection = EngineConnection()
        try:
            payload = json.dumps(body).encode() if body is not None else None
            connection.request(method, '/v1.47' + path, payload,
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read()
            if response.status >= 300:
                raise EngineHTTPError(response.status,
                                      f'Engine {response.status}: {data.decode(errors="replace")[:300]}')
            return data if raw else json.loads(data) if data else None
        finally:
            connection.close()
