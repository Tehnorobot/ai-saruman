"""Narrow Unix-socket build service with no host-directory mount API.

Only this service has the Engine socket. The worker can send a project archive
and a command; it cannot select images, host paths, mounts or security settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import quote

from common.safe_archive import pack_tree, unpack_tree
from runner.engine import Engine, EngineHTTPError
from runner.policy import RUNNER_NETWORK, container_config, identifier, validate_request

MAX_ARCHIVE = 21 * 1024 ** 3
MAX_RUNTIME = 7200


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class Broker:
    def __init__(self, root=Path('/state'), engine=None):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.engine = engine or Engine()
        owner_file = self.root / 'owner.json'
        if not owner_file.exists():
            write_json(owner_file, {'id': uuid.uuid4().hex})
        self.owner = identifier(json.loads(owner_file.read_text())['id'], 32)
        (self.root / 'runs').mkdir(mode=0o700, exist_ok=True)
        info = self.engine.api('GET', '/containers/' + quote(socket.gethostname(), safe='') + '/json')
        mounts = [m for m in info['Mounts'] if m['Destination'] == '/state' and m['Type'] == 'volume']
        if len(mounts) != 1:
            raise RuntimeError('Broker state must be a private named Docker volume')
        self.volume = mounts[0]['Name']
        network=self.engine.api('GET','/networks/'+quote(RUNNER_NETWORK,safe=''))
        if (network.get('Internal') is not True or network.get('Driver')!='bridge'
                or network.get('Options',{}).get('com.docker.network.bridge.gateway_mode_ipv4')!='isolated'
                or network.get('Options',{}).get('com.docker.network.bridge.gateway_mode_ipv6')!='isolated'):
            raise RuntimeError('Runner requires an internal isolated bridge without a host gateway')
        self.network=identifier(network.get('Id'),64)
        # Pin the local image ID for this broker lifetime. Requests never select it.
        image = os.getenv('LAB_RUNNER_IMAGE', 'codex-lab-worker:local')
        self.image = self.engine.api('GET', '/images/' + quote(image, safe='') + '/json')['Id']
        self.recover_startup()

    def directory(self, token):
        return self.root / 'runs' / identifier(token, 32)

    def prepare_cache(self, project):
        """Only expose a project's cache leaf; never traverse its task-owned contents.

        Ancestors stay root-owned so a job mounted at /cache cannot replace the
        leaf entry between validation and Engine's volume-subpath resolution.
        """
        identifier(project)
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            raise RuntimeError('Project cache requires Linux descriptor path checks')
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        descriptor=os.open('/',flags)
        try:
            for component in self.root.absolute().parts[1:]:
                child=os.open(component,flags,dir_fd=descriptor)
                os.close(descriptor);descriptor=child
            for component in ('projects',project,'cache'):
                try:os.mkdir(component,mode=0o700,dir_fd=descriptor)
                except FileExistsError:pass
                child=os.open(component,flags,dir_fd=descriptor)
                os.close(descriptor);descriptor=child
                leaf=component=='cache'
                os.fchown(descriptor,1000 if leaf else 0,1000 if leaf else 0)
                os.fchmod(descriptor,0o755 if leaf else 0o700)
            for component in ('project-gradle','build','native'):
                try:os.mkdir(component,mode=0o755,dir_fd=descriptor)
                except FileExistsError:pass
                child=os.open(component,flags,dir_fd=descriptor)
                try:
                    os.fchown(child,1000,1000)
                    os.fchmod(child,0o755)
                finally:os.close(child)
        finally:os.close(descriptor)
        return self.root/'projects'/project/'cache'

    def load(self, token):
        path = self.directory(token) / 'run.json'
        if not path.is_file():
            raise FileNotFoundError('Unknown run')
        return json.loads(path.read_text())

    def save(self, token, data):
        write_json(self.directory(token) / 'run.json', data)

    def container(self, token):
        self.load(token)  # A worker-provided token is never an Engine container ID.
        name = 'codex-lab-run-' + self.owner + '-' + token
        try:
            info = self.engine.api('GET', '/containers/' + name + '/json')
        except EngineHTTPError as exc:
            if exc.status == 404:
                return None
            raise
        labels = info.get('Config', {}).get('Labels', {})
        if labels.get('codex-lab.broker') != self.owner or labels.get('codex-lab.run') != token:
            raise RuntimeError('Container ownership mismatch')
        return info

    def by_job(self, job):
        identifier(job)
        matches = [p.parent.name for p in (self.root / 'runs').glob('*/run.json')
                   if json.loads(p.read_text())['request']['job'] == job]
        if not matches:
            raise FileNotFoundError('Unknown job')
        if len(matches) != 1:
            raise RuntimeError('Multiple unfinished runs for one job')
        return self.status(matches[0])

    def create(self, payload):
        request = validate_request(payload)
        for pending in (self.root/'runs').glob('*/run.json'):
            if json.loads(pending.read_text())['request']['project']==request['project']:
                raise ValueError('Project already has an unfinished run; recover it first')
        try:
            self.by_job(request['job'])
        except FileNotFoundError:
            pass
        else:
            raise ValueError('Job already has an unfinished run')
        if len(list((self.root / 'runs').iterdir())) >= 4:
            raise ValueError('Runner capacity reached; recover previous runs first')
        self.prepare_cache(request['project'])
        token = uuid.uuid4().hex
        directory = self.directory(token)
        directory.mkdir(mode=0o700)
        for folder in ('workspace', 'output'):
            path = directory / folder
            trusted_output=folder=='output' and request.get('verification') is True
            path.mkdir(mode=0o700 if trusted_output else 0o755)
            os.chown(path,0 if trusted_output else 1000,0 if trusted_output else 1000)
        self.save(token, {'request': request, 'uploaded': False, 'started': None})
        return {'Id': token}

    def upload(self, token, stream):
        data = self.load(token)
        if data['uploaded'] or data['started'] is not None or self.container(token):
            raise ValueError('Workspace is already installed')
        target = self.directory(token) / 'workspace'
        # A failed upload is retried only as a fresh run, never over a partial tree.
        unpack_tree(stream, target)
        for parent, dirs, files in os.walk(target, followlinks=False):
            os.chown(parent, 1000, 1000, follow_symlinks=False)
            for name in dirs + files:
                os.chown(Path(parent) / name, 1000, 1000, follow_symlinks=False)
        data['uploaded'] = True
        self.save(token, data)

    def start(self, token):
        data = self.load(token)
        if not data['uploaded']:
            raise ValueError('Upload a workspace before starting')
        existing = self.container(token)
        if data['started'] is not None:
            raise ValueError('Run was already started')
        if existing:
            raise ValueError('Run needs recovery before restart')
        self.prepare_cache(data['request']['project'])
        # Commit intent before asking Engine. Recovery can always find by name,
        # including a crash after create but before the response is delivered.
        data['started'] = time.time()
        self.save(token, data)
        config = container_config(self.image, self.volume, token, data['request'], self.owner)
        config['HostConfig']['NetworkMode']=self.network
        name = 'codex-lab-run-' + self.owner + '-' + token
        result = self.engine.api('POST', '/containers/create?name=' + name, config)
        self.engine.api('POST', '/containers/' + result['Id'] + '/start')

    def status(self, token):
        data = self.load(token)
        info = self.container(token)
        return {'Id': token, 'Uploaded': data['uploaded'], 'Started': data['started'] is not None,
                'Config': {'Labels': {'codex-lab.role': 'project-test',
                                     'codex-lab.project': data['request']['project']}},
                'State': info['State'] if info else {'Running': False, 'ExitCode': None}}

    def stop(self, token):
        info = self.container(token)
        if info and info['State']['Running']:
            self.engine.api('POST', '/containers/' + info['Id'] + '/stop?t=8')
        info = self.container(token)
        if info and info['State']['Running']:
            raise RuntimeError('Runner stop was not confirmed')

    def logs(self, token):
        info = self.container(token)
        if not info:
            return b''
        return self.engine.api('GET', '/containers/' + info['Id'] + '/logs?stdout=1&stderr=1', raw=True)

    def archive(self, token, folder, destination):
        if folder not in ('workspace', 'output'):
            raise ValueError('Unknown archive')
        data = self.load(token)
        if not data['uploaded']:
            raise ValueError('Workspace upload is incomplete')
        info = self.container(token)
        if info and info['State']['Running']:
            raise ValueError('Stop the runner before taking a snapshot')
        if folder=='workspace' and data['request'].get('verification') is True:
            pack_tree(self.directory(token)/folder,destination,exclude_top=('.lab-cache','.gradle','build'))
        else:
            pack_tree(self.directory(token) / folder, destination)

    def delete(self, token):
        self.stop(token)
        info = self.container(token)
        if info:
            self.engine.api('DELETE', '/containers/' + info['Id'] + '?force=1&v=1')
        shutil.rmtree(self.directory(token))

    def recover_startup(self):
        # Stop writers left by a broker/host restart. Keep their snapshots for
        # worker recovery. Only this private persistent owner label is accepted.
        filters = quote(json.dumps({'label': ['codex-lab.broker=' + self.owner]}), safe='')
        for item in self.engine.api('GET', '/containers/json?all=1&filters=' + filters):
            token = item.get('Labels', {}).get('codex-lab.run', '')
            identifier(token, 32)
            try:
                self.load(token)
            except FileNotFoundError:
                self.engine.api('DELETE', '/containers/' + item['Id'] + '?force=1&v=1')
            else:
                self.stop(token)

    def reap(self):
        for path in (self.root / 'runs').glob('*/run.json'):
            data = json.loads(path.read_text())
            if data['started'] is not None and time.time() - data['started'] > MAX_RUNTIME:
                self.stop(path.parent.name)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        print(format % args, flush=True)

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body_file(self, limit):
        length = int(self.headers.get('Content-Length', '-1'))
        if not 0 <= length <= limit or self.headers.get('Transfer-Encoding'):
            raise ValueError('Invalid request length')
        output = tempfile.TemporaryFile(dir=self.server.broker.root)
        try:
            while length:
                block = self.rfile.read(min(length, 1024 * 1024))
                if not block:
                    raise ValueError('Incomplete request body')
                output.write(block)
                length -= len(block)
            output.seek(0)
            return output
        except BaseException:
            output.close()
            raise

    def dispatch(self):
        self.connection.settimeout(120)
        broker = self.server.broker
        parts = self.path.split('/')
        if self.command == 'GET' and self.path == '/health':
            self.reply(200, {'ready': True})
            return
        if self.command == 'POST' and self.path == '/runs':
            with self.body_file(256 * 1024) as body:
                self.reply(201, broker.create(json.load(body)))
            return
        if self.command == 'GET' and len(parts) == 4 and parts[1:3] == ['runs', 'by-job']:
            self.reply(200, broker.by_job(parts[3]))
            return
        if len(parts) not in (3, 4) or parts[1] != 'runs':
            raise FileNotFoundError('Unknown operation')
        token = identifier(parts[2], 32)
        action = parts[3] if len(parts) == 4 else ''
        if self.command == 'GET' and action == '':
            self.reply(200, broker.status(token))
        elif self.command == 'PUT' and action == 'workspace':
            with self.body_file(MAX_ARCHIVE) as body:
                broker.upload(token, body)
            self.reply(200, {})
        elif self.command == 'POST' and action in ('start', 'stop'):
            getattr(broker, action)(token)
            self.reply(200, {})
        elif self.command == 'DELETE' and action == '':
            broker.delete(token)
            self.reply(200, {})
        elif self.command == 'GET' and action == 'logs':
            data = broker.logs(token)
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.command == 'GET' and action in ('workspace', 'output'):
            with tempfile.TemporaryFile(dir=broker.root) as body:
                broker.archive(token, action, body)
                length = body.tell()
                body.seek(0)
                self.send_response(200)
                self.send_header('Content-Length', str(length))
                self.end_headers()
                shutil.copyfileobj(body, self.wfile)
        else:
            raise FileNotFoundError('Unknown operation')

    def handle_request(self):
        try:
            self.dispatch()
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.reply(400, {'error': str(exc)[:300]})
        except FileNotFoundError:
            self.reply(404, {'error': 'Unknown run or operation'})
        except Exception as exc:
            self.log_message('Broker error: %s: %s', type(exc).__name__, exc)
            self.reply(500, {'error': 'Runner operation failed; inspect broker logs'})

    do_GET = do_POST = do_PUT = do_DELETE = handle_request


class Server(socketserver.UnixStreamServer):
    allow_reuse_address = True


def main():
    broker = Broker()
    control = Path('/run/lab-runner')
    control.mkdir(parents=True, mode=0o750, exist_ok=True)
    os.chown(control, 0, 1000)
    os.chmod(control, 0o750)
    path = control / 'runner.sock'
    path.unlink(missing_ok=True)
    with Server(str(path), Handler) as server:
        os.chown(path, 0, 1000)
        os.chmod(path, 0o660)
        server.broker = broker
        server.timeout = 5
        print('Restricted runner ready', flush=True)
        while True:
            server.handle_request()
            broker.reap()


if __name__ == '__main__':
    main()
