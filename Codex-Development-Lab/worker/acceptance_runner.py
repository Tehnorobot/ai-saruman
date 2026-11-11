"""Image-owned execution of selected acceptance checks; project claims are not evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET
from copy import deepcopy
import uuid


MAX_JSON = 1024 * 1024
SCENARIO = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z', re.ASCII)
CATALOG_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z', re.ASCII)
TASK = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,95}\Z', re.ASCII)


def _text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


def execution_reuse(scenario):
    """An explicit reviewed equivalence declaration, never inferred from a task name."""
    value = scenario.get('execution_reuse')
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'group', 'profile', 'initial_state'}
            or any(not isinstance(v, str) or not CATALOG_ID.fullmatch(v) for v in value.values())):
        raise ValueError('Invalid execution reuse declaration')
    if scenario.get('kind') == 'gameplay':
        raise ValueError('Gameplay scenarios cannot share a physical execution')
    return value


def load_request(path):
    """Read only the supervisor-owned selection, never the agent workspace manifest."""
    path = Path(path)
    if not os.path.lexists(path):
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON:
            raise ValueError('Unsafe acceptance request')
        raw = handle.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise ValueError('Acceptance request exceeds size limit')
        request = json.loads(raw)
    if (not isinstance(request, dict) or type(request.get('schema_version')) is not int or request['schema_version'] not in (1,2)
            or not isinstance(request.get('spec_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', request['spec_sha256'])):
        raise ValueError('Invalid acceptance request version or specification digest')
    catalog = request.get('catalog_ids')
    if (not isinstance(catalog, list) or not 1 <= len(catalog) <= 2000
            or any(not isinstance(x, str) or not CATALOG_ID.fullmatch(x) for x in catalog)
            or len(set(catalog)) != len(catalog)):
        raise ValueError('Invalid acceptance catalog IDs')
    scenarios = request.get('scenarios')
    if not isinstance(scenarios, list) or (request['schema_version']==1 and len(scenarios) > 4):
        raise ValueError('At most four acceptance scenarios may run per round')
    seen = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError('Invalid acceptance scenario')
        sid = scenario.get('id')
        if (not isinstance(sid, str) or len(sid) > 64 or not SCENARIO.fullmatch(sid)
                or sid in seen or scenario.get('kind') not in ('gameplay', 'unit', 'integration')
                or not isinstance(scenario.get('task'), str) or not TASK.fullmatch(scenario['task'])):
            raise ValueError('Invalid scenario ID, kind or Gradle task')
        seen.add(sid)
        ids = scenario.get('requirement_ids')
        if (not isinstance(ids, list) or not ids or len(ids) > 2000
                or any(not isinstance(x, str) or x not in catalog for x in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Scenario references unknown or duplicate requirements')
        tests = scenario.get('expected_tests')
        if (not isinstance(tests, list) or not tests or (request['schema_version']==1 and len(tests)>100)
                or any(not isinstance(t, dict) or not _text(t.get('classname'))
                       or not _text(t.get('name')) for t in tests)):
            raise ValueError('Scenario requires bounded exact test identities')
        pairs = [(t['classname'], t['name']) for t in tests]
        if len(set(pairs)) != len(pairs):
            raise ValueError('Duplicate expected tests')
        execution_reuse(scenario)
    return request


def _execution_key(request, scenario, argv, workspace):
    reuse = execution_reuse(scenario)
    if reuse is None:
        return None
    from common.acceptance import source_fingerprint
    inputs = {'spec_sha256': request['spec_sha256'], 'source_sha256': source_fingerprint(workspace),
              'kind': scenario['kind'], 'argv': argv, 'reuse': reuse,
              'expected_tests': sorted((t['classname'], t['name']) for t in scenario['expected_tests']),
              # The hash distinguishes real process environments without exporting their values.
              'environment_sha256': hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True).encode()).hexdigest()}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def _clone_reports(output, proof, destination):
    """Copy immutable supervisor snapshots to each logical scenario's allowed namespace."""
    from common.history import _open
    destination.mkdir(mode=0o700)
    copied = deepcopy(proof)
    for number, descriptor in enumerate(copied['reports']):
        relative = descriptor['file']
        if (not isinstance(relative, str) or '\\' in relative or ':' in relative
                or relative.startswith('/') or any(p in ('', '.', '..') for p in relative.split('/'))):
            raise ValueError('Unsafe shared execution evidence path')
        with _open(output, tuple(relative.split('/'))) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
                raise ValueError('Unsafe shared execution evidence')
            raw = source.read(4 * 1024 * 1024 + 1)
        if len(raw) != info.st_size or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise ValueError('Shared execution evidence hash mismatch')
        target = destination / ('report-%03d.xml' % number)
        with target.open('xb') as handle:
            handle.write(raw)
        descriptor['file'] = target.relative_to(output).as_posix()
    return copied


def _inventory(output, proof):
    """Exact case inventory from already checked immutable XML, including extra cases."""
    from common.history import _open
    cases = []
    for descriptor in proof['reports']:
        with _open(output, tuple(descriptor['file'].split('/'))) as source:
            raw = source.read(4 * 1024 * 1024 + 1)
        if hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise ValueError('Build evidence changed after collection')
        document = ET.fromstring(raw.decode('utf-8-sig'))
        cases.extend((case.get('classname'), case.get('name')) for case in document.iter('testcase'))
    return sorted(cases)


def store_execution_record(output, directory, record):
    """Durable supervisor-owned physical-run metadata, separate from logical views."""
    raw = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode('utf-8')
    target = directory / 'execution.json'
    with target.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return {'file':target.relative_to(output).as_posix(), 'sha256':hashlib.sha256(raw).hexdigest()}


def _visit_xml(build, callback):
    """Open every component relative to an FD; never follow project symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root = os.open(build, flags)
    entries = 0
    count = 0
    try:
        try:
            reports = os.open('test-results', flags, dir_fd=root)
        except FileNotFoundError:
            return
        try:
            def walk(fd, prefix=()):
                nonlocal entries, count
                if len(prefix) > 16:
                    raise ValueError('Test result tree is too deep')
                for name in sorted(os.listdir(fd)):
                    entries += 1
                    if entries > 10000 or name in ('.', '..') or any(c in name for c in ('/', '\\', ':')):
                        raise ValueError('Unsafe test result tree')
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(name, flags, dir_fd=fd)
                        try:
                            walk(child, prefix + (name,))
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and name.endswith('.xml'):
                        count += 1
                        if count > 256:
                            raise ValueError('Too many test result XML files')
                        callback(fd, name, prefix + (name,))
            walk(reports)
        finally:
            os.close(reports)
    finally:
        os.close(root)


def clear_test_reports(build):
    """Remove old XML as the project UID, so cached reports cannot establish PASS."""
    if not Path(build).exists():
        return
    root = os.name == 'posix' and os.geteuid() == 0
    if root:
        os.setegid(1000)
        os.seteuid(1000)
    try:
        _visit_xml(build, lambda fd, name, parts: os.unlink(name, dir_fd=fd))
    finally:
        if root:
            os.seteuid(0)
            os.setegid(0)


def assertion_evidence(build, destination, expected_tests):
    evidence, cases, total = [], {}, 0
    test_cases=[];recorded_cases={};extra_cases=0
    wanted={(t['classname'],t['name']) for t in expected_tests}
    failures = []
    suite_failed = False
    destination.mkdir(mode=0o700)
    def collect(parent, name, parts):
        nonlocal total, suite_failed, extra_cases
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
                raise ValueError('Unsafe or oversized test report')
            total += info.st_size
            if total > 16 * 1024 * 1024:
                raise ValueError('Test reports exceed size limit')
            data = handle.read(info.st_size + 1)
        if (len(data) != info.st_size or b'\x00' in data
                or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper()):
            raise ValueError('Unsafe XML report')
        # XML UTF-16 can hide DTD tokens from an ASCII scan. Require UTF-8 reports.
        source = data.decode('utf-8-sig')
        document = ET.fromstring(source)
        if document.tag not in ('testsuite', 'testsuites'):
            raise ValueError('Not a JUnit test report')
        for suite in document.iter('testsuite'):
            for attribute in ('failures', 'errors'):
                value = suite.get(attribute, '0')
                if not re.fullmatch(r'[0-9]{1,9}', value):
                    raise ValueError('Invalid JUnit result counts')
                suite_failed |= int(value) > 0
        target = destination / ('report-%03d.xml' % len(evidence))
        target.write_bytes(data)
        evidence.append({'file': str(target.name), 'source': '/'.join(parts),
                         'sha256': hashlib.sha256(data).hexdigest()})
        for case in document.iter('testcase'):
            key = (case.get('classname'), case.get('name'))
            status = ('FAIL' if case.find('failure') is not None or case.find('error') is not None
                      else 'NOT_TESTED' if case.find('skipped') is not None else 'TEST_PASSED')
            cases.setdefault(key, []).append(status)
            if ((key in wanted and recorded_cases.get(key,0)<2)
                    or (key not in wanted and status=='FAIL' and extra_cases<8)):
                recorded_cases[key]=recorded_cases.get(key,0)+1
                if key not in wanted:extra_cases+=1
                node=case.find('failure')
                if node is None:node=case.find('error')
                if node is None:node=case.find('skipped')
                message='' if node is None else (node.get('message') or node.text or '')
                test_cases.append({'classname':str(key[0])[:256],'name':str(key[1])[:256],
                    'status':'SKIPPED' if case.find('skipped') is not None and status!='FAIL' else status,
                    'message':message[:500],'report':target.name})
            if status=='FAIL' and len(failures)<8:
                node=case.find('failure')
                if node is None:node=case.find('error')
                message=node.get('message') or ''
                stack=node.text or ''
                causes=re.findall(r'^\s*Caused by:\s*(.+)$',stack,re.MULTILINE)
                if causes:message=causes[-1]
                elif message.strip().lower() in ('','null','none'):message=stack.split('\n',1)[0]
                failures.append({'classname':str(key[0])[:256],'name':str(key[1])[:256],
                    'message':message[:800]})
    if Path(build).exists():
        _visit_xml(build, collect)
    expected = [(t['classname'], t['name']) for t in expected_tests]
    missing = [pair for pair in expected if pair not in cases]
    statuses = [s for pair in expected for s in cases.get(pair, [])]
    if suite_failed or any('FAIL' in values for values in cases.values()):
        status, detail = 'FAIL', 'Fresh JUnit results contain failures or errors'
        if failures:
            detail+='; JUnit failure: '+failures[0]['message']
    elif missing or not statuses or 'NOT_TESTED' in statuses or any(len(cases.get(pair, [])) != 1 for pair in expected):
        status, detail = 'NOT_TESTED', 'Expected exact test cases are missing or skipped in fresh JUnit results'
    else:
        status, detail = 'TEST_PASSED', 'Expected exact test cases executed without reported failures or skips'
    return status, detail, {'reports': evidence, 'expected_tests': expected_tests,'failures':failures,
                            'test_cases':test_cases,
                            'missing_tests': [{'classname': c, 'name': n} for c, n in missing],
                            'scope_note': 'Project-authored assertions require separate review against the specification.'}


def _copy_execution_log(output, source_path, destination):
    from common.history import _open
    if (not isinstance(source_path, str) or '\\' in source_path or ':' in source_path
            or source_path.startswith('/') or any(p in ('', '.', '..') for p in source_path.split('/'))):
        raise ValueError('Unsafe shared execution log path')
    with _open(output, tuple(source_path.split('/'))) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024 * 1024:
            raise ValueError('Unsafe or oversized shared execution log')
        with destination.open('xb') as target:
            for block in iter(lambda:source.read(1024 * 1024), b''):
                target.write(block)


def run_scenarios(request, plan, workspace, output, timeout_seconds, run_command,
                  recorded_run, *, build=Path('/cache/build'), eula=False, build_execution=None,
                  world_fixture=None):
    report = {'schema_version': 1, 'spec_sha256': request['spec_sha256'], 'scenarios': [],
              'execution_runs': []}
    # Sharing is invocation-local and contiguous. Any different physical run
    # invalidates the candidate, so gameplay/state-changing tasks cannot be
    # silently crossed to reuse an earlier initial state.
    shared = None
    build_candidate = build_execution
    for scenario in request['scenarios']:
        sid = scenario['id']
        relative = 'acceptance/' + sid
        directory = output / relative
        directory.mkdir(parents=True, mode=0o700)
        result = {'id': sid, 'requirement_ids': scenario['requirement_ids'],
                  'status': 'NOT_TESTED', 'test_status': 'NOT_TESTED', 'video': None,
                  'log': None, 'detail': 'Not executed'}
        report['scenarios'].append(result)
        command = None
        execution_id = None
        journal_path = None
        try:
            if 'gradle' not in plan:
                result['detail'] = 'Acceptance execution requires a Gradle project'
                continue
            if scenario['kind'] == 'gameplay' and (not eula or recorded_run is None):
                result.update(detail=('Gameplay requires existing Minecraft EULA acceptance' if not eula
                    else 'Trusted gameplay execution/recording runner is unavailable'),
                    attempted=False,execution_stage='world_preparation',stopped_by='world_fixture')
                continue
            runner = recorded_run if scenario['kind'] == 'gameplay' else run_command
            argv = plan['gradle'] + [scenario['task'], '--rerun-tasks']
            # This property selects the Minecraft client scenario. Unit/API
            # scenarios use their dedicated task and must not enter its registry.
            if scenario['kind'] == 'gameplay':
                argv.append('-PlabAcceptanceScenario=' + sid)
            key = _execution_key(request, scenario, argv, workspace)
            candidate = shared if key is not None and shared and shared['key'] == key else None
            if candidate is None and build_candidate is not None and key is not None and scenario['kind'] == 'unit' and scenario['task'] == 'test':
                from common.acceptance import source_fingerprint
                # An opt-in logical unit selection may project the fresh test
                # dependency of global build. Actual argv stays build, not test.
                wanted = sorted((t['classname'], t['name']) for t in scenario['expected_tests'])
                if (build_candidate.get('fresh') is True and build_candidate['command'].get('status') == 'PASS'
                        and build_candidate.get('source_sha256') == source_fingerprint(workspace)
                        and _inventory(output, build_candidate['proof']) == wanted):
                    candidate = {'key':key, 'result':build_candidate['result']}
                    report['execution_runs'].append(deepcopy(build_candidate['execution_record']))
            build_candidate = None
            if candidate is not None:
                physical = candidate['result']
                own_id, own_requirements = result['id'], result['requirement_ids']
                result.update(deepcopy(physical))
                result.update(id=own_id, requirement_ids=own_requirements, measurement_reused=True)
                result['assertion_evidence'] = _clone_reports(output, physical['assertion_evidence'], directory / 'assertions')
                result['assertion_evidence']['expected_tests'] = deepcopy(scenario['expected_tests'])
                if physical.get('log'):
                    _copy_execution_log(output, physical['log'], directory / 'test.log')
                    result['log'] = relative + '/test.log'
                shared = {'key':key, 'result':deepcopy(result)}
                continue
            shared = None
            clear_test_reports(build)
            result['log'] = relative + '/test.log'
            options = {'scenario_id': sid} if scenario['kind'] == 'gameplay' else {}
            if scenario['kind']=='gameplay' and world_fixture is not None:
                try:
                    argv,env,fixture=world_fixture.launch(argv,sid,directory)
                    options['env']=env
                    result['world_fixture']=fixture
                except Exception as exc:
                    result.update(detail='World preparation unavailable: '+str(exc),attempted=False,
                                  execution_stage='world_preparation',stopped_by='world_fixture')
                    continue
            execution_id = 'RUN-' + uuid.uuid4().hex
            if scenario['kind'] == 'gameplay':
                from common.gameplay_execution import prepare_journal
                # Journal absence never prevents a real command from running.
                # Random per-run paths cannot reuse an earlier process journal.
                try:
                    journal_path = prepare_journal(execution_id)
                    options['env'] = {**options.get('env', {}),
                        'LAB_GAMEPLAY_EXECUTION_JOURNAL':str(journal_path),
                        'LAB_GAMEPLAY_EXECUTION_ID':execution_id}
                except (OSError, ValueError) as exc:
                    result['journal_preparation_error'] = str(exc)[:500]
                result.update(attempted=True, execution_stage='scenario', execution_id=execution_id)
            command = runner(argv, workspace, directory / 'test.log', timeout_seconds, **options)
            result['command_tail']=str(command.get('tail',''))[-2000:]
            result.update(test_status=command['status'], duration_seconds=command.get('duration_seconds'),
                          exit_code=command.get('exit_code'), argv=argv,
                          execution_id=execution_id, measurement_reused=False)
            execution_record = {'execution_id':execution_id, 'logical_input_sha256':key,
                'argv':argv, 'kind':scenario['kind'], 'origin_scenario_id':sid,
                'log':result['log'], 'status':command['status'], 'spec_sha256':request['spec_sha256'],
                'execution_reuse':deepcopy(scenario.get('execution_reuse'))}
            result['execution_record'] = store_execution_record(output, directory, execution_record)
            execution_record['record_ref'] = result['execution_record']
            report['execution_runs'].append(execution_record)
            if command['status'] != 'PASS':
                result.update(status='FAIL', detail='Scenario command did not succeed: ' + command['status'])
            quality = command.get('video_capture')
            if quality is not None:
                result['video_capture'] = quality
                (directory / 'video_capture.json').write_text(json.dumps(quality, indent=2), encoding='utf-8')
                video = directory / 'minecraft_client_capture.mp4'
                if quality.get('status') == 'PASS' and video.is_file() and not video.is_symlink() and video.stat().st_size:
                    result['video'] = relative + '/' + video.name
            status, detail, evidence = assertion_evidence(build, directory / 'assertions', scenario['expected_tests'])
            for item in evidence['reports']:
                item['file'] = relative + '/assertions/' + item['file']
            result['assertion_evidence'] = evidence
            result.update(status=status, detail=detail)
            if command['status'] != 'PASS':
                result.update(status='FAIL', detail='Scenario command did not succeed: ' + command['status']+
                    ('; '+detail if status=='FAIL' else ''))
            if key is not None:
                shared = {'key':key, 'result':deepcopy(result)}
        except Exception as exc:
            if scenario['kind']=='gameplay' and execution_id is not None and command is None:
                result.update(test_status='ERROR',exit_code=None)
            result.update(status='FAIL' if result['test_status'] in ('FAIL', 'ERROR', 'TIMEOUT') else 'NOT_TESTED',
                          detail=type(exc).__name__ + ': ' + str(exc)[:500])
        finally:
            if scenario['kind']=='gameplay' and execution_id is not None:
                from common.gameplay_execution import collect_execution_lifecycle
                try:
                    result['execution_lifecycle'] = collect_execution_lifecycle(build, directory, output,
                        sid, execution_id, command or {'status':'ERROR','exit_code':None,
                            'supervisor_error':result.get('detail')},journal_path=journal_path)
                except (OSError,ValueError,UnicodeError) as exc:
                    result['execution_lifecycle_error'] = str(exc)[:500]
                finally:
                    if journal_path is not None:
                        from common.gameplay_execution import release_journal
                        try:release_journal(journal_path)
                        except (OSError,ValueError) as exc:
                            result['journal_cleanup_error'] = str(exc)[:500]
            if scenario['kind']=='gameplay' and world_fixture is not None:
                collector=getattr(world_fixture,'collect_client_setup',None)
                if collector is not None:
                    try:
                        setup=collector(directory)
                        if setup:
                            setup['file']=relative+'/'+setup['file']
                            result['dependency_preparation']=setup
                    except (OSError,ValueError,UnicodeError) as exc:
                        result['dependency_preparation']={'status':'UNAVAILABLE',
                            'detail':'Unable to retain client setup diagnostics: '+str(exc)}
                try:world_fixture.release()
                except Exception as exc:
                    result['world_cleanup']={'status':'DEFERRED_TO_CONTAINER_REMOVAL','detail':str(exc)[:500]}
            from common.failure_details import enrich_result
            result.update(enrich_result(scenario,result))
    (output / 'acceptance_results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
