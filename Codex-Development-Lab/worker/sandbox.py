"""Worker client for the restricted runner. No Docker Engine access lives here."""
from __future__ import annotations

import http.client
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import time
import traceback

from common.safe_archive import pack_tree, recover_tree, replace_tree, unpack_tree
from common.acceptance import read_json


class RunnerCleanupError(RuntimeError):
    """Project remains quarantined until runner stop and snapshot return succeed."""


class RunnerAPIError(RuntimeError):
    """Runner HTTP failure with a status independent of its response body."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RunnerConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=120)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class DockerSandbox:
    # Retain the public class name for callers; this is no longer an Engine client.
    def __init__(self):
        self.socket_path = os.getenv('LAB_RUNNER_SOCKET', '/run/lab-runner/runner.sock')

    def api(self, method, path, body=None, *, raw=False, upload=None, download=None):
        connection = RunnerConnection(self.socket_path)
        # Archive installation/packing and cleanup can legitimately outlast a
        # small control-request timeout (many fsyncs on a slow Docker volume).
        # A local transport deadline must not abandon a still-running upload
        # and then misreport its queued cleanup as an unconfirmed writer.
        # Execution budgets and project isolation are enforced separately.
        if upload is not None or download is not None or method=='DELETE':
            connection.timeout=None
        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {'Content-Type': 'application/json'}
            if upload is not None:
                upload.seek(0, os.SEEK_END)
                headers = {'Content-Type': 'application/x-tar', 'Content-Length': str(upload.tell())}
                upload.seek(0)
                payload = upload
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            if response.status >= 300:
                data = response.read(4096)
                raise RunnerAPIError(response.status,
                                     f'Runner {method} {path}: {response.status}: '
                                     + data.decode(errors='replace')[:300])
            if download is not None:
                expected = int(response.getheader('Content-Length', '-1'))
                if not 0 <= expected <= 21 * 1024 ** 3:
                    raise RuntimeError('Invalid runner archive length')
                remaining = expected
                while remaining:
                    block = response.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise RuntimeError('Runner archive was interrupted')
                    download.write(block)
                    remaining -= len(block)
                download.seek(0)
                return None
            data = response.read()
            return data if raw else json.loads(data) if data else None
        finally:
            connection.close()

    def config(self, argv, cwd, jid, *, record=False, env=None):
        requested = Path(cwd).absolute()
        cwd = requested.resolve()
        projects = Path(os.getenv('LAB_PROJECTS', '/projects')).resolve()
        if (requested != cwd or cwd.parent.parent != projects or cwd.name != 'workspace'
                or len(cwd.parent.name) != 14
                or any(c not in '0123456789abcdef' for c in cwd.parent.name)):
            raise ValueError('Runner needs a real workspace belonging to one project')
        mapped = []
        for argument in argv:
            argument = str(argument)
            if argument == str(cwd) or argument.startswith(str(cwd) + os.sep):
                argument = '/workspace' + argument[len(str(cwd)):].replace(os.sep, '/')
            mapped.append(argument)
        return {'project': cwd.parent.name, 'job': jid, 'argv': mapped,
                'record': record, 'ci': (env or {}).get('CI', 'true'),
                'eula': (env or {}).get('MINECRAFT_EULA_ACCEPTED', 'false') == 'true'}

    def _restore(self, token, workspace):
        with tempfile.TemporaryFile() as snapshot:
            self.api('GET', '/runs/' + token + '/workspace', download=snapshot)
            size=snapshot.seek(0,os.SEEK_END)
            snapshot.seek(0)
            replace_tree(snapshot, workspace)
            return size

    def recover(self, project_root):
        recover_tree(Path(project_root) / 'workspace')
        marker = Path(project_root) / '.lab-runner.json'
        if not marker.exists():
            return
        token = json.loads(marker.read_text(encoding='utf-8'))['container']
        # Never interpret a legacy/raw Engine ID as an unrestricted target.
        if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise RunnerCleanupError('Legacy runner marker requires stopping the old runner before upgrading')
        try:
            info = self.api('GET', '/runs/' + token)
            labels = info.get('Config', {}).get('Labels', {})
            if labels.get('codex-lab.project') != Path(project_root).name:
                raise RuntimeError('Refusing recovery of another project')
            self.api('POST', '/runs/' + token + '/stop')
            if info['Uploaded']:
                self._restore(token, Path(project_root) / 'workspace')
            self.api('DELETE', '/runs/' + token)
        except Exception as exc:
            # A deleted run means the previous cleanup finished before marker unlink.
            if not isinstance(exc, RunnerAPIError) or exc.status != 404:
                raise RunnerCleanupError('Runner recovery is unconfirmed; project stays locked: '
                                         + type(exc).__name__ + ': ' + str(exc)[:400]) from exc
        marker.unlink()

    def execute(self, argv, cwd, logfile, timeout, jid, poll, *, env=None, record=False):
        return self._execute(argv, cwd, logfile, timeout, jid, poll, env=env, record=record)

    def verify(self, cwd, output_dir, timeout, jid, poll, *, eula=False):
        """One isolated session and workspace roundtrip for all verification phases."""
        if type(timeout) is not int or timeout < 60 or type(eula) is not bool:
            raise ValueError('Verification requires an integer phase budget of at least 60 seconds and boolean EULA')
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file=output_dir/'verification.json'
        report_file.unlink(missing_ok=True)
        try:
            request=read_json(cwd,'acceptance-request.json')
            scenario_count=len(request.get('scenarios',[])) if request.get('schema_version')==2 else min(4,len(request.get('scenarios',[])))
        except (OSError,ValueError,TypeError,AttributeError):scenario_count=0
        result = self._execute([], cwd, output_dir/'runner.log', timeout*(3+scenario_count)+180, jid, poll,
                               verification={'timeout_seconds':timeout,'eula':eula}, output_dir=output_dir)
        if not report_file.is_file() or report_file.stat().st_size>1024*1024:
            report={'status':'FAIL','checks':[{'label':'verification_session',**result}],
                    'gameplay_scenarios':[], 'video':None}
        else:
            try:
                report=json.loads(report_file.read_text(encoding='utf-8'))
                checks=report['checks']
                if (not isinstance(checks,list) or not 1 <= len(checks) <= 16
                        or any(not isinstance(check,dict) or not isinstance(check.get('label'),str)
                               or check.get('status') not in ('PASS','FAIL','TIMEOUT','ERROR','NOT_TESTED','NOT_CONFIGURED')
                               for check in checks)
                        or report.get('status') not in ('FAIL','INCOMPLETE','BUILD_VERIFIED_GAMEPLAY_UNVERIFIED')):
                    raise ValueError('Invalid verification report')
            except (ValueError,TypeError,KeyError,RecursionError) as exc:
                report={'status':'FAIL','checks':[{'label':'verification_report','status':'ERROR',
                                                  'detail':str(exc)[:300]}],'video':None}
        if (result['status'] != 'PASS'
                and not any(c.get('label') == 'verification_session' for c in report['checks'])):
            report['checks'].append({'label':'verification_session',**result})
        if result['status'] != 'PASS' or any(c.get('status') in ('FAIL','TIMEOUT','ERROR') for c in report['checks']):
            report['status']='FAIL'
        report.update(timings=result.get('timings',{}),metrics=result.get('metrics',{}))
        return report

    @staticmethod
    def _copy_output(source, output_dir):
        allowed_files={'verification.json','video_capture.json','acceptance_results.json','build.log','maven.log','pytest.log','npm.log',
                       'game_tasks.log','client_gametest.log','minecraft_client_capture.mp4','ffmpeg.log','xvfb.log'}
        allowed_dirs={'builds','screenshots','reports','test-results','crash-reports','run','acceptance','world-fixture'}
        for path in source.rglob('*'):
            relative=path.relative_to(source)
            if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve())
                    or (relative.parts[0] not in allowed_files and relative.parts[0] not in allowed_dirs)):
                continue
            target=output_dir/relative
            if target.is_symlink() or not target.resolve().is_relative_to(output_dir.resolve()):
                raise ValueError('Verification output escapes artifacts')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)

    def _execute(self, argv, cwd, logfile, timeout, jid, poll, *, env=None, record=False,
                 verification=None,output_dir=None):
        recover_tree(Path(cwd))
        request = self.config(argv if verification is None else ['verification'], cwd, jid, record=record, env=env)
        if verification is not None:
            request={key:request[key] for key in ('project','job')}
            request.update(verification=True,**verification)
        if record or verification is not None:
            from common.recording_settings import DEFAULT_MAX_SECONDS, max_seconds
            try:
                request['capture_max_seconds'] = max_seconds()
            except (OSError, ValueError, sqlite3.Error) as exc:
                # Recording is optional; a configuration read must not stop tests.
                logging.getLogger(__name__).warning(
                    'Recording setting unavailable (%s); using %s seconds',
                    type(exc).__name__, DEFAULT_MAX_SECONDS)
                request['capture_max_seconds'] = DEFAULT_MAX_SECONDS
        root = Path(cwd).parent
        marker = root / '.lab-runner.json'
        token = None
        started = time.monotonic()
        status = None
        exit_code = None
        restored = False
        timings={}
        metrics={}
        phase=time.monotonic()

        def mark(value):
            temporary = marker.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as handle:
                json.dump({'container': value}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(marker)

        try:
            self.recover(root)
            # Includes a crash after create's response but before writing our marker.
            try:
                orphan = self.api('GET', '/runs/by-job/' + jid)
            except RunnerAPIError as exc:
                if exc.status != 404:
                    raise
            else:
                if orphan['Config']['Labels']['codex-lab.project'] != root.name:
                    raise RunnerCleanupError('Unfinished run belongs to another project')
                mark(orphan['Id'])
                self.recover(root)
            if status := poll():
                return {'status': status, 'exit_code': None, 'tail': ''}
            # Prepare/validate the source before creating a durable broker run.
            with tempfile.TemporaryFile() as snapshot:
                if verification is None:
                    pack_tree(Path(cwd), snapshot)
                else:
                    pack_tree(Path(cwd), snapshot,exclude_top=('.lab-cache','.gradle','build'))
                timings['pack_seconds']=round(time.monotonic()-phase,4)
                metrics['workspace_upload_bytes']=snapshot.tell()
                phase=time.monotonic()
                token = self.api('POST', '/runs', request)['Id']
                mark(token)
                self.api('PUT', '/runs/' + token + '/workspace', upload=snapshot)
                timings['upload_seconds']=round(time.monotonic()-phase,4)
            if status := poll():
                return {'status': status, 'exit_code': None, 'tail': ''}
            phase=time.monotonic()
            self.api('POST', '/runs/' + token + '/start')
            timings['start_seconds']=round(time.monotonic()-phase,4)
            phase=time.monotonic()
            while True:
                info = self.api('GET', '/runs/' + token)
                if not info['State']['Running']:
                    exit_code = info['State']['ExitCode']
                    status = 'PASS' if exit_code == 0 and not info['State'].get('OOMKilled') else 'FAIL'
                    break
                status = poll()
                if status or time.monotonic() - started > timeout:
                    status = status or 'TIMEOUT'
                    self.api('POST', '/runs/' + token + '/stop')
                    break
                time.sleep(1)
            timings['execution_seconds']=round(time.monotonic()-phase,4)
            data = self.api('GET', '/runs/' + token + '/logs', raw=True)
            logfile.write_text(data.decode('utf-8', errors='replace'), encoding='utf-8')
            phase=time.monotonic()
            metrics['workspace_download_bytes']=self._restore(token, Path(cwd))
            timings['workspace_return_seconds']=round(time.monotonic()-phase,4)
            restored = True
            if record or verification is not None:
                phase=time.monotonic()
                with tempfile.TemporaryDirectory() as output, tempfile.TemporaryFile() as snapshot:
                    self.api('GET', '/runs/' + token + '/output', download=snapshot)
                    metrics['output_download_bytes']=snapshot.seek(0,os.SEEK_END)
                    snapshot.seek(0)
                    unpack_tree(snapshot, Path(output))
                    if verification is not None:
                        self._copy_output(Path(output),output_dir)
                    else:
                        video = Path(output) / 'minecraft_client_capture.mp4'
                        if video.is_file() and not video.is_symlink() and video.stat().st_size:
                            shutil.copyfile(video, logfile.parent / video.name)
                timings['output_return_seconds']=round(time.monotonic()-phase,4)
            return {'status': status, 'exit_code': exit_code, 'tail': data[-16000:].decode('utf-8', errors='replace'),
                    'timings':timings,'metrics':metrics}
        finally:
            phase=time.monotonic()
            if token:
                try:
                    self.api('POST', '/runs/' + token + '/stop')
                    info = self.api('GET', '/runs/' + token)
                    if info['Uploaded'] and not restored:
                        self._restore(token, Path(cwd))
                    self.api('DELETE', '/runs/' + token)
                    marker.unlink(missing_ok=True)
                except Exception as exc:
                    try:
                        with logfile.open('a', encoding='utf-8') as handle:
                            handle.write('\n[runner transport/cleanup failure]\n' + traceback.format_exc()[-32000:])
                    except OSError:
                        pass
                    raise RunnerCleanupError('Runner stop/snapshot return is unconfirmed; project stays locked: '
                                             + type(exc).__name__ + ': ' + str(exc)[:400]) from exc
            timings['cleanup_seconds']=round(time.monotonic()-phase,4)
            timings['total_seconds']=round(time.monotonic()-started,4)
