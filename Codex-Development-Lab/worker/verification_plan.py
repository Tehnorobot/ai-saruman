"""Trusted selection and execution of bounded checks in one disposable job.

This file is image-owned. Workspace code remains untrusted; successful commands
do not establish complete gameplay coverage or authenticate project assertions.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time

try:  # Adjacent image-owned module under the isolated supervisor, package in tests.
    from .acceptance_runner import load_request, run_scenarios
except ImportError:
    from acceptance_runner import load_request, run_scenarios


def gradle_command(workspace):
    wrapper = workspace / 'gradlew'
    if wrapper.is_file():
        if not wrapper.resolve().is_relative_to(workspace.resolve()):
            raise ValueError('Gradle wrapper escapes workspace')
        return ['bash', str(wrapper)]
    return ['gradle']


def select_plan(workspace):
    workspace = Path(workspace)
    build_files = [workspace / name for name in ('build.gradle', 'build.gradle.kts', 'gradle.properties')]
    text = []
    for path in build_files:
        if path.is_file():
            if not path.resolve().is_relative_to(workspace.resolve()):
                raise ValueError('Build file escapes workspace')
            text.append(path.read_text(encoding='utf-8', errors='replace').lower())
    minecraft = any(marker in '\n'.join(text) for marker in ('minecraft', 'fabric-loom', 'fabric_version'))
    gradle = any(path.is_file() for path in build_files[:2])
    if gradle:
        prefix = gradle_command(workspace) + ['--daemon', '--build-cache', '--max-workers=4', '--console=plain',
                                              '-Dorg.gradle.jvmargs=-Xmx2G -XX:ActiveProcessorCount=4']
        return {'label': 'gradle_build', 'argv': prefix + ['build'],
                'gradle': prefix, 'minecraft': minecraft}
    if (workspace / 'pom.xml').is_file():
        return {'label': 'maven_test', 'argv': ['mvn', '-B', 'test'], 'minecraft': minecraft}
    if any((workspace / name).exists() for name in ('pyproject.toml', 'pytest.ini', 'tests')):
        return {'label': 'pytest', 'argv': ['python3', '-m', 'pytest', '-q'], 'minecraft': minecraft}
    package = workspace / 'package.json'
    if package.is_file():
        if not package.resolve().is_relative_to(workspace.resolve()):
            raise ValueError('package.json escapes workspace')
        data = json.loads(package.read_text(encoding='utf-8'))
        if isinstance(data.get('scripts'), dict) and data['scripts'].get('test'):
            return {'label': 'npm_test', 'argv': ['npm', 'test'], 'minecraft': minecraft}
    return {'label': 'build', 'argv': None, 'minecraft': minecraft}


def run_command(argv, cwd, logfile, timeout, *, env=None):
    started = time.monotonic()
    process = None
    status, exit_code = 'ERROR', None
    try:
        with Path(logfile).open('wb') as log:
            identity = ({'user': 1000, 'group': 1000, 'extra_groups': [], 'umask': 0o022}
                        if os.name == 'posix' and os.geteuid() == 0 else {})
            process = subprocess.Popen(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       env=env, start_new_session=(os.name == 'posix'),
                                       close_fds=True, **identity)
            try:
                exit_code = process.wait(timeout=timeout)
                status = 'PASS' if exit_code == 0 else 'FAIL'
            except subprocess.TimeoutExpired:
                status = 'TIMEOUT'
                if os.name == 'posix':
                    try:
                        _signal_project_group(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    if os.name == 'posix':
                        try:
                            _signal_project_group(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait()
    except Exception as exc:
        status = 'ERROR'
        Path(logfile).write_text(f'{type(exc).__name__}: {exc}', encoding='utf-8')
    data = b''
    if Path(logfile).exists():
        with Path(logfile).open('rb') as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 16000))
            data = log.read(16000)
    return {'status': status, 'exit_code': exit_code,
            'duration_seconds': round(time.monotonic() - started, 3),
            'tail': data[-16000:].decode('utf-8', errors='replace')}


def _signal_project_group(pid, signum):
    # Root deliberately lacks CAP_KILL. Signal project children using their UID,
    # then restore the saved supervisor identity before touching protected output.
    was_root = os.geteuid() == 0
    if was_root:
        os.seteuid(1000)
    try:
        os.killpg(pid, signum)
    finally:
        if was_root:
            os.seteuid(0)


def collect_artifacts(output, build=Path('/cache/build')):
    """Copy bounded regular artifacts through FDs, never following project links."""
    if not os.path.lexists(build):
        return []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root = os.open(build, flags)
    except FileNotFoundError:
        return []
    jars, seen, copied, screenshots = [], 0, 0, 0
    try:
        def walk(fd, prefix=()):
            nonlocal seen, copied, screenshots
            if len(prefix) > 24:
                raise ValueError('Artifact tree exceeds depth limit')
            for name in os.listdir(fd):
                seen += 1
                if seen > 20000:
                    raise ValueError('Artifact tree exceeds entry limit')
                if name in ('.', '..') or '\\' in name or ':' in name:
                    raise ValueError('Unsafe artifact filename')
                parts = prefix + (name,)
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if not prefix and name not in ('libs', 'test-results', 'reports', 'run'):
                        continue
                    child = os.open(name, flags, dir_fd=fd)
                    try:
                        walk(child, parts)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                target = None
                if len(parts) == 2 and parts[0] == 'libs' and name.endswith('.jar'):
                    target = output / 'builds' / name
                elif parts[0] == 'test-results' and name.endswith('.xml'):
                    target = output.joinpath(*parts)
                elif parts[:2] == ('reports', 'tests') and Path(name).suffix in ('.html', '.css', '.js', '.png', '.svg'):
                    target = output.joinpath(*parts)
                elif (parts[0] == 'run' and 'screenshots' in parts[1:-1]
                      and name.endswith('.png') and screenshots < 30):
                    target = output / 'screenshots' / ('-'.join(parts))
                    screenshots += 1
                elif parts[0] == 'run' and ('crash-reports' in parts or 'logs' in parts) and name.endswith(('.txt', '.log')):
                    target = output.joinpath(*parts)
                if target is None:
                    continue
                source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
                with os.fdopen(source_fd, 'rb') as source:
                    actual = os.fstat(source.fileno())
                    if not stat.S_ISREG(actual.st_mode) or actual.st_size > 512 * 1024 ** 2:
                        raise ValueError('Unsafe or oversized artifact')
                    copied += actual.st_size
                    if copied > 1024 ** 3:
                        raise ValueError('Artifact export exceeds 1 GiB')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with target.open('xb') as destination:
                        remaining = actual.st_size
                        while remaining:
                            block = source.read(min(remaining, 1024 * 1024))
                            if not block:
                                raise ValueError('Artifact changed size while exporting')
                            destination.write(block)
                            digest.update(block)
                            remaining -= len(block)
                    if target.parent == output / 'builds':
                        jars.append({'filename': name, 'size': actual.st_size, 'sha256': digest.hexdigest()})
        walk(root)
    finally:
        os.close(root)
    return jars


def run_verification(timeout_seconds, *, workspace=Path('/workspace'), output=Path('/output'),
                     recorded_run=None):
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError('Stage timeout must be a positive integer number of seconds')
    started = time.monotonic()
    workspace, output = Path(workspace), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    report = {'checks': results, 'status': 'INCOMPLETE', 'video': None,
              'gameplay_scenarios': [],
              'gameplay_coverage_note': 'Command success and video do not verify all required gameplay scenarios.'}

    def stage(label, argv, filename, *, recording=False):
        runner = recorded_run if recording else run_command
        if runner is None:
            raise RuntimeError('No trusted recording runner was installed')
        result = runner(argv, workspace, output / filename, timeout_seconds)
        result.update(label=label, log=filename, argv=argv)
        results.append(result)
        return result

    try:
        acceptance = load_request(output / 'acceptance-request.json')
        plan = select_plan(workspace)
        report['plan'] = {key: value for key, value in plan.items() if key != 'gradle'}
        if plan['argv'] is None:
            results.append({'label': plan['label'], 'status': 'NOT_CONFIGURED',
                            'detail': 'No recognised build/test configuration'})
        else:
            build = stage(plan['label'], plan['argv'], 'build.log')
            if build['status'] == 'PASS' and acceptance is not None:
                report['acceptance_results'] = run_scenarios(
                    acceptance, plan, workspace, output, timeout_seconds, run_command,
                    recorded_run, eula=os.getenv('MINECRAFT_EULA_ACCEPTED', 'false') == 'true')
                if any(s['status'] == 'FAIL' for s in report['acceptance_results']['scenarios']):
                    results.append({'label': 'acceptance_scenarios', 'status': 'FAIL',
                                    'detail': 'One or more acceptance scenario commands or assertions failed'})
            scheduled_gameplay = acceptance is not None and any(
                s['kind'] == 'gameplay' for s in acceptance['scenarios'])
            if (build['status'] == 'PASS' and plan['minecraft'] and not scheduled_gameplay
                    and not (acceptance or {}).get('skip_global_gameplay')):
                if os.getenv('MINECRAFT_EULA_ACCEPTED', 'false') != 'true':
                    results.append({'label': 'minecraft_client_gametest', 'status': 'NOT_TESTED',
                                    'detail': 'Minecraft EULA has not been explicitly accepted'})
                elif 'gradle' not in plan:
                    results.append({'label': 'minecraft_client_gametest', 'status': 'NOT_CONFIGURED',
                                    'detail': 'Client GameTest discovery requires a Gradle project'})
                else:
                    discovery = stage('discover_game_test', plan['gradle'] + ['--offline', 'tasks', '--all'],
                                      'game_tasks.log')
                    if discovery['status'] == 'PASS':
                        with (output / 'game_tasks.log').open('r', encoding='utf-8', errors='replace') as log:
                            tasks = log.read(4 * 1024 * 1024)
                        if re.search(r'^\s*runClientGameTest(?:\s+-.*)?\s*$', tasks, re.MULTILINE):
                            client = stage('minecraft_client_gametest', plan['gradle'] + ['runClientGameTest'],
                                           'client_gametest.log', recording=True)
                            quality = client.pop('video_capture', {'status': 'ERROR',
                                                'reason': 'Trusted recorder did not supply capture validation'})
                            report['video_capture'] = quality
                            (output / 'video_capture.json').write_text(json.dumps(quality, indent=2), encoding='utf-8')
                            if quality.get('status') != 'PASS':
                                results.append({'label': 'video_capture',
                                                'status': 'FAIL' if quality.get('status') == 'FAIL' else 'ERROR',
                                                'detail': quality.get('reason', 'Video capture validation failed')})
                            video = output / 'minecraft_client_capture.mp4'
                            if (quality.get('status') == 'PASS' and video.is_file()
                                    and not video.is_symlink() and video.stat().st_size):
                                report['video'] = video.name
                        else:
                            results.append({'label': 'minecraft_client_gametest', 'status': 'NOT_CONFIGURED',
                                            'detail': 'No runClientGameTest task exists'})
            if build['status'] == 'PASS':
                report['status'] = 'BUILD_VERIFIED_GAMEPLAY_UNVERIFIED'
    except Exception as exc:
        results.append({'label': 'verification_pipeline', 'status': 'ERROR',
                        'detail': f'{type(exc).__name__}: {exc}'})
    finally:
        try:
            report['jars'] = collect_artifacts(output)
            if (results and results[0]['status'] == 'PASS' and results[0]['label'] == 'gradle_build'
                    and not any(not jar['filename'].endswith(('-sources.jar', '-dev.jar', '-javadoc.jar'))
                                for jar in report['jars']) and Path('/cache/build').exists()):
                results.append({'label': 'production_jar', 'status': 'FAIL',
                                'detail': 'Gradle build succeeded without a production JAR'})
        except Exception as exc:
            results.append({'label': 'artifact_export', 'status': 'ERROR',
                            'detail': f'{type(exc).__name__}: {exc}'})
        if any(result['status'] in ('FAIL', 'TIMEOUT', 'ERROR') for result in results):
            report['status'] = 'FAIL'
        report['duration_seconds'] = round(time.monotonic() - started, 3)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output,
                                         prefix='.verification-', suffix='.tmp', delete=False) as handle:
            json.dump(report, handle, indent=2)
            temporary = handle.name
        os.replace(temporary, output / 'verification.json')
    return report
