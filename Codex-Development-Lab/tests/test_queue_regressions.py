"""Queue recovery/races and the actual dashboard event pagination code."""
import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from common import db


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


def api_action(jid, action):
    # Exercise the actual route without requiring an HTTP server or FastAPI.
    source = REPO / 'app' / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'action')
    node.decorator_list = []
    scope = {'db': db, 'HTTPException': HTTPException}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
    return scope['action'](jid, action)


class QueueRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.patch_db = patch.object(db, 'DB', root / 'queue.sqlite3')
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.patch_env = patch.dict(os.environ, {'LAB_PROJECTS': str(root / 'projects')})
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        db.init()

    def create_job(self):
        return db.create_project('Queue regression', 'spec.md', b'# Isolated development specification')[1]

    def test_crashed_worker_pause_is_reclaimed_then_acknowledged_before_resume(self):
        jid = self.create_job()
        db.claim('crashed-worker')
        self.assertEqual(api_action(jid, 'pause')['status'], 'RUNNING')
        db.update(jid, lease_until=0)
        self.assertEqual(db.claim('replacement')['id'], jid)
        row = db.job_row(jid)
        self.assertEqual(row['status'], 'RUNNING')
        self.assertEqual(row['worker'], 'replacement')
        self.assertEqual(row['pause_requested'], 1)
        with self.assertRaises(HTTPException) as raised:
            api_action(jid, 'resume')
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(db.set_job_codex_settings(jid, '', 'default'))
        # Only the replacement worker can acknowledge actual writer exit.
        self.assertFalse(db.update_owned(jid, 'crashed-worker', status='USER_PAUSED'))
        self.assertTrue(db.update_owned(jid, 'replacement', status='USER_PAUSED', worker=None, lease_until=0))
        row = db.job_row(jid)
        self.assertEqual(row['status'], 'USER_PAUSED')
        self.assertIsNone(row['worker'])
        self.assertEqual(api_action(jid, 'resume')['status'], 'QUEUED')
        self.assertEqual(db.claim('replacement')['id'], jid)

    def test_expired_lease_alone_does_not_acknowledge_pause(self):
        jid = self.create_job()
        db.claim('crashed-worker')
        api_action(jid, 'pause')
        db.update(jid, lease_until=0)
        with self.assertRaises(HTTPException) as raised:
            api_action(jid, 'resume')
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(db.job_row(jid)['status'], 'RUNNING')
        self.assertFalse(db.set_job_codex_settings(jid, '', 'default'))

    def test_crashed_worker_stop_stays_terminal(self):
        jid = self.create_job()
        db.claim('crashed-worker')
        api_action(jid, 'stop')
        db.update(jid, lease_until=0)
        self.assertEqual(db.claim('replacement')['id'], jid)
        self.assertEqual(db.job_row(jid)['status'], 'RUNNING')
        self.assertEqual(db.job_row(jid)['cancel_requested'], 1)
        self.assertFalse(db.heartbeat(jid, 'crashed-worker'))
        self.assertFalse(db.update_owned(jid, 'crashed-worker', status='FINISHED'))
        with self.assertRaises(HTTPException):
            api_action(jid, 'resume')
        self.assertTrue(db.update_owned(jid, 'replacement', status='STOPPED', worker=None, lease_until=0))
        self.assertEqual(db.job_row(jid)['status'], 'STOPPED')
        self.assertIsNone(db.claim('another-worker'))
        with self.assertRaises(HTTPException) as raised:
            api_action(jid, 'resume')
        self.assertEqual(raised.exception.status_code, 409)

    def test_new_owner_rejects_expired_worker_writes(self):
        jid = self.create_job()
        db.claim('old-owner')
        self.assertTrue(db.heartbeat(jid, 'old-owner'))
        db.update(jid, lease_until=0)
        self.assertFalse(db.heartbeat(jid, 'old-owner'))
        self.assertFalse(db.update_owned(jid, 'old-owner', status='FINISHED'))
        db.claim('new-owner')
        self.assertFalse(db.update_owned(jid, 'old-owner', status='FAILED'))
        self.assertTrue(db.update_owned(jid, 'new-owner', phase='verifying'))
        self.assertEqual(db.job_row(jid)['status'], 'RUNNING')

    def control_while_locked(self, jid, mutate):
        """Commit a worker change while a real concurrent control request waits."""
        original_connect = db.connect
        attempted = threading.Event()
        outcome = []

        @contextmanager
        def observed_connect():
            with original_connect() as con:
                if threading.current_thread().name == 'control-request':
                    attempted.set()
                yield con

        def control():
            try:
                outcome.append(api_action(jid, 'pause'))
            except Exception as exc:
                outcome.append(exc)

        with original_connect() as con:
            con.execute('BEGIN IMMEDIATE')
            mutate(con)
            with patch.object(db, 'connect', observed_connect):
                thread = threading.Thread(target=control, name='control-request', daemon=True)
                thread.start()
                self.assertTrue(attempted.wait(10), 'Concurrent control request did not reach the database')
                con.commit()
                thread.join(10)
                self.assertFalse(thread.is_alive(), 'Concurrent control request deadlocked')
        self.assertEqual(len(outcome), 1)
        return outcome[0]

    def test_completion_wins_racing_pause(self):
        jid = self.create_job()
        db.claim('worker')
        result = self.control_while_locked(jid, lambda con: con.execute(
            "UPDATE jobs SET status='FINISHED',phase='max_rounds_reached' WHERE id=?", (jid,)))
        self.assertIsInstance(result, HTTPException)
        self.assertEqual(result.status_code, 409)
        row = db.job_row(jid)
        self.assertEqual(row['status'], 'FINISHED')
        self.assertEqual(row['pause_requested'], 0)

    def test_claim_wins_racing_pause_without_premature_acknowledgement(self):
        jid = self.create_job()
        result = self.control_while_locked(jid, lambda con: con.execute(
            "UPDATE jobs SET status='RUNNING',worker='worker',lease_until=? WHERE id=?",
            (int(db.time.time()) + 90, jid)))
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'RUNNING')
        self.assertEqual(result['pause_requested'], 1)
        with self.assertRaises(HTTPException):
            api_action(jid, 'resume')
        self.assertFalse(db.set_job_codex_settings(jid, '', 'default'))

    def test_event_cursor_reaches_new_events_past_500(self):
        jid = self.create_job()
        with db.connect() as con:
            for index in range(502):
                db.event(con, jid, f'event-{index + 1}')
        first = db.events_list(jid)
        tail = db.events_list(jid, first[-1]['id'])
        self.assertEqual(len(first), 500)
        self.assertEqual(tail[-1]['text'], 'event-502')
        self.assertFalse({x['id'] for x in first} & {x['id'] for x in tail})


class DashboardPaginationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required to execute dashboard JavaScript')
    def test_actual_dashboard_drains_pages_then_requests_after_cursor(self):
        html = (REPO / 'app' / 'index.html').read_text(encoding='utf-8')
        script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
        script = script[:script.rfind('setInterval(')]
        harness = r'''
const vm=require('vm'), assert=require('assert');
const nodes=new Map(), calls=[];
let all=Array.from({length:502},(_,i)=>({id:i+1,created_at:'now',level:'info',text:'event '+(i+1)}));
const context=vm.createContext({console,document:{getElementById(id){if(!nodes.has(id))nodes.set(id,{textContent:''});return nodes.get(id)}},
fetch:async path=>{calls.push(path);const after=Number(new URL(path,'http://lab').searchParams.get('after'));
return {ok:true,text:async()=>JSON.stringify(all.filter(x=>x.id>after).slice(0,500))}}});
vm.runInContext(SOURCE,context);
(async()=>{
  await vm.runInContext("active='job-a';refreshEvents(active)",context);
  assert.deepEqual(calls,['/api/jobs/job-a/events?after=0','/api/jobs/job-a/events?after=500']);
  assert(nodes.get('events').textContent.endsWith('event 502'));
  all.push({id:503,created_at:'now',level:'error',text:'finished after page limit'});
  await vm.runInContext('refreshEvents(active)',context);
  assert.equal(calls.at(-1),'/api/jobs/job-a/events?after=502');
  assert(nodes.get('events').textContent.endsWith('finished after page limit'));
  assert.equal(nodes.get('events').textContent.split('\n').length,503);
  await vm.runInContext("active='job-b';refreshEvents(active)",context);
  assert(calls.includes('/api/jobs/job-b/events?after=0'));
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('SOURCE', json.dumps(script))
        completed = subprocess.run([shutil.which('node'), '-e', harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == '__main__':
    unittest.main()
