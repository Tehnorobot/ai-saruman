"""Reproduce queue/control defects with only the Python standard library.

Run from any directory: python audit/reproduce_queue_ui.py
Application sources, real databases and project directories are never changed.
The API action function is compiled directly from its source AST to avoid the
FastAPI dependency. For the UI issue, actual DB paging is combined with explicit
source evidence of the dashboard request; this script does not emulate a browser.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from common import db


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


def load_action():
    source = REPO / 'app' / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    action = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'action')
    action.decorator_list = []
    namespace = {'db': db, 'HTTPException': HTTPException}
    exec(compile(ast.Module(body=[action], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['action']


def emit(case, **observations):
    print(json.dumps({'case': case, **observations}, ensure_ascii=False))


def create_job():
    return db.create_project('Audit', 'audit.md', b'# An isolated reproduction specification')[1]


def interrupted_control(action):
    for control in ('pause', 'stop'):
        jid = create_job()
        assert db.claim('worker-before-crash')['id'] == jid
        action(jid, control)
        # The process has died; zero makes the lease unambiguously expired.
        db.update(jid, lease_until=0)
        reclaimed = db.claim('worker-after-restart')
        try:
            action(jid, 'resume')
            resume_code = 200
        except HTTPException as exc:
            resume_code = exc.status_code
        row = db.job_row(jid)
        assert reclaimed is None
        assert row['status'] == 'RUNNING' and resume_code == 409
        emit('control_then_worker_crash', control=control, status=row['status'],
             pause_requested=row['pause_requested'], cancel_requested=row['cancel_requested'],
             reclaimed=reclaimed, resume_http_status=resume_code)
        db.update(jid, status='STOPPED')


def stale_status_race(action):
    jid = create_job()
    assert db.claim('worker')['id'] == jid
    original = db.job_row

    def read_then_worker_finishes(job_id):
        previous = original(job_id)
        # Trigger once: action's final read must see the actual persisted result.
        db.job_row = original
        db.update(job_id, status='FINISHED', phase='max_rounds_reached')
        return previous

    db.job_row = read_then_worker_finishes
    try:
        action(jid, 'pause')
    finally:
        db.job_row = original
    row = original(jid)
    assert row['status'] == 'RUNNING' and row['pause_requested'] == 1
    emit('worker_finishes_between_control_select_and_update', status=row['status'],
         phase=row['phase'], pause_requested=row['pause_requested'])
    db.update(jid, status='STOPPED')


def journal_paging():
    jid = create_job()
    for index in range(1, 503):
        db.log(jid, 'audit-event-' + str(index))
    first = db.events_list(jid)
    refreshed = db.events_list(jid)
    tail = db.events_list(jid, first[-1]['id'])
    assert len(first) == 500 and first == refreshed
    assert any(event['text'] == 'audit-event-502' for event in tail)

    html = (REPO / 'app' / 'index.html').read_text(encoding='utf-8')
    # Check the actual client call, rather than assume that it omits pagination.
    call = re.search(r"api\('/api/jobs/'\+active\+'/events'\)", html)
    assert call, 'Dashboard request changed; independently re-check pagination.'
    assert "q('events').textContent=ev.map" in html
    emit('dashboard_repeats_first_event_page', repeated_page_size=len(refreshed),
         first_page_last_text=refreshed[-1]['text'],
         events_accessible_only_with_after=len(tail), latest_text=tail[-1]['text'],
         dashboard_request_source=call.group(0),
         method='real SQLite pagination plus dashboard source inspection')


def main():
    old_db = db.DB
    old_projects = os.environ.get('LAB_PROJECTS')
    try:
        with tempfile.TemporaryDirectory(prefix='lab-queue-audit-') as temp:
            root = Path(temp)
            db.DB = root / 'audit.sqlite3'
            os.environ['LAB_PROJECTS'] = str(root / 'projects')
            db.init()
            action = load_action()
            interrupted_control(action)
            stale_status_race(action)
            journal_paging()
    finally:
        db.DB = old_db
        if old_projects is None:
            os.environ.pop('LAB_PROJECTS', None)
        else:
            os.environ['LAB_PROJECTS'] = old_projects


if __name__ == '__main__':
    main()
