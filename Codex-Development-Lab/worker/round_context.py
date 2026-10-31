"""Small cross-round handoffs; only independent checks may resolve failures.

The job artifact copy is controller-owned. Workspace copies and agent handoffs
are documents to consult, never evidence that a requirement has passed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone

from common import acceptance, memory_archive
from common.error_history import reconcile_legacy_errors
from common import acceptance_progress, acceptance_tasks, task_assignment
from common import work_routing, verification_feedback, functional_priority,round_progress
from common.history import _atomic, _open
from common.verification import infrastructure_failure

MAX_DOCUMENT = 1024 * 1024
MAX_ERRORS = 30


def failure_excerpt(value,limit=800):
    text=str(value or '')
    lines=text.splitlines()
    relevant=[]
    for index,line in enumerate(lines):
        if re.search(r'Caused by:|(?:Error|Exception):|AssertionError|JUnit failure:|^> (?!Task\b|Configure\b|Run with\b|Get more help\b)|What went wrong:|Execution failed|План отклон',line):
            relevant.extend(lines[index:min(index+2,len(lines))])
    return ('\n'.join(relevant) or text[-limit:])[:limit].strip()


def compact_assignment(value):
    current=value.get('current_task')
    result={key:value.get(key) for key in ('current_task_id','status','selection_reason',
        'pending_task_count','unresolved_failure_count','manual_review_requirement_count')}
    result['completed_task_count']=len(value.get('completed_task_ids',[]))
    result['regression_requirement_count']=len(value.get('regression_requirement_ids',[]))
    result['current_task']=None
    if isinstance(current,dict):
        members=current.get('requirement_ids',[]);unresolved=current.get('unresolved_requirement_ids',[])
        result['current_task']={'id':current.get('id'),'title':str(current.get('title',''))[:140],
            'requirement_ids':members[:64],'unresolved_requirement_ids':unresolved[:64],
            'requirement_count':len(members),'unresolved_requirement_count':len(unresolved),
            'omitted_unresolved_count':max(0,len(unresolved)-64)}
    return result


def save_round_snapshot(output_dir, number, phase, workspace=None):
    """Capture actual controller state now; never invent historical snapshots."""
    if type(number) is not int or not 1<=number<=2**53-1:
        raise ValueError('Invalid snapshot round')
    if phase not in ('before_development','after_verification'):
        raise ValueError('Invalid snapshot phase')
    root=Path(output_dir);folder=root/f'round-{number:02d}'
    if root.is_symlink() or folder.is_symlink() or not folder.is_dir():
        raise ValueError('Snapshot requires an existing real round directory')
    memory=_read(root,'round-memory.json')
    if memory is None:return
    memory.update(snapshot_round=number,snapshot_phase=phase,
                  snapshot_at=datetime.now(timezone.utc).isoformat())
    filename='round-memory-start.json' if phase=='before_development' else 'round-memory.json'
    _atomic(folder/filename,memory)
    if workspace is not None and phase=='after_verification' and _read(workspace,'agent-handoff.json') is not None:
        handoff=_handoff(workspace,memory['spec_sha256'],root,number)
        if handoff.get('available'):
            development=_read(folder,'development_complete.json') or {}
            state=_read(folder,'acceptance.json') or {}
            source=state.get('source_sha256')
            # A real coder turn can establish a new note's origin. A test-only
            # round must preserve the earlier origin, or leave it unknown.
            if (handoff.get('source_round') is None and development
                    and development.get('kind') in (None,'model')
                    and isinstance(source,str) and re.fullmatch('[0-9a-f]{64}',source)):
                handoff.update(source_round=number,source_sha256=source,
                               freshness='current',stale=False)
            _atomic(folder/'agent-handoff.json',handoff)


def _read(root, filename):
    try:
        with _open(root, (filename,)) as handle:
            raw = handle.read(MAX_DOCUMENT + 1)
        value = json.loads(raw) if len(raw) <= MAX_DOCUMENT else None
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def _text(value, maximum):
    return value[:maximum] if isinstance(value, str) else ''


def _checks(report):
    values = report.get('checks', []) if isinstance(report, dict) else []
    checks=[item for item in values if isinstance(item,dict)] if isinstance(values,list) else []
    if isinstance(report,dict) and not any(c.get('label')=='video_capture' for c in checks):
        captures=[report.get('video_capture')]
        scenarios=report.get('acceptance_results',{})
        scenarios=scenarios.get('scenarios',[]) if isinstance(scenarios,dict) else []
        if isinstance(scenarios,list):
            captures.extend(s.get('video_capture') for s in scenarios if isinstance(s,dict))
        # Successful capture is nested in the trusted report, not a top-level
        # check. Preserve that real recovery evidence in error history too.
        statuses=[c.get('status') for c in captures if isinstance(c,dict)]
        if 'PASS' in statuses and not any(s in ('FAIL','ERROR','TIMEOUT') for s in statuses):
            checks.append({'label':'video_capture','status':'PASS'})
    raw=report.get('acceptance_results',{}) if isinstance(report,dict) else {}
    for scenario in raw.get('scenarios',[]) if isinstance(raw,dict) else []:
        if not isinstance(scenario,dict) or not acceptance.ID.fullmatch(str(scenario.get('id',''))):continue
        status=scenario.get('status')
        checks.append({'label':'scenario:'+scenario['id'],
            'status':'PASS' if status=='TEST_PASSED' else 'FAIL' if status=='FAIL' else 'NOT_TESTED',
            'detail':str(scenario.get('detail',''))[:800],
            'tail':failure_excerpt(str(scenario.get('detail',''))+'\n'+str(scenario.get('command_tail','')))})
    if (isinstance(report,dict) and report.get('scenario_failures_remaining')==0
            and isinstance(raw,dict) and raw.get('scenarios')
            and all(s.get('status')=='TEST_PASSED' for s in raw['scenarios'])):
        checks.append({'label':'acceptance_scenarios','status':'PASS'})
    return checks


def _signature(label, detail):
    # Collapse timestamps and transient object addresses, retaining concrete
    # exception/path text so distinct failures are not merged indiscriminately.
    normalized = re.sub(r'\b\d{4}-\d\d-\d\d[T ][0-9:.+-]+Z?\b', '<time>', detail)
    normalized = re.sub(r'0x[0-9a-fA-F]+', '<address>', normalized)
    normalized = ' '.join(normalized.split())
    return hashlib.sha256((label + '\n' + normalized).encode()).hexdigest()


def _apply_report(memory, number, report):
    revision = hashlib.sha256(('context-v5:'+json.dumps(report, sort_keys=True, ensure_ascii=True)).encode()).hexdigest()
    revisions = memory.setdefault('report_revisions', {})
    if revisions.get(str(number)) == revision:
        return
    errors = memory['errors']
    blocker = infrastructure_failure(report)
    for check in _checks(report):
        label = _text(check.get('label'), 96)
        status = _text(check.get('status'), 32)
        if not label:
            continue
        if status == 'PASS':
            for error in errors:
                if (error['label'] == label and error['status'] in ('OPEN','HISTORICAL')
                        and error['last_round'] <= number):
                    error.update(status='RESOLVED', resolved_round=number,
                                 resolution=f'Independent round {number}: check {label} PASS')
            continue
        if status not in ('FAIL', 'ERROR', 'TIMEOUT'):
            continue
        if label == 'verification_session' and blocker is None:
            continue
        detail = failure_excerpt(_text(check.get('detail'), MAX_DOCUMENT)+'\n'+
                  _text(check.get('tail'), MAX_DOCUMENT))
        if not detail:
            detail = f"{status}; exit_code={check.get('exit_code') if isinstance(check.get('exit_code'), int) else 'unknown'}"
        signature = _signature(memory.get('spec_sha256', '') + '\n' + label, detail)
        found = next((item for item in errors if item['signature'] == signature), None)
        if found is None:
            found = {'signature': signature, 'label': label, 'detail': detail,
                     'first_round': number, 'repeat_count': 0, 'observed_rounds': []}
            errors.append(found)
        seen = found.setdefault('observed_rounds', [found['last_round']] if 'last_round' in found else [])
        if number not in seen:
            found['repeat_count'] += 1
            seen.append(number)
            found['observed_rounds'] = seen
        found.update(first_round=min(number, found.get('first_round', number)),
                     last_round=max(number, found.get('last_round', number)), status='OPEN')
        found.pop('resolved_round', None)
        found.pop('resolution', None)
    memory['errors'] = sorted(errors, key=lambda item: item['last_round'])
    checks = _checks(report)
    # Old aggregate failures cannot be equated to any single named scenario.
    # Retain them for investigation without presenting them as current blockers.
    reconcile_legacy_errors(memory,number,checks)
    # An executed build with no infrastructure error proves startup recovered,
    # including a normal exit 1 reporting project compilation/test failures.
    ran_build = next((item for item in checks if item.get('label') in
                     ('gradle_build', 'maven_test', 'pytest', 'npm_test')
                     and item.get('status') in ('PASS', 'FAIL', 'TIMEOUT')
                     and isinstance(item.get('exit_code'), int)), None)
    if ran_build:
        for error in memory['errors']:
            infrastructure_label = error['label']
            if (infrastructure_label in ('verification_session', 'verification_report')
                    and error['last_round'] <= number
                    and blocker is None):
                error.update(status='RESOLVED', resolved_round=number, resolution=
                    f"Independent round {number}: {ran_build['label']} executed "
                    f"({ran_build['status']}); {infrastructure_label} startup recovered")
    memory['last_processed_round'] = max(number, memory.get('last_processed_round', 0))
    revisions[str(number)] = revision
    memory['report_revisions'] = revisions


def _document_index(text, digest):
    lines = text.splitlines()
    sections, fence = [], None
    for number, line in enumerate(lines, 1):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        match = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', line) if fence is None else None
        if match:
            sections.append({'title': match[2][:140], 'level': len(match[1]),
                             'start_line': number, 'end_line': len(lines)})
    ancestors = []
    for section in sections:
        while ancestors and ancestors[-1]['level'] >= section['level']:
            ancestors.pop()['end_line'] = section['start_line'] - 1
        ancestors.append(section)
    return {'schema_version': 1, 'spec_sha256': digest, 'path': 'ТЗ.md',
            'total_lines': len(lines), 'sections': sections[:120],
            'truncated_count': max(0, len(sections) - 120)}


def _archived_summary(output_dir, number):
    """Migration only: public final main message, never command/tool output."""
    if number < 1:
        return ''
    folder = Path(output_dir) / f'round-{number:02d}'
    if folder.is_symlink() or _read(folder, 'verification_complete.json') is None:
        return ''
    latest = ''
    try:
        with _open(folder, ('codex.jsonl',)) as handle:
            if os.fstat(handle.fileno()).st_size > 2 * MAX_DOCUMENT:
                return ''
            while True:
                line = handle.readline(MAX_DOCUMENT + 1)
                if not line:
                    break
                if len(line) > MAX_DOCUMENT:
                    return ''
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if not isinstance(event, dict) or event.get('type') != 'item.completed':
                    continue
                item = event.get('item')
                if (isinstance(item, dict) and item.get('type') == 'agent_message'
                        and item.get('agent_id', event.get('agent_id', 'main')) == 'main'):
                    latest = _text(item.get('text'), 1800)
    except (OSError, ValueError):
        return ''
    return latest


def _handoff(workspace, digest, output_dir, previous_round):
    raw = _read(workspace, 'agent-handoff.json')
    if not raw:
        summary = _archived_summary(output_dir, previous_round)
        return {'available': bool(summary), 'summary': summary,
                'source': f'Archived public final main message from round {previous_round}',
                'provenance': 'UNVERIFIED agent claims, not independent evidence'}
    declared_digest = raw.get('spec_sha256')
    if declared_digest and declared_digest != digest:
        return {'available': False, 'stale': True,
                'provenance': 'Agent handoff omitted: different specification'}
    result = {'available': True, 'stale': None, 'freshness': 'unknown',
              'source_round': None, 'source_sha256': None,
              'specification_bound': declared_digest == digest,
              'provenance': 'UNVERIFIED agent claims and proposed solutions; never test evidence'}
    # Never recursively copy arbitrary agent data/private protocol fields.
    result['summary'] = _text(raw.get('summary'), 1200)
    for key in ('next_steps', 'decisions', 'errors_and_solutions'):
        values = raw.get(key)
        if isinstance(values, list):
            result[key] = []
            for item in values[:5]:
                if isinstance(item, str):
                    result[key].append(item[:350])
                elif key == 'errors_and_solutions' and isinstance(item, dict):
                    result[key].append({field: _text(item.get(field), 250)
                                        for field in ('error', 'solution', 'evidence')})
        elif isinstance(values, str):
            result[key] = values[:600]
    # Workspace declarations are claims. Locate their controller-owned snapshot
    # rather than relabelling an unchanged handoff after verification-only rounds.
    for number in range(max(1, previous_round-127), previous_round+1):
        saved = _read(Path(output_dir)/f'round-{number:02d}', 'agent-handoff.json')
        if not saved or any(saved.get(k) != result.get(k) for k in
                ('summary', 'next_steps', 'decisions', 'errors_and_solutions')):
            continue
        origin=saved.get('source_round')
        source=saved.get('source_sha256')
        if type(origin) is not int:
            development=_read(Path(output_dir)/f'round-{number:02d}', 'development_complete.json') or {}
            if development.get('kind') not in (None, 'model') or not development:
                continue
            origin=number
            state=_read(Path(output_dir)/f'round-{number:02d}', 'acceptance.json') or {}
            source=state.get('source_sha256')
        if not isinstance(source,str) or not re.fullmatch('[0-9a-f]{64}',source):
            continue
        result.update(source_round=origin, source_sha256=source)
        current_source=acceptance.source_fingerprint(workspace)
        freshness=('different_source' if source != current_source else
                   'older_round' if origin < previous_round else 'current')
        result.update(freshness=freshness, stale=freshness != 'current')
        break
    return result


def _full_archived_note(folder):
    """Migration-only public main message; never export private/tool events."""
    latest = ''
    try:
        with _open(folder, ('codex.jsonl',)) as source:
            for line in source:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if not isinstance(event, dict) or event.get('type') != 'item.completed':
                    continue
                item = event.get('item')
                if (isinstance(item, dict) and item.get('type') == 'agent_message'
                        and item.get('agent_id', event.get('agent_id', 'main')) == 'main'
                        and isinstance(item.get('text'), str)):
                    latest = item['text']
    except (OSError, ValueError):
        pass
    return latest


def scenario_facts(plan, ledger, state, focus_ids=()):
    """Expose current verifier statuses separately from historical observations."""
    statuses={s['id']:s.get('status','NOT_TESTED') for r in state.get('requirements',[])
              for s in r.get('scenarios',[])}
    focus=set(focus_ids)
    ordered=sorted(plan.get('scenarios',[]), key=lambda s:(
        not focus.intersection(s.get('requirement_ids',[])),
        statuses.get(s['id']) != 'FAIL', s['id']))
    def record(value):
        if not isinstance(value,dict):return None
        result=value.get('result') or {}
        from common.failure_details import summarize
        proof=result.get('assertion_evidence',{})
        definition={'id':result.get('id'),'requirement_ids':result.get('requirement_ids',[]),
                    'expected_tests':proof.get('expected_tests',[]) if isinstance(proof,dict) else []}
        details=summarize(definition,result) if result.get('status')=='FAIL' else None
        if details:
            details={k:details[k] for k in ('counts','root_cause_status','observed_failure')}
            # Full per-check facts are available in .lab-feedback/index.json;
            # keep routine scenario context compact.
        return {**{k:value.get(k) for k in ('round','source_sha256','definition_sha256')},
                'status':result.get('status'), 'test_status':result.get('test_status'),
                'failure_details':details}
    items=[]
    for scenario in ordered[:64]:
        entry=ledger.get('scenarios',{}).get(scenario['id'],{})
        items.append({'id':scenario['id'], 'requirement_ids':scenario.get('requirement_ids',[])[:64],
            'status':statuses.get(scenario['id'],'NOT_TESTED'),
            'definition_sha256':acceptance_progress.definition_digest(scenario),
            'latest':record(entry.get('latest')), 'last_pass':record(entry.get('last_pass')),
            'evidence_source':'controller acceptance-progress.json'})
    return {'items':items, 'total':len(ordered), 'omitted':max(0,len(ordered)-len(items))}


def planner_input_revision(catalog, plan, ledger, source, directive, current_task):
    """Fingerprint factual inputs, excluding notes, clocks and model selection."""
    facts={'spec_sha256':catalog['spec_sha256'], 'source_sha256':source,
           'plan':plan, 'ledger':ledger.get('scenarios',{}),
           'directive':directive, 'current_task':current_task}
    return hashlib.sha256(json.dumps(facts,sort_keys=True,separators=(',',':'),
                                    ensure_ascii=False).encode()).hexdigest()


def refresh_archive(ws, output_dir, n, digest, legacy_memory=None, previous=None):
    """Migrate/refresh memory without changing scheduling or running an agent.

    Every old controller snapshot and verification report is examined on the
    first migration. Later calls read only changed reports, then replay from
    durable report records in chronological order when a revision changes.
    """
    ws, output_dir = Path(ws), Path(output_dir)
    archive = memory_archive.load(output_dir)
    rounds = archive['rounds']
    prior_latest = max((item['round'] for item in rounds.values()), default=0)
    migrated = memory_archive.read_json(output_dir, 'memory-archive/index.json') is not None
    legacy = legacy_memory or memory_archive.read_json(output_dir, 'round-memory.json') or {}
    errors = {item['signature']: item for item in archive['errors']
              if isinstance(item.get('signature'), str)}
    notes = {item['id']: item for item in archive['notes'] if isinstance(item.get('id'), str)}

    def add_note(saved, observed_round):
        if saved and saved['id'] not in notes:
            saved['first_observed_round'] = observed_round
            saved['observed_order'] = max((item.get('observed_order', 0) for item in notes.values()), default=0) + 1
            notes[saved['id']] = saved

    def import_memory(value, source_ref):
        if not isinstance(value, dict):
            return
        spec = value.get('spec_sha256', digest)
        if not isinstance(spec, str) or not re.fullmatch('[0-9a-f]{64}', spec):
            return
        for error in value.get('errors', []) if isinstance(value.get('errors'), list) else []:
            if (not isinstance(error, dict) or not re.fullmatch('[0-9a-f]{64}', str(error.get('signature', '')))
                    or error.get('status') not in ('OPEN', 'RESOLVED', 'HISTORICAL')
                    or not isinstance(error.get('label'), str) or not isinstance(error.get('detail'), str)
                    or any(type(error.get(key)) is not int for key in ('first_round', 'last_round', 'repeat_count'))):
                continue
            signature = _signature(spec + '\n' + error['label'], error['detail'])
            if signature not in errors:
                errors[signature] = {**error, 'signature': signature, 'spec_sha256': spec,
                                             'migration_source': source_ref}
                seen = error.get('observed_rounds', [])
                errors[signature]['observed_rounds'] = [number for number in seen
                    if type(number) is int and number >= 0] if isinstance(seen, list) else []
        for item in value.get('recent_agent_notes', []) if isinstance(value.get('recent_agent_notes'), list) else []:
            if not isinstance(item, dict) or (item.get('source_round') is not None and type(item.get('source_round')) is not int):
                continue
            saved = memory_archive.note(item.get('claims'), item.get('source_round'),
                                        item.get('spec_sha256', spec), source_ref)
            if saved:
                add_note(saved, item.get('first_observed_round', item.get('source_round') or 0))

    if not migrated:
        import_memory(legacy, 'round-memory.json')
    changed_rounds = set()
    candidates = []
    for folder in output_dir.iterdir():
        match = re.fullmatch(r'round-([0-9]{2,16})', folder.name)
        if match and int(match[1]) < n and not folder.is_symlink() and folder.is_dir():
            candidates.append((int(match[1]), folder))
    for number, folder in sorted(candidates):
        key = str(number)
        manifest = rounds.setdefault(key, {'round': number,
            'source_report': f'round-{number:02d}/verification_complete.json',
            'agent_report': f'rounds/{number:02d}-verification.json'})
        if 'spec_sha256' not in manifest:
            snapshot = memory_archive.read_json(folder, 'round-memory.json') or memory_archive.read_json(folder, 'round-memory-start.json') or {}
            manifest['spec_sha256'] = snapshot.get('spec_sha256', digest)
        if not migrated:
            for filename in ('round-memory-start.json', 'round-memory.json'):
                import_memory(memory_archive.read_json(folder, filename), f'{folder.name}/{filename}')
        # Stat through _open so symlinked canonical sources are never followed.
        try:
            with _open(folder, ('verification_complete.json',)) as source:
                info = os.fstat(source.fileno())
                stamp = [info.st_mtime_ns, info.st_size]
            if manifest.get('source_stat') != stamp:
                report = memory_archive.read_json(folder, 'verification_complete.json')
                if report is not None:
                    revision = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
                    if revision != manifest.get('report_revision'):
                        changed_rounds.add(number)
                    manifest.update(report_revision=revision, source_stat=stamp)
                    if revision not in manifest.setdefault('report_versions', []):
                        manifest['report_versions'].append(revision)
                    memory_archive.write_json(output_dir, f'memory-archive/reports/{number:02d}-{revision}.json', report)
                    memory_archive.write_json(output_dir, f'memory-archive/reports/{number:02d}.json', report)
        except (OSError, ValueError):
            pass
        # Notes are small independent records, not a rolling eight-entry store.
        raw = memory_archive.read_json(folder, 'agent-handoff.json')
        if raw:
            snapshot = memory_archive.read_json(folder, 'round-memory.json') or {}
            spec = raw.get('spec_sha256', snapshot.get('spec_sha256', digest))
            origin = raw.get('source_round')
            origin = origin if type(origin) is int and 0 <= origin <= number else None
            saved = memory_archive.note(raw, origin, spec, f'{folder.name}/agent-handoff.json')
            if saved:
                add_note(saved, number)
    if n > 1 and previous:
        number = n - 1
        manifest = rounds.setdefault(str(number), {'round': number,
            'source_report': f'round-{number:02d}/verification_complete.json',
            'agent_report': f'rounds/{number:02d}-verification.json'})
        manifest.setdefault('spec_sha256', digest)
        revision = hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest()
        if revision != manifest.get('report_revision'):
            changed_rounds.add(number)
            manifest['report_revision'] = revision
            if revision not in manifest.setdefault('report_versions', []):
                manifest['report_versions'].append(revision)
            memory_archive.write_json(output_dir, f'memory-archive/reports/{number:02d}-{revision}.json', previous)
            memory_archive.write_json(output_dir, f'memory-archive/reports/{number:02d}.json', previous)
    raw = memory_archive.read_json(ws, 'agent-handoff.json')
    if raw:
        trusted = _handoff(ws, digest, output_dir, n - 1)
        origin = trusted.get('source_round')
        saved = memory_archive.note(raw, origin if type(origin) is int else None,
                                    digest, 'workspace/agent-handoff.json')
        if saved:
            if not any(item.get('spec_sha256') == digest and item.get('claim_sha256') == saved['claim_sha256']
                       for item in notes.values()):
                add_note(saved, max(0, n - 1))
    # Recover public main-message legacy notes when no handoff was written.
    if not migrated:
        for number, folder in sorted(candidates):
            if memory_archive.read_json(folder, 'agent-handoff.json'):
                continue
            summary = _full_archived_note(folder)
            if summary:
                saved = memory_archive.note({'summary': summary}, number,
                    rounds[str(number)].get('spec_sha256', digest), f'{folder.name}/codex.jsonl')
                add_note(saved, number)
    current = {'schema_version': 1, 'spec_sha256': digest,
               'errors': [item for item in errors.values() if item.get('spec_sha256', digest) == digest],
               'last_processed_round': 0, 'report_revisions': {}}
    if changed_rounds or not migrated:
        replay_all = not migrated or min(changed_rounds, default=prior_latest + 1) <= prior_latest
        covered = {item['round'] for item in rounds.values() if item.get('report_revision')} if replay_all else set()
        prior_observations = {}
        for error in current['errors']:
            if error['first_round'] in covered and error['last_round'] in covered:
                # A corrected report can change current status, but must not
                # erase an observation retained in an immutable earlier report.
                prior_observations[error['signature']] = (
                    error['first_round'], error['last_round'], error['repeat_count'],
                    set(error.get('observed_rounds', [])))
                error.update(status='HISTORICAL')
                error.pop('resolved_round', None)
                error['resolution'] = 'Historical diagnostic; replaying independent report history'
        # Replaying ALL later reports prevents an edited old failure from
        # reopening an error already resolved by a later independent check.
        for manifest in sorted(rounds.values(), key=lambda item: item['round']):
            if not replay_all and manifest['round'] not in changed_rounds:
                continue
            if manifest.get('spec_sha256', digest) != digest:
                continue
            report = memory_archive.read_json(output_dir, f"memory-archive/reports/{manifest['round']:02d}.json")
            if report is None:
                continue
            _apply_report(current, manifest['round'], report)
        for item in current['errors']:
            if item['signature'] in prior_observations:
                first, last, count, seen = prior_observations[item['signature']]
                observed = set(item.get('observed_rounds', []))
                outside = sum(number < first or number > last for number in observed - seen)
                item['repeat_count'] = max(count + outside, len(observed))
            item['spec_sha256'] = digest
            references = item.setdefault('source_reports', [])
            for number in item.get('observed_rounds', [item['last_round']]):
                for revision in rounds.get(str(number), {}).get('report_versions', []):
                    reference = {'round': number, 'report_sha256': revision,
                        'path': f'memory-archive/reports/{number:02d}-{revision}.json',
                        'original_source': f'round-{number:02d}/verification_complete.json',
                        'agent_path': f'.lab-memory/rounds/{number:02d}-{revision}-verification.json'}
                    if reference not in references:
                        references.append(reference)
            errors[item['signature']] = item
    ordered = sorted(current['errors'], key=lambda item: (item['status'] == 'OPEN', item['last_round']))
    selected = ordered[-MAX_ERRORS:]
    selected.sort(key=lambda item: item['last_round'])
    current['errors'] = [{**{key: value for key, value in item.items()
                              if key not in ('source_reports', 'observed_rounds')},
                          'detail': item['detail'][-800:], 'label': item['label'][:96],
                          'observed_rounds': item.get('observed_rounds', [])[-30:],
                          'archive_path': f".lab-memory/errors/{item['signature']}.json"}
                         for item in selected]
    all_notes = sorted(notes.values(), key=lambda item: (
        item.get('first_observed_round', item.get('source_round') or 0),
        item.get('observed_order', 0), item['id']))
    current_notes = [item for item in all_notes if item['spec_sha256'] == digest]
    current['recent_agent_notes'] = [{**item, 'archive_path': f".lab-memory/notes/{item['id']}.json"}
                                      for item in current_notes[-8:]]
    # Prompt notes have bounded excerpts. Complete claims remain in the archive.
    for item in current['recent_agent_notes']:
        full_claims = item['claims']
        item['claims'] = {key: value[:1200] if isinstance(value, str) else
                          [entry[:350] if isinstance(entry, str) else
                           {field: text[:250] for field, text in entry.items()}
                           for entry in value[:5]]
                          for key, value in item['claims'].items()}
        item['truncated_fields'] = [key for key in full_claims if full_claims[key] != item['claims'][key]]
    exported = memory_archive.persist(output_dir, ws, digest, list(errors.values()), all_notes, rounds)
    current['last_processed_round'] = max((int(key) for key in rounds), default=0)
    current['report_revisions'] = {}
    current['memory_archive'] = {key: exported[key] for key in ('path', 'counts', 'export_errors')}
    current['memory_archive'].update(omitted_errors=len(ordered) - len(selected),
                                    omitted_notes=max(0, len(current_notes) - 8),
                                    current_open_errors=sum(item['status'] == 'OPEN' for item in ordered))
    return {**legacy, **current}


def prepare_context(ws, output_dir, n, previous):
    """Refresh controller memory under the caller's project lock, return a digest.

    ``n`` is the upcoming development round. Legacy reports are all archived.
    Agent-written workspace memory cannot
    overwrite the authoritative artifact copy.
    """
    ws, output_dir = Path(ws), Path(output_dir)
    previous = previous if isinstance(previous, dict) else {}
    if ws.is_symlink() or output_dir.is_symlink():
        raise ValueError('Context roots must be real directories')
    text = acceptance.read_spec(ws.parent)
    digest = hashlib.sha256(text.encode()).hexdigest()
    memory = refresh_archive(ws, output_dir, n, digest, previous=previous)
    index = _document_index(text, digest)
    handoff = _handoff(ws, digest, output_dir, n - 1)
    catalog=acceptance.build_catalog(text)
    plan=acceptance.load_plan(ws,catalog)
    source=acceptance.source_fingerprint(ws)
    ledger=acceptance_progress.load_progress(output_dir,catalog,plan)
    assignment=task_assignment.update(output_dir,catalog,plan,ledger,source,n)
    directive=work_routing.choose_directive(output_dir,catalog,plan,assignment,ledger,n,previous)
    from common.failure_details import hydrate
    feedback_report={**previous,'acceptance_results':{**previous.get('acceptance_results',{})}}
    feedback_scenarios=[]
    definitions={s['id']:s for s in plan.get('scenarios',[])}
    for result in previous.get('acceptance_results',{}).get('scenarios',[]):
        if result.get('status')=='FAIL' and result.get('id') in definitions and n>1:
            result=hydrate(output_dir.parent.parent,output_dir.parent.name,output_dir.name,
                definitions[result['id']],result,round_number=n-1)
        feedback_scenarios.append(result)
    feedback_report['acceptance_results']['scenarios']=feedback_scenarios
    feedback=verification_feedback.export_feedback(ws,output_dir,feedback_report,max(0,n-1))
    feedback_context={'path':feedback['path'],'source_round':feedback.get('source_round'),
        'file_count':len(feedback.get('files',[])),'errors':feedback.get('errors',[])[:4]}
    ledger=task_assignment.validated_ledger(output_dir,ledger,plan)
    directive=functional_priority.select(catalog,plan,assignment,ledger,source,previous,directive)
    state=acceptance.build_state(catalog,plan,
        acceptance_progress.current_results(ledger,catalog,plan,source),source)
    acceptance_progress.decorate_state(state,catalog,plan,ledger,source)
    acceptance_tasks.annotate(state,catalog)
    facts=scenario_facts(plan,ledger,state,(assignment.get('current_task') or {}).get('requirement_ids',[]))
    progress_policy=round_progress.policy(catalog,plan,assignment,ledger,previous,state)
    if progress_policy['enabled']:
        directive={**directive,'progress_policy_revision':hashlib.sha256(
            json.dumps(progress_policy,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
    input_revision=planner_input_revision(catalog,plan,ledger,source,directive,assignment.get('current_task_id'))
    request=task_assignment.request(catalog,plan,ledger,source,assignment,job_root=output_dir)
    scheduled=work_routing.reserve_regression(request,plan,directive)['scenarios']
    target_requirements={rid for scenario in scheduled for rid in scenario['requirement_ids']}
    task_index=[{key:task[key] for key in ('id','title','status','requirement_ids')}
                for task in state['tasks']]
    focus=[assignment['current_task_id']] if assignment.get('current_task_id') else []
    # The full source inventory lives in the generated file; model context
    # carries group summaries and exact upcoming scenarios, not all paragraphs.
    prioritized=sorted(task_index,key=lambda task:task['id'] not in focus)
    compact=[{'id':task['id'],'title':task['title'][:140],'status':task['status'],
              'source_count':len(task['requirement_ids'])} for task in prioritized[:32]]
    task_document={'schema_version':1,'spec_sha256':digest,'source_sha256':source,
        'tasks':state['tasks'],'summary':state['task_summary'],'assignment':assignment}
    acceptance.write_workspace_json(ws,'acceptance-tasks.json',task_document)
    memory['task_summary']=state['task_summary']
    memory['focus_task_ids']=focus[:32]
    assignment_context=compact_assignment(assignment)
    memory['assignment']=assignment_context
    members=(assignment.get('current_task') or {}).get('unresolved_requirement_ids',[])
    mapped={r['id'] for r in plan['requirements'] if r.get('scenario_ids')}
    uncovered={rid for rid,value in plan.get('_coverage',{}).items()
        if any(not c.get('scenario_ids') or c.get('review_status')=='missing' for c in value.get('obligations',[]))}
    unmapped=[rid for rid in members if rid not in mapped or rid in uncovered]
    failures=assignment.get('unresolved_failures',[])
    failed_checks=[c for c in _checks(previous) if c.get('status') in ('FAIL','ERROR','TIMEOUT')]
    if plan.get('notes'):
        action={'kind':'repair_acceptance_plan','target':'acceptance-plan.json','diagnostics':plan['notes'][:4]}
    elif failures:
        action={'kind':'repair_scenario','target':failures[0]['id'],
            'requirement_ids':failures[0]['requirement_ids'][:12],
            'diagnostic':failure_excerpt(str(failures[0].get('detail',''))+'\n'+str(failures[0].get('command_tail','')),1000)}
    elif failed_checks:
        action={'kind':'repair_verification_failure','target':failed_checks[0].get('label'),
            'diagnostic':failure_excerpt(failed_checks[0].get('tail') or failed_checks[0].get('detail'),1000)}
    elif unmapped:
        rid=unmapped[0];requirement=next(r for r in catalog['requirements'] if r['id']==rid)
        action={'kind':'implement_and_map_requirement','target':rid,
            'source':requirement['source']['text'][:1200]}
    elif scheduled:
        action={'kind':'implement_or_verify_scenario','target':scheduled[0]['id'],
            'requirement_ids':scheduled[0]['requirement_ids'][:12]}
    else:action={'kind':'verify_task','target':assignment.get('current_task_id')}
    if directive['kind']=='rotate_requirement':
        rid=directive['required_target'];requirement=next(r for r in catalog['requirements'] if r['id']==rid)
        action={'kind':'implement_and_map_requirement','target':rid,'requirement_ids':[rid],
            'source':requirement['source']['text'][:1200],'reason':directive['reason']}
    elif directive['kind']=='return_to_failure':
        sid=directive['required_target'];scenario=next(s for s in plan['scenarios'] if s['id']==sid)
        failure=next((f for f in failures if f['id']==sid),{})
        action={'kind':'repair_scenario','target':sid,'requirement_ids':scenario['requirement_ids'][:12],
            'reason':directive['reason'],'diagnostic':failure_excerpt(failure.get('detail',''),1000)}
    elif directive['kind']=='implement_functional_goal':
        action={'kind':'implement_and_map_requirement','target':directive['required_target'],
                'requirement_ids':[directive['required_target']],
                'condition_ids':directive['required_condition_ids'],
                'source':directive['source_condition'],'reason':directive['reason']}
    memory['round_action']=action
    memory['round_actions']=[action]
    memory['round_package']={'action_count':1,'reason':'','verification_limit':acceptance.MAX_SCENARIOS_PER_ROUND}
    memory['round_action_round']=n
    memory['scheduling_directive']=directive
    memory['round_progress_policy']=progress_policy
    if isinstance((previous or {}).get('round_progress_outcome'),dict):
        memory['last_round_progress_outcome']=previous['round_progress_outcome']
    memory['verification_feedback']=feedback_context
    memory['acceptance_plan_notes']=plan.get('notes',[])[:4]
    coverage_items=[{'requirement_id':rid,'complete':value['complete'],
        'review_round':value.get('review_round'),
        'conditions':[{'id':c['id'],'source_start':c['start'],'source_end':c['end'],'scenario_ids':c['scenario_ids'],'reviewed':c['reviewed'],
                       'review_status':c.get('review_status'),'reason':c.get('reason','')[:240]}
                      for c in value['obligations'][:24]],
        'conditions_total':len(value['obligations']),'missing_ids':value['missing_ids'][:24],
        'missing_total':len(value['missing_ids'])}
        for rid,value in plan.get('_coverage',{}).items() if rid in members][:12]
    _atomic(output_dir / 'round-memory.json', memory)
    acceptance.write_workspace_json(ws, 'round-memory.json', memory)
    acceptance.write_workspace_json(ws, 'document-index.json', index)
    summary = {'status': _text((previous or {}).get('status'), 48), 'checks': []}
    for item in _checks(previous)[:8]:
        summary['checks'].append({'label': _text(item.get('label'), 96),
                                  'status': _text(item.get('status'), 32),
                                  'tail': failure_excerpt(_text(item.get('tail'), MAX_DOCUMENT) or
                                           _text(item.get('detail'), MAX_DOCUMENT),1000)})
    acceptance_state = (previous or {}).get('acceptance', {})
    if isinstance(acceptance_state, dict) and isinstance(acceptance_state.get('summary'), dict):
        summary['acceptance_counts'] = {str(key)[:32]: value for key, value in
            list(acceptance_state['summary'].items())[:12] if isinstance(value, int)}
    return {'schema_version': 1, 'round': n, 'spec_sha256': digest,
            'source_sha256':source, 'planner_input_revision':input_revision, 'scenario_facts':facts,
            'compatibility_inputs':{'manifest':'compatibility-inputs.json',
                'instructions':'COMPATIBILITY_INPUTS.md'} if (ws/'compatibility-inputs.json').is_file() else None,
            'documents': {'normative_spec': 'ТЗ.md', 'index': 'document-index.json',
                          'tasks':'acceptance-tasks.json',
                          'memory': 'round-memory.json', 'handoff': 'agent-handoff.json',
                          'memory_archive': '.lab-memory/index.json',
                          'verification_feedback':feedback_context['path'],
                          'section_count': len(index['sections']), 'total_lines': index['total_lines']},
            'previous_independent_verification': summary,
            'open_error_count': memory['memory_archive']['current_open_errors'],
            'memory_archive': memory['memory_archive'],
            'recent_open_errors': [{**{key: value for key, value in item.items()
                if key not in ('spec_sha256', 'migration_source', 'observed_rounds')},
                'detail': item['detail'][-400:]} for item in
                memory['errors'] if item['status'] == 'OPEN'][-5:],
            'agent_handoff': handoff,
            'coverage_status':coverage_items,
            'task_index':compact,'task_index_total':len(task_index),
            'task_index_omitted':max(0,len(task_index)-len(compact)),'focus_task_ids':focus[:32],
            'current_task':assignment_context.get('current_task'),
            'task_assignment':assignment_context,
            'round_action':action,'acceptance_plan_notes':plan.get('notes',[])[:4],
            'scheduling_directive':directive,'verification_feedback':feedback_context,
            'round_progress_policy':progress_policy,
            'unresolved_scenario_failures':assignment.get('unresolved_failures',[])[:12],
            'upcoming_scenarios':[{'id':s['id'],'requirement_ids':s['requirement_ids']}
                                  for s in scheduled]}
