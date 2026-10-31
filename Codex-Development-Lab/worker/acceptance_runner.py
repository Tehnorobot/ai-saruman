"""Image-owned execution of selected acceptance checks; project claims are not evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET


MAX_JSON = 1024 * 1024
SCENARIO = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z', re.ASCII)
CATALOG_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z', re.ASCII)
TASK = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,95}\Z', re.ASCII)


def _text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


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
    return request


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


def run_scenarios(request, plan, workspace, output, timeout_seconds, run_command,
                  recorded_run, *, build=Path('/cache/build'), eula=False):
    report = {'schema_version': 1, 'spec_sha256': request['spec_sha256'], 'scenarios': []}
    for scenario in request['scenarios']:
        sid = scenario['id']
        relative = 'acceptance/' + sid
        directory = output / relative
        directory.mkdir(parents=True, mode=0o700)
        result = {'id': sid, 'requirement_ids': scenario['requirement_ids'],
                  'status': 'NOT_TESTED', 'test_status': 'NOT_TESTED', 'video': None,
                  'log': None, 'detail': 'Not executed'}
        report['scenarios'].append(result)
        try:
            if 'gradle' not in plan:
                result['detail'] = 'Acceptance execution requires a Gradle project'
                continue
            if scenario['kind'] == 'gameplay' and (not eula or recorded_run is None):
                result['detail'] = 'Gameplay requires existing EULA acceptance and trusted recording'
                continue
            clear_test_reports(build)
            runner = recorded_run if scenario['kind'] == 'gameplay' else run_command
            argv = plan['gradle'] + [scenario['task'], '--rerun-tasks']
            # This property selects the Minecraft client scenario. Unit/API
            # scenarios use their dedicated task and must not enter its registry.
            if scenario['kind'] == 'gameplay':
                argv.append('-PlabAcceptanceScenario=' + sid)
            result['log'] = relative + '/test.log'
            options = {'scenario_id': sid} if scenario['kind'] == 'gameplay' else {}
            command = runner(argv, workspace, directory / 'test.log', timeout_seconds, **options)
            result['command_tail']=str(command.get('tail',''))[-2000:]
            result.update(test_status=command['status'], duration_seconds=command.get('duration_seconds'),
                          exit_code=command.get('exit_code'), argv=argv)
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
        except Exception as exc:
            result.update(status='FAIL' if result['test_status'] in ('FAIL', 'ERROR', 'TIMEOUT') else 'NOT_TESTED',
                          detail=type(exc).__name__ + ': ' + str(exc)[:500])
        finally:
            from common.failure_details import enrich_result
            result.update(enrich_result(scenario,result))
    (output / 'acceptance_results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
