"""Access the private provider gateway through a Unix socket, never host TCP."""
import http.client
import json
import os
import socket


class GatewayError(RuntimeError):
    pass


class Connection(http.client.HTTPConnection):
    def __init__(self, timeout=180):
        super().__init__('localhost', timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(os.getenv('DSLAB_GATEWAY_SOCKET', '/run/lab-provider/gateway.sock'))


def control(method, path, body=None):
    token = os.getenv('DSLAB_CONTROL_TOKEN', '')
    if not token:
        raise GatewayError('DSLab gateway control token is not configured')
    connection = Connection(timeout=5)
    try:
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(2 * 1024 * 1024 + 1)
        if response.status >= 300 or len(raw) > 2 * 1024 * 1024:
            raise GatewayError('DSLab gateway rejected request (HTTP %d)' % response.status)
        return json.loads(raw)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise GatewayError('DSLab gateway is unavailable') from exc
    finally:
        connection.close()
