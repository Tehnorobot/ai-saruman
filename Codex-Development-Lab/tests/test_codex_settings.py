"""End-to-end settings propagation tests; mock Codex executable, never spend tokens."""
import json, os, sqlite3, stat, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import db
from common.codex_settings import codex_cli_overrides, validate_model, validate_reasoning
from agent import daemon as agent
from app import main as dashboard
from fastapi.testclient import TestClient


class CodexSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.projects = self.root / 'projects'; self.projects.mkdir()
        self.artifacts = self.root / 'artifacts'; self.artifacts.mkdir()
        db.DB = self.root / 'db' / 'database.sqlite'
        self.env = patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects),
            'LAB_ARTIFACTS': str(self.artifacts)}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)
        dashboard.PROJECTS = self.projects; dashboard.ARTIFACTS = self.artifacts
        db.init()
        self.client = TestClient(dashboard.app)

    def test_dashboard_persists_selected_model_effort(self):
        response = self.client.post('/api/projects',
            files={'spec': ('ТЗ.md', b'# Test\nMake a real tool described by spec.', 'text/markdown')},
            data={'model': 'gpt-6-sol', 'reasoning_effort': 'high', 'max_rounds': '2'})
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()['job_id']
        row = self.client.get('/api/jobs/' + job_id).json()
        self.assertEqual(row['model'], 'gpt-6-sol')
        self.assertEqual(row['reasoning_effort'], 'high')
        # A process restart does not reset a saved choice.
        db.init()
        self.assertEqual(db.job_row(job_id)['reasoning_effort'], 'high')
        self.assertEqual(db.job_row(job_id)['model'], 'gpt-6-sol')

    def test_default_is_backward_compatible(self):
        response = self.client.post('/api/projects',
            files={'spec': ('T.md', b'# Hello\nWrite a production-ready application.', 'text/markdown')})
        self.assertEqual(response.status_code, 200, response.text)
        row = db.job_row(response.json()['job_id'])
        self.assertEqual((row['model'], row['reasoning_effort']), ('', 'default'))
        self.assertEqual(codex_cli_overrides(row['model'], row['reasoning_effort']), [])

    def test_model_and_effort_reject_unsafe_or_unknown(self):
        with self.assertRaises(ValueError): validate_model('--model bad')
        with self.assertRaises(ValueError): validate_model('gpt-6;rm -rf')
        with self.assertRaises(ValueError): validate_reasoning('ultra-invalid')
        for model, effort in [('x;bad', 'medium'), ('gpt-6-sol', 'MY OWN')]:
            r = self.client.post('/api/projects',
                files={'spec': ('T.md', b'# Hello\nWrite a production-ready application.', 'text/markdown')},
                data={'model': model, 'reasoning_effort': effort})
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get('/api/jobs').json(), [])

    def test_max_and_ultra_are_forwarded_to_codex(self):
        for effort in ('max','ultra'):
            self.assertIn('model_reasoning_effort="'+effort+'"',codex_cli_overrides('gpt-6-sol',effort))

    def test_old_database_schema_is_migrated_without_data_loss(self):
        old = self.root / 'old.sqlite'
        with sqlite3.connect(old) as con:
            con.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, project_id TEXT, status TEXT, phase TEXT, round INTEGER, max_rounds INTEGER, created_at TEXT, updated_at TEXT, last_error TEXT, resume_at INTEGER, cancel_requested INTEGER, pause_requested INTEGER, worker TEXT, lease_until INTEGER)')
            con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        ('job-old', 'project-old', 'USER_PAUSED', 'test', 1, 5, 'today', 'today', None, 0, 0, 1, None, 0))
        db.DB = old; db.init()
        with db.connect() as con:
            row = con.execute('SELECT * FROM jobs WHERE id=?', ('job-old',)).fetchone()
            self.assertEqual(row['status'], 'USER_PAUSED')
            self.assertEqual(row['model'], '')
            self.assertEqual(row['reasoning_effort'], 'default')

    def test_selected_settings_reach_actual_codex_cli_arguments(self):
        pid, jid = db.create_project('Test', 'T.md', b'# Make an app\nCreate something useful.',
                                      model='gpt-6-sol', reasoning_effort='xhigh')
        mailbox = self.root / 'agent-mail'
        for part in ('requests', 'results', 'logs', 'cancel'):
            (mailbox / part).mkdir(parents=True)
        shell = self.root / 'fake_codex.py'
        # Record exact argv (one entry per argument), no network, no API usage.
        shell.write_text('import sys\nfrom pathlib import Path\n'
                         'Path("observed-argv.txt").write_text("\\n".join(sys.argv[1:]), encoding="utf-8")\n'
                         'print(\'{"type":"turn.completed"}\')\n', encoding='utf-8')
        taskid = jid + '-round01'
        task = {'id': taskid, 'job_id': jid, 'project_id': pid, 'prompt': 'Read spec.',
                'model': 'gpt-6-sol', 'reasoning_effort': 'xhigh'}
        req = mailbox / 'requests' / (taskid + '.json')
        req.write_text(json.dumps(task), encoding='utf-8')
        real_popen = subprocess.Popen
        def fake_popen(argv, **kwargs):
            return real_popen([sys.executable, str(shell), *argv[1:]], **kwargs)
        with patch.object(agent, 'ROOT', mailbox), patch.object(agent, 'PROJECTS', self.projects), \
             patch.object(agent.subprocess, 'Popen', side_effect=fake_popen):
            agent.run_task(req)
        args = (self.projects / pid / 'workspace' / 'observed-argv.txt').read_text().splitlines()
        self.assertEqual(args[args.index('--model') + 1], 'gpt-6-sol')
        overrides = [args[i+1] for i, arg in enumerate(args) if arg == '-c']
        self.assertIn('model_reasoning_effort="xhigh"', overrides)
        self.assertIn('approval_policy="never"', overrides)
        self.assertNotIn('--ask-for-approval', args)
        result = json.loads((mailbox / 'results' / (taskid + '.json')).read_text())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['reasoning_effort'], 'xhigh')

    def test_change_paused_or_queued_job_but_not_running_job(self):
        pid, jid = db.create_project('Test', 'T.md', b'# Test\nMake a real tool.',
                                      model='gpt-6-sol', reasoning_effort='medium')
        result = self.client.post('/api/jobs/' + jid + '/codex-settings',
                                   data={'model': 'gpt-6-luna', 'reasoning_effort': 'high'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['reasoning_effort'], 'high')
        self.assertEqual(result.json()['model'], 'gpt-6-luna')
        self.assertIsNotNone(db.claim('worker-1'))
        invalid = self.client.post('/api/jobs/' + jid + '/codex-settings',
                                   data={'model': 'gpt-6-sol', 'reasoning_effort': 'xhigh'})
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(db.job_row(jid)['reasoning_effort'], 'high')
        db.update(jid, status='USER_PAUSED', pause_requested=1)
        updated = self.client.post('/api/jobs/' + jid + '/codex-settings',
                                   data={'model': 'gpt-6-sol', 'reasoning_effort': 'xhigh'})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(db.job_row(jid)['reasoning_effort'], 'xhigh')
        self.assertEqual(db.job_row(jid)['model'], 'gpt-6-sol')

    def test_minimal_none_options_pass_through_without_claim_of_support(self):
        self.assertEqual(codex_cli_overrides('', 'none'), ['-c', 'model_reasoning_effort="none"'])
        self.assertEqual(codex_cli_overrides('', 'minimal'), ['-c', 'model_reasoning_effort="minimal"'])
        self.assertEqual(codex_cli_overrides('gpt-6-luna', 'medium'),
                         ['--model', 'gpt-6-luna', '-c', 'model_reasoning_effort="medium"'])

if __name__ == '__main__': unittest.main()
