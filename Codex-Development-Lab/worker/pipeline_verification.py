"""Independent execution adapter for semantic-pipeline manifests.

Only fresh, hash-verified JUnit cases establish checkpoint outcomes. Model
reports and persisted ``test_cases`` lists never establish PASS here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import uuid
import xml.etree.ElementTree as ET

from common import acceptance
from common.history import _open
from common.pipeline_contracts import validate
from worker.sandbox import RunnerCleanupError


MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_REPORT_TOTAL = 16 * 1024 * 1024
SAFE_ID = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z', re.ASCII)


def _parts(relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ValueError('Invalid relative artifact path')
    path = PurePosixPath(relative)
    if path.is_absolute() or any(p in ('', '.', '..') or ':' in p for p in relative.split('/')):
        raise ValueError('Artifact path escapes its root')
    return path.parts


def _read(root, relative, limit=None):
    with _open(root, _parts(relative)) as source:
        if limit is not None and os.fstat(source.fileno()).st_size > limit:
            raise ValueError('Evidence file exceeds the parsing budget')
        raw = source.read() if limit is None else source.read(limit + 1)
    if limit is not None and len(raw) > limit:
        raise ValueError('Evidence file exceeds the parsing budget')
    return raw


def _write(root, relative, raw):
    """Worker-owned artifact writes, with anchored/no-follow POSIX components."""
    root = Path(root).absolute()
    parts = _parts(relative)
    if root.is_symlink():
        raise ValueError('Artifact root must be a real directory')
    root.mkdir(parents=True, exist_ok=True)
    if os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open('/', flags)
        temporary = '.pipeline-' + uuid.uuid4().hex + '.tmp'
        try:
            for component in (*root.parts[1:], *parts[:-1]):
                try: child = os.open(component, flags, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    child = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            opened = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(opened, 'wb') as target:
                target.write(raw); target.flush(); os.fsync(target.fileno())
            os.replace(temporary, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try: os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError: pass
            os.close(fd)
    else:
        parent = root
        for component in parts[:-1]:
            parent /= component
            if parent.is_symlink(): raise ValueError('Artifact directory contains a link')
            parent.mkdir(exist_ok=True)
        target = parent / parts[-1]
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError('Artifact target escapes its root')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
    return raw


def _json(root, relative, value):
    return _write(root, relative, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def _ref(job_root, stage, relative, raw, media_type):
    return {'path':(stage.relative_to(job_root) / relative).as_posix(),
            'sha256':hashlib.sha256(raw).hexdigest(), 'media_type':media_type}


def _media_ref(job_root, stage, relative, media_type):
    """Hash media incrementally; a movie must not be loaded into worker memory."""
    digest = hashlib.sha256()
    with _open(stage, _parts(relative)) as source:
        head = source.read(12)
        if media_type == 'video/mp4' and (len(head) < 12 or head[4:8] != b'ftyp'):
            raise ValueError('Recording file is missing or not MP4')
        if media_type == 'image/png' and not head.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Screenshot is missing or not PNG')
        digest.update(head)
        for chunk in iter(lambda:source.read(1024*1024),b''): digest.update(chunk)
    return {'path':(stage.relative_to(job_root)/relative).as_posix(),
            'sha256':digest.hexdigest(),'media_type':media_type}


def _prepare(catalog, manifest, decision, *, full=False):
    validate('RequirementsCatalog', catalog)
    validate('ScenarioManifest', manifest)
    validate('PlannerDecision', decision)
    conditions = {c['id']:c['requirement_id'] for c in catalog['conditions']}
    requirements = {r['id'] for r in catalog['requirements']}
    if len(conditions) != len(catalog['conditions']): raise ValueError('Duplicate catalog condition ID')
    selected = {cid for action in decision['actions'] for cid in action['condition_ids']}
    regressions=set(decision['regression_condition_ids'])
    if not regressions<=conditions.keys():raise ValueError('Regression selection references an unknown condition')
    if not selected <= conditions.keys(): raise ValueError('Decision references an unknown condition')
    scenarios, seen = [], set()
    for scenario in manifest['scenarios']:
        sid = scenario['id']
        if not SAFE_ID.fullmatch(sid) or len(sid) > 64 or sid in seen:
            raise ValueError('Unsafe or duplicate verification scenario ID')
        seen.add(sid)
        ids = scenario['condition_ids']
        if not ids or len(set(ids)) != len(ids) or not set(ids) <= conditions.keys():
            raise ValueError('Scenario must reference known, distinct conditions')
        refs = list(dict.fromkeys(conditions[cid] for cid in ids))
        if not set(refs) <= requirements: raise ValueError('Condition references an unknown requirement')
        checkpoints = {c['id'] for c in scenario['checkpoints']}
        if not checkpoints or len(checkpoints) != len(scenario['checkpoints']):
            raise ValueError('Scenario requires distinct checkpoints')
        for checkpoint in scenario['checkpoints']:
            if not set(checkpoint['condition_ids']) <= set(ids):
                raise ValueError('Checkpoint references a condition outside its scenario')
        expected = scenario['execution']['expected_tests']
        pairs = [(t['classname'], t['name']) for t in expected]
        if not pairs or len(set(pairs)) != len(pairs): raise ValueError('Distinct expected test cases are required')
        bound = set()
        for binding in scenario['execution']['checkpoint_tests']:
            if binding['checkpoint_id'] not in checkpoints or binding['checkpoint_id'] in bound:
                raise ValueError('Unknown or duplicate checkpoint binding')
            if (binding['classname'], binding['name']) not in pairs:
                raise ValueError('Checkpoint binding must name an expected test case')
            bound.add(binding['checkpoint_id'])
        if not full and not set(ids)&(selected|regressions):continue
        scenarios.append((scenario, {'id':sid, 'kind':scenario['execution']['kind'],
            'task':scenario['execution']['gradle_task'], 'requirement_ids':refs, 'expected_tests':deepcopy(expected)}))
    # The planner selects related regressions; only final verification selects all.
    scenarios.sort(key=lambda item: not bool(set(item[0]['condition_ids']) & selected))
    return scenarios


def _junit(stage, scenario, item, job_root):
    """Reparse originals; ignore model-authored/persisted testcase verdicts."""
    proof = item.get('assertion_evidence')
    if not isinstance(proof, dict) or proof.get('expected_tests') != scenario['expected_tests']:
        raise ValueError('Exact executed-test inventory is unavailable or does not match the request')
    reports = proof.get('reports', [])
    if not isinstance(reports, list): raise ValueError('Invalid JUnit report inventory')
    cases, artifacts, total, suite_failed = {}, [], 0, False
    for descriptor in reports:
        if not isinstance(descriptor, dict): raise ValueError('Invalid JUnit report descriptor')
        relative = descriptor.get('file')
        prefix = 'acceptance/' + scenario['id'] + '/assertions/'
        if not isinstance(relative, str) or not relative.startswith(prefix) or not relative.endswith('.xml'):
            raise ValueError('JUnit report is outside the selected scenario')
        raw = _read(stage, relative, MAX_REPORT_BYTES)
        total += len(raw)
        if total > MAX_REPORT_TOTAL: raise ValueError('JUnit report collection exceeds the parsing budget')
        if hashlib.sha256(raw).hexdigest() != descriptor.get('sha256'):
            raise ValueError('JUnit report hash mismatch')
        if b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('Unsafe JUnit XML declaration')
        document = ET.fromstring(raw.decode('utf-8-sig'))
        if document.tag not in ('testsuite', 'testsuites'): raise ValueError('Not a JUnit test report')
        for suite in document.iter('testsuite'):
            for key in ('failures','errors'):
                value = suite.get(key,'0')
                if not re.fullmatch(r'[0-9]+',value): raise ValueError('Invalid JUnit suite result count')
                suite_failed |= int(value) > 0
        artifact = _ref(job_root, stage, relative, raw, 'application/xml')
        artifacts.append(artifact)
        for node in document.iter('testcase'):
            key = (node.get('classname'), node.get('name'))
            if not all(isinstance(x, str) and x for x in key): continue
            failure = node.find('failure')
            if failure is None: failure = node.find('error')
            skip = node.find('skipped')
            status = 'FAIL' if failure is not None else 'SKIPPED' if skip is not None else 'PASS'
            reason = failure if failure is not None else skip
            message = '' if reason is None else reason.get('message') or reason.text or ''
            cases.setdefault(key, []).append({'status':status, 'message':message, 'report':artifact})
    return cases, artifacts, suite_failed


def _media(stage, scenario, item, job_root):
    artifacts, errors = [], []
    quality = item.get('video_capture', {})
    quality = quality if isinstance(quality, dict) else {}
    needs_video = scenario['execution']['kind'] == 'gameplay' or 'video' in scenario['media']
    if needs_video:
        try:
            relative = item.get('video')
            if (quality.get('status') != 'PASS' or quality.get('scenario_window_complete') is not True
                    or not isinstance(relative, str) or not relative.startswith('acceptance/' + scenario['id'] + '/')):
                raise ValueError('No verified recording of the complete scenario window')
            artifacts.append(_media_ref(job_root, stage, relative, 'video/mp4'))
        except (OSError, ValueError, TypeError) as exc: errors.append(str(exc))
    if 'screenshot' in scenario['media']:
        try:
            still = quality.get('still_frame')
            if quality.get('status') != 'PASS' or not isinstance(still, str):
                raise ValueError('No verified screenshot for this scenario')
            relative = 'acceptance/' + scenario['id'] + '/' + still
            artifacts.append(_media_ref(job_root, stage, relative, 'image/png'))
        except (OSError, ValueError, TypeError) as exc: errors.append(str(exc))
    return artifacts, errors


def verify(ws, stage, jid, catalog, manifest, decision, *, sandbox, poll, timeout, eula, full=False):
    """Return ``(strict VerificationResult, legacy runner report)``.

    Artifact refs are job-root-relative. Sandbox owns all actual execution;
    callers must hold the project lock and honor its cleanup failures.
    """
    ws, stage = Path(ws), Path(stage)
    round_stage = stage.parent if stage.name == 'full-verification' else stage
    match = re.fullmatch(r'round-([0-9]+)', round_stage.name)
    if not match: raise ValueError('Verification stage must name its round')
    if ws.is_symlink() or stage.is_symlink(): raise ValueError('Verification roots must not be symlinks')
    job_root = round_stage.parent
    source = acceptance.source_fingerprint(ws)
    scope = {**catalog['scope'], 'source_revision':source, 'round':int(match[1])}
    if manifest['scope']['source_revision'] != source:
        raise ValueError('Scenario manifest does not describe the current source snapshot')
    if any(value['scope']['spec_revision'] != scope['spec_revision'] for value in (manifest, decision)):
        raise ValueError('Verification inputs refer to different specification revisions')
    if any(value['scope']['project_id'] != scope['project_id'] for value in (manifest, decision)):
        raise ValueError('Verification inputs refer to different projects')
    selected = _prepare(catalog, manifest, decision,full=full)
    stage.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for scenario, _ in selected:
        test = scenario['test_file']
        raw = _read(ws, test['path'])
        if hashlib.sha256(raw).hexdigest() != test['sha256']:
            raise ValueError('Scenario test file hash does not match its snapshot')
        relative = 'test-sources/' + test['path']
        _write(stage, relative, raw)
        snapshots.append(_ref(job_root, stage, relative, raw, test['media_type']))
    request = {'schema_version':2, 'spec_sha256':scope['spec_revision'],
               'catalog_ids':[r['id'] for r in catalog['requirements']],
               'scenarios':[value for _, value in selected],'skip_global_gameplay':True}
    acceptance.write_workspace_json(ws, 'acceptance-request.json', request)
    request_raw = _json(stage, 'pipeline-runner-request.json', request)
    # This descriptor records the exact input revision and immutable test files.
    # DockerSandbox independently transfers and executes the workspace snapshot.
    snapshot_raw = _json(stage, 'source-snapshot.json', {'scope':scope, 'test_files':snapshots})
    manifest_raw = _json(stage, 'scenario-manifest.json', manifest)
    wanted = {cid for action in decision['actions'] for cid in action['condition_ids']}
    verification_request = {'contract':'VerificationRequest','version':'v1','scope':scope,
        'source_snapshot_ref':_ref(job_root, stage, 'source-snapshot.json', snapshot_raw, 'application/json'),
        'scenario_manifest_ref':_ref(job_root, stage, 'scenario-manifest.json', manifest_raw, 'application/json'),
        'scenario_ids':[s['id'] for s, _ in selected if full or set(s['condition_ids']) & wanted],
        'regression_scenario_ids':[] if full else [s['id'] for s, _ in selected if not set(s['condition_ids']) & wanted],
        'run_kind':'full' if full else 'targeted', 'runner_profile':'isolated-gradle-verification'}
    validate('VerificationRequest', verification_request)
    request_raw = _json(stage, 'pipeline-verification-request.json', verification_request)
    # Do not accept a previous attempt's result if this invocation never emits it.
    (stage / 'acceptance_results.json').unlink(missing_ok=True)
    report = sandbox.verify(ws, stage, timeout, jid, poll, eula=eula)
    source_changed = acceptance.source_fingerprint(ws) != source
    errors = []
    from common.verification import infrastructure_failure
    blocker=infrastructure_failure(report)
    if blocker:errors.append(str(blocker.get('label','verification'))+': '+str(blocker.get('detail',blocker.get('tail','Execution infrastructure error'))))
    if source_changed: errors.append('Source snapshot changed during independent verification')
    build = next((c.get('status') for c in report.get('checks', []) if c.get('label') == 'gradle_build'), 'NOT_EXECUTED')
    build = build if build in ('PASS','FAIL','NOT_EXECUTED') else 'ERROR'
    try:
        raw_results = acceptance.read_json(stage, 'acceptance_results.json', 16 * 1024 * 1024)
        if raw_results.get('spec_sha256') != scope['spec_revision']:
            raise ValueError('Runner results refer to another specification revision')
        rows = raw_results.get('scenarios')
        if not isinstance(rows, list): raise ValueError('Runner scenario inventory is missing')
        grouped = {}
        for row in rows:
            if isinstance(row, dict): grouped.setdefault(row.get('id'), []).append(row)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        grouped = {}
        # A compile failure or a broken test reporter belongs to the coder's
        # next repair. Only a failed trusted supervisor is an infrastructure stop.
        report.setdefault('pipeline_diagnostics',[]).append('Acceptance evidence unavailable: ' + str(exc))
    scenario_results, originals = [], []
    for scenario, legacy in selected:
        item_list = grouped.get(scenario['id'], [])
        item = item_list[0] if len(item_list) == 1 else {}
        case_error = None
        cases, suite_failed = {}, False
        try:
            if len(item_list) != 1: raise ValueError('Scenario has no unique fresh runner result')
            if item.get('requirement_ids') != legacy['requirement_ids']:
                raise ValueError('Executed scenario requirement mapping does not match the request')
            if source_changed: raise ValueError('Source snapshot changed during verification')
            cases, reports, suite_failed = _junit(stage, legacy, item, job_root)
            originals.extend(reports)
        except (OSError, ValueError, TypeError, UnicodeError, ET.ParseError) as exc:
            case_error = str(exc)
        bindings = {b['checkpoint_id']:b for b in scenario['execution']['checkpoint_tests']}
        observations = []
        for checkpoint in scenario['checkpoints']:
            binding = bindings.get(checkpoint['id'])
            rows = [] if binding is None else cases.get((binding['classname'], binding['name']), [])
            evidence = []
            if case_error:
                status, actual = 'ERROR', case_error
            elif binding is None:
                status, actual = 'NOT_EXECUTED', 'No exact JUnit identity is bound to this checkpoint'
            elif not rows:
                status, actual = 'NOT_EXECUTED', 'Expected test case is absent from fresh JUnit; execution may have stopped earlier'
            elif len(rows) != 1:
                status, actual = 'ERROR', 'Multiple JUnit cases match this checkpoint; outcome is ambiguous'
            else:
                status = rows[0]['status']
                actual = binding['classname'] + '#' + binding['name'] + ': ' + (rows[0]['message'] or status)
                proof = {'checkpoint_id':checkpoint['id'], 'test':binding, 'status':status,
                         'actual':actual, 'report':rows[0]['report']}
                relative = 'checkpoint-evidence/' + hashlib.sha256((scenario['id']+'\0'+checkpoint['id']).encode()).hexdigest() + '.json'
                raw = _json(stage, relative, proof)
                lines = raw.decode('utf-8').splitlines()
                line = next(i for i, value in enumerate(lines, 1) if '"status":' in value)
                evidence.append({'artifact':_ref(job_root, stage, relative, raw, 'application/json'),
                                 'start_line':line,'end_line':line,'excerpt':lines[line-1]})
            observations.append({'checkpoint_id':checkpoint['id'],'status':status,'actual':actual,'evidence_refs':evidence})
        media, media_errors = _media(stage, scenario, item, job_root)
        statuses = [o['status'] for o in observations]
        expected_rows = [cases.get((t['classname'],t['name']),[]) for t in legacy['expected_tests']]
        if case_error or 'ERROR' in statuses or any(len(rows)>1 for rows in expected_rows): status = 'ERROR'
        elif 'FAIL' in statuses or item.get('test_status') in ('FAIL','ERROR','TIMEOUT') or item.get('status') == 'FAIL': status = 'FAIL'
        elif suite_failed or any(c['status'] == 'FAIL' for rows in cases.values() for c in rows): status = 'FAIL'
        elif not statuses or 'NOT_EXECUTED' in statuses or any(not rows for rows in expected_rows): status = 'NOT_EXECUTED'
        elif 'SKIPPED' in statuses or any(rows[0]['status']=='SKIPPED' for rows in expected_rows): status = 'SKIPPED'
        elif item.get('test_status') != 'PASS' or item.get('status') != 'TEST_PASSED': status = 'NOT_EXECUTED'
        else: status = 'PASS'
        if case_error:report.setdefault('pipeline_diagnostics',[]).append(scenario['id'] + ': ' + case_error)
        report.setdefault('pipeline_diagnostics',[]).extend(scenario['id'] + ': ' + value for value in media_errors)
        scenario_results.append({'scenario_id':scenario['id'],'status':status,
                                 'checkpoints':observations,'media_refs':media})
    raw_reports = list({r['path']:r for r in originals}.values())
    try:
        raw = _read(stage, 'acceptance_results.json', 16 * 1024 * 1024)
        raw_reports.append(_ref(job_root, stage, 'acceptance_results.json', raw, 'application/json'))
    except (OSError,ValueError): pass
    value = {'contract':'VerificationResult','version':'v1','scope':scope,
        'request_ref':_ref(job_root, stage, 'pipeline-verification-request.json', request_raw, 'application/json'),
        'runner_run_id':'RUN-' + uuid.uuid4().hex, 'build_status':build,
        'scenario_results':scenario_results,'infrastructure_errors':list(dict.fromkeys(errors)),
        'raw_report_refs':raw_reports}
    validate('VerificationResult', value)
    _json(stage, 'pipeline-verification-result.json', value)
    return value, report
