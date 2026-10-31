"""Actual Linux detached-process cleanup through the daemon, without a model."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

from agent import daemon


FAKE_CLI = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
mode, evidence = sys.argv[1], Path(sys.argv[2])
if mode == '--detached':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (evidence / 'detached.json').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'sid': os.getsid(0)}))
    while True: time.sleep(1)
elif mode == '--intermediate':
    (evidence / 'intermediate.pid').write_text(str(os.getpid()))
    subprocess.Popen([sys.executable, __file__, '--detached', str(evidence)], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 5
    while not (evidence / 'detached.json').exists():
        if time.monotonic() > deadline: raise RuntimeError('Detached child did not start')
        time.sleep(.01)
else:
    sys.stdin.read()
    (evidence / 'cli.json').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
    subprocess.run([sys.executable, __file__, '--intermediate', str(evidence)], check=True)
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'fake CLI with detached grandchild'}}), flush=True)
    if mode == 'PASS': sys.exit(0)
    if mode == 'SOFT_PASS':
        time.sleep(1.4)
        sys.exit(0)
    while True: time.sleep(1)
'''


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'), 'Linux pidfd/subreaper integration required')
class AgentProcessCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mailbox, self.projects = self.root / 'mailbox', self.root / 'projects'
        self.pid, self.jid = 'a' * 14, 'b' * 14
        self.tid = self.jid + '-round01-' + 'c' * 32
        (self.projects / self.pid / 'workspace').mkdir(parents=True)
        for directory in ('running', 'results', 'logs', 'cancel'):
            (self.mailbox / directory).mkdir(parents=True)
        self.request = self.mailbox / 'running' / (self.tid + '.json')
        self.request.write_text(json.dumps({'id': self.tid, 'project_id': self.pid,
                                          'job_id': self.jid, 'prompt': 'Synthetic task; no network'}))
        self.script = self.root / 'fake_cli.py'
        self.script.write_text(FAKE_CLI)
        self.real_popen = subprocess.Popen
        self.baseline = self.real_popen([sys.executable, '-c', 'import time;time.sleep(40)'])
        self.addCleanup(self.stop_baseline)
        self.addCleanup(self.stop_stray_child)

    def stop_baseline(self):
        if self.baseline.poll() is None:
            self.baseline.kill()
        self.baseline.wait(timeout=5)

    def stop_stray_child(self):
        # Clean up even if assertions expose a regression; do not leave a test
        # process alive in an interactive developer environment.
        marker = self.root / 'detached.json'
        if marker.exists():
            pid = json.loads(marker.read_text())['pid']
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

    def run_scenario(self, mode):
        calls = []
        def fake_codex(command, **options):
            self.assertEqual(command[0], 'codex')
            self.assertIn('--json', command)
            calls.append(command)
            return self.real_popen([sys.executable, str(self.script), mode, str(self.root)], **options)

        cancelled = threading.Event()
        def request_cancel():
            deadline = time.monotonic() + 8
            while not (self.root / 'detached.json').exists():
                if time.monotonic() > deadline:
                    return
                time.sleep(.01)
            (self.mailbox / 'cancel' / self.tid).touch()
            cancelled.set()

        cancel_thread = threading.Thread(target=request_cancel) if mode == 'CANCELLED' else None
        if cancel_thread:
            cancel_thread.start()
            self.addCleanup(cancel_thread.join, 10)
        with patch.object(daemon, 'ROOT', self.mailbox), \
             patch.object(daemon, 'PROJECTS', self.projects), \
             patch.object(daemon, 'TIMEOUT', .2 if mode == 'SOFT_PASS' else 10), \
             patch.object(daemon.subprocess, 'Popen', side_effect=fake_codex):
            daemon.run_task(self.request)
        self.assertEqual(len(calls), 1)
        result = json.loads((self.mailbox / 'results' / (self.tid + '.json')).read_text())
        self.assertEqual(result['status'], 'PASS' if mode=='SOFT_PASS' else mode, result)
        if mode == 'SOFT_PASS':
            self.assertGreater(result['duration_seconds'],.2)
            self.assertIsNone(result['error'])
        if mode == 'CANCELLED':
            self.assertTrue(cancelled.is_set())
        cli = json.loads((self.root / 'cli.json').read_text())
        detached = json.loads((self.root / 'detached.json').read_text())
        self.assertNotEqual(detached['pgid'], cli['pgid'], 'Test did not create a detached process group')
        self.assertEqual(detached['sid'], detached['pid'], 'Grandchild did not create its own session')
        for pid in (cli['pid'], detached['pid'], int((self.root / 'intermediate.pid').read_text())):
            with self.subTest(pid=pid), self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertIsNone(self.baseline.poll(), 'Cleanup killed an unrelated pre-existing child')
        self.assertFalse(self.request.exists())
        log = (self.mailbox / 'logs' / (self.tid + '.jsonl')).read_text()
        self.assertIn('fake CLI with detached grandchild', log)

    def test_success_reaps_detached_sigterm_ignoring_grandchild(self):
        self.run_scenario('PASS')

    def test_soft_deadline_allows_completion_and_then_reaps_detached_grandchild(self):
        self.run_scenario('SOFT_PASS')

    def test_cancel_reaps_detached_sigterm_ignoring_grandchild(self):
        self.run_scenario('CANCELLED')

    def test_unconfirmed_cleanup_has_no_terminal_ack_until_new_pid_namespace(self):
        def fake_short_cli(_command, **options):
            return self.real_popen([sys.executable, '-c', 'pass'], **options)
        with patch.object(daemon, 'ROOT', self.mailbox), \
             patch.object(daemon, 'PROJECTS', self.projects), \
             patch.object(daemon, 'pid_namespace', return_value=111), \
             patch.object(daemon.subprocess, 'Popen', side_effect=fake_short_cli), \
             patch.object(daemon, 'TaskProcesses') as scope:
            scope.return_value.close.side_effect = OSError('Synthetic unconfirmed cleanup')
            with self.assertRaises(daemon.TaskCleanupFailed):
                daemon.run_task(self.request)
            self.assertTrue(self.request.exists())
            result = self.mailbox / 'results' / (self.tid + '.json')
            marker = self.mailbox / 'cleanup-failed' / (self.tid + '.json')
            self.assertFalse(result.exists())
            self.assertTrue(marker.exists())
            with self.assertRaises(daemon.TaskCleanupFailed):
                daemon.acknowledge_restarted_cleanup()
            self.assertFalse(result.exists())
            self.assertTrue(self.request.exists())
        with patch.object(daemon, 'ROOT', self.mailbox), \
             patch.object(daemon, 'pid_namespace', return_value=222):
            daemon.acknowledge_restarted_cleanup()
        self.assertFalse(self.request.exists())
        self.assertFalse(marker.exists())
        payload = json.loads(result.read_text())
        self.assertEqual(payload['status'], 'ERROR')
        self.assertIn('cleanup', payload['error'].lower())

    def test_reported_remaining_processes_do_not_publish_cleanup_success(self):
        def fake_short_cli(_command, **options):
            return self.real_popen([sys.executable, '-c', 'pass'], **options)
        with patch.object(daemon, 'ROOT', self.mailbox), \
             patch.object(daemon, 'PROJECTS', self.projects), \
             patch.object(daemon.subprocess, 'Popen', side_effect=fake_short_cli), \
             patch.object(daemon, 'TaskProcesses') as scope:
            scope.return_value.close.return_value = [12345]
            with self.assertRaises(daemon.TaskCleanupFailed):
                daemon.run_task(self.request)
        self.assertTrue(self.request.exists())
        self.assertFalse((self.mailbox / 'results' / (self.tid + '.json')).exists())
        self.assertTrue((self.mailbox / 'cleanup-failed' / (self.tid + '.json')).exists())

    def test_unconfirmed_cleanup_has_no_terminal_ack_before_new_pid_namespace(self):
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 0
        scope = Mock()
        scope.close.return_value = [424242]
        with patch.object(daemon, 'ROOT', self.mailbox), \
             patch.object(daemon, 'PROJECTS', self.projects), \
             patch.object(daemon, 'TaskProcesses', return_value=scope), \
             patch.object(daemon, 'pid_namespace', return_value=42), \
             patch.object(daemon.subprocess, 'Popen', return_value=process):
            with self.assertRaises(daemon.TaskCleanupFailed):
                daemon.run_task(self.request)
            self.assertFalse((self.mailbox/'results'/(self.tid+'.json')).exists())
            self.assertTrue(self.request.exists())
            with self.assertRaises(daemon.TaskCleanupFailed):
                daemon.acknowledge_restarted_cleanup()
        with patch.object(daemon, 'ROOT', self.mailbox), \
             patch.object(daemon, 'pid_namespace', return_value=43):
            daemon.acknowledge_restarted_cleanup()
        result = json.loads((self.mailbox/'results'/(self.tid+'.json')).read_text())
        self.assertEqual(result['status'], 'ERROR')
        self.assertFalse(self.request.exists())
        self.assertFalse((self.mailbox/'cleanup-failed'/(self.tid+'.json')).exists())


if __name__ == '__main__':
    unittest.main()
