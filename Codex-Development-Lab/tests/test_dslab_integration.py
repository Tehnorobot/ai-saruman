"""Independent integration checks: no paid calls, real persistence and protocol fixtures."""
import ast
import asyncio
import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from starlette.requests import Request
from agent import daemon, dslab
from app import main as dashboard
from common import db, provider_client
from common.history import _request
from worker import engine
from provider_gateway.main import Store, create_app


class SettingsAndPropagationTests(unittest.TestCase):
    def test_large_limits_create_and_update_without_old_ui_ceiling(self):
        jid=self.create(provider='minimax',model='MiniMax-M3.1-Flash',dslab_token_budget=3000000000,
                        dslab_max_output_tokens=131072,dslab_max_calls=50000)
        row=db.job_row(jid)
        self.assertEqual((row['dslab_token_budget'],row['dslab_max_output_tokens'],row['dslab_max_calls']),
                         (3000000000,131072,50000))
        db.update(jid,status='USER_PAUSED')
        response=self.client.post('/api/jobs/'+jid+'/codex-settings',data={
            'dslab_token_budget':4000000000,'dslab_max_output_tokens':262144,'dslab_max_calls':100000})
        self.assertEqual(response.status_code,200,response.text)
        row=db.job_row(jid)
        self.assertEqual((row['dslab_token_budget'],row['dslab_max_output_tokens'],row['dslab_max_calls']),
                         (4000000000,262144,100000))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = self.root / 'projects'
        self.mailbox = self.root / 'mailbox'
        self.artifacts = self.root / 'artifacts'
        for name in ('requests', 'running', 'results', 'logs', 'cancel'):
            (self.mailbox / name).mkdir(parents=True)
        contexts = [patch.object(db, 'DB', self.root / 'database.sqlite'),
                    patch.dict(os.environ, {'LAB_PROJECTS': str(self.projects), 'AGENT_TASKS': str(self.mailbox)}),
                    patch.object(dashboard, 'PROJECTS', self.projects),
                    patch.object(dashboard, 'ARTIFACTS', self.artifacts)]
        for context in contexts:
            context.start()
            self.addCleanup(context.stop)
        db.init()
        self.client = TestClient(dashboard.app)

    def create(self, **settings):
        response = self.client.post('/api/projects', files={'spec': ('T.md', b'# Mod\nImplement the complete mod specification.', 'text/markdown')}, data=settings)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['job_id']

    def test_dslab_settings_survive_restart_and_running_change_is_rejected(self):
        jid = self.create(provider='dslab', model='claude-opus-5-5', reasoning_effort='xhigh', dslab_token_budget=123456, dslab_max_calls=17)
        db.init()
        row = db.job_row(jid)
        self.assertEqual((row['provider'], row['model'], row['reasoning_effort']), ('dslab', 'claude-opus-5-5', 'xhigh'))
        self.assertEqual((row['dslab_token_budget'], row['dslab_max_calls']), (123456, 17))
        db.update(jid, status='RUNNING')
        response = self.client.post('/api/jobs/' + jid + '/codex-settings', data={'provider': 'codex', 'model': 'gpt-6-sol'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(db.job_row(jid)['provider'], 'dslab')

    def test_default_jobs_remain_codex_and_invalid_provider_never_queues(self):
        jid = self.create()
        self.assertEqual(db.job_row(jid)['provider'], 'codex')
        response = self.client.post('/api/projects', files={'spec': ('T.md', b'# Mod\nImplement the complete mod specification.')}, data={'provider': 'dslab', 'model': 'gpt-6-sol'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(db.jobs_list()), 1)

    def test_usage_route_reports_unavailability_without_fabricating_zero(self):
        jid = self.create(provider='dslab')
        with patch.object(dashboard, 'provider_control', side_effect=provider_client.GatewayError('unavailable')):
            report = self.client.get('/api/jobs/' + jid + '/usage').json()
        self.assertFalse(report['available'])
        self.assertNotIn('totals', report)
        self.assertTrue(report['cost_is_estimate'])

    def test_worker_registers_limits_and_archives_no_capability(self):
        jid = self.create(provider='dslab', model='claude-sonnet-5-5', reasoning_effort='high', dslab_token_budget=250000, dslab_max_calls=11)
        pid = db.job_row(jid)['project_id']
        logfile = self.artifacts / 'round01' / 'agent.jsonl'
        with patch.object(engine, 'provider_control', return_value={'capability': 'private-task-capability'}) as control, patch.object(engine, 'poll_job', return_value='USER_PAUSED'), patch.object(engine, 'archive_attempt', wraps=engine.archive_attempt) as archive:
            result = engine.request_agent(jid, pid, 1, 'Implement the specification', logfile)
        self.assertEqual(result['status'], 'USER_PAUSED')
        registration = control.call_args_list[0].args[2]
        self.assertEqual(registration['token_budget'], 250000)
        self.assertEqual(registration['effort'], 'high')
        self.assertEqual(registration['max_calls'], 11)
        live_request = archive.call_args_list[0].kwargs['request']
        self.assertEqual(live_request['dslab_capability'], 'private-task-capability')
        archived = list(self.artifacts.rglob('request.json'))
        self.assertEqual(len(archived), 1)
        self.assertNotIn('private-task-capability', archived[0].read_text())
        self.assertTrue(control.call_args_list[-1].args[1].endswith('/revoke'))

    def test_worker_claude_defaults_are_model_specific_without_budget_changes(self):
        for index,(model,expected) in enumerate((('claude-sonnet-5-5','high'),('claude-opus-5-5','medium'))):
            jid=self.create(provider='dslab',model=model,reasoning_effort='default',
                            dslab_token_budget=250000,dslab_max_calls=11)
            pid=db.job_row(jid)['project_id']
            logfile=self.artifacts/('defaults-'+str(index))/'codex.jsonl'
            with patch.object(engine,'provider_control',return_value={'capability':'private-capability'}) as control, \
                    patch.object(engine,'poll_job',return_value='USER_PAUSED'):
                result=engine.request_agent(jid,pid,1,'Implement',logfile)
            self.assertEqual(result['status'],'USER_PAUSED')
            registration=control.call_args_list[0].args[2]
            self.assertEqual(registration['effort'],expected)
            self.assertEqual(registration['token_budget'],250000)
            self.assertEqual(registration['max_calls'],11)

    def test_daemon_launches_executor_with_capability_in_stdin_only(self):
        jid = self.create(provider='dslab')
        pid = db.job_row(jid)['project_id']
        tid = jid + '-round01'
        task = {'id': tid, 'job_id': jid, 'project_id': pid, 'provider': 'dslab', 'model': 'claude-sonnet-5-5', 'prompt': 'Implement', 'dslab_capability': 'private-task-capability'}
        source = self.mailbox / 'requests' / (tid + '.json')
        source.write_text(json.dumps(task), encoding='utf-8')
        observed = {}
        def launch(argv, **kwargs):
            observed['argv'] = argv
            observed['task'] = json.load(kwargs['stdin'])
            return MagicMock(returncode=0, poll=lambda: 0)
        scope = MagicMock()
        scope.close.return_value = []
        with patch.object(daemon, 'ROOT', self.mailbox), patch.object(daemon, 'PROJECTS', self.projects), patch.object(daemon.subprocess, 'Popen', side_effect=launch), patch.object(daemon, 'TaskProcesses', return_value=scope):
            daemon._run_task(source)
        self.assertEqual(observed['argv'][1], '-I')
        self.assertEqual(Path(observed['argv'][2]).resolve(), Path(daemon.__file__).resolve().with_name('dslab.py'))
        self.assertTrue(Path(observed['argv'][2]).is_absolute())
        self.assertNotIn('private-task-capability', repr(observed['argv']))
        self.assertEqual(observed['task']['dslab_capability'], 'private-task-capability')
        self.assertEqual(json.loads((self.mailbox / 'results' / (tid + '.json')).read_text())['provider'], 'dslab')

    def test_gateway_registration_failure_never_publishes_a_paid_task(self):
        jid = self.create(provider='dslab')
        pid = db.job_row(jid)['project_id']
        with patch.object(engine, 'provider_control', side_effect=provider_client.GatewayError('gateway unavailable')) as control:
            result = engine.request_agent(jid, pid, 1, 'Implement', self.artifacts / 'round01' / 'agent.jsonl')
        self.assertEqual(result['status'], 'ERROR')
        self.assertEqual(result['label'], 'dslab')
        self.assertFalse(list((self.mailbox / 'requests').glob('*.json')))
        self.assertEqual(control.call_count, 1)

    def test_malformed_legacy_mailbox_item_cannot_crash_provider_routing(self):
        source = self.mailbox / 'requests' / 'malformed.json'
        for content in ('{broken', '[]', 'null', '{"provider":"unknown"}'):
            source.write_text(content, encoding='utf-8')
            self.assertEqual(daemon.task_provider(source), 'codex')
        source.write_text('{"provider":"dslab"}', encoding='utf-8')
        self.assertEqual(daemon.task_provider(source), 'dslab')

    def test_deployment_pause_transaction_rejects_racing_active_job(self):
        source = Path(__file__).resolve().parents[1] / 'audit' / 'deploy_dslab.py'
        if not source.is_file():
            self.skipTest('Host-only deployment helper is intentionally excluded from container images')
        jid = self.create()
        tree = ast.parse(source.read_text(encoding='utf-8'))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'pause_script' for target in node.targets))
        script = eval(compile(ast.Expression(assignment.value), str(source), 'eval'), {'quota_jobs': [jid], 'repr': repr})
        db.update(jid, status='RUNNING', round=17, pause_requested=0)
        with self.assertRaisesRegex(AssertionError, 'Active job'):
            exec(script, {})
        self.assertEqual(db.job_row(jid)['pause_requested'], 0)
        self.assertEqual(db.job_row(jid)['status'], 'RUNNING')
        db.update(jid, status='QUOTA_PAUSED')
        exec(script, {})
        self.assertEqual(db.job_row(jid)['status'], 'USER_PAUSED')
        self.assertEqual(db.job_row(jid)['round'], 17)


class ExecutorProtocolTests(unittest.TestCase):
    def test_planner_can_finish_after_twelve_calls_but_still_has_a_hard_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            task={'model':'example','dslab_capability':'synthetic','dslab_max_calls':20}
            calls=[]
            def send(*args,**kwargs):
                calls.append(1)
                if len(calls)==13:return [{'type':'text','text':'final plan'}],'end_turn'
                return [{'type':'tool_use','id':'call-'+str(len(calls)),'name':'list_files','input':{}}],'tool_use'
            with patch.object(dslab,'item'),patch.object(dslab,'event'):
                executor=dslab.Executor(task,Path(temporary),send=send)
                self.assertEqual(executor.run('Plan',agent='planner',readonly=True,turns=20),'final plan')
                self.assertEqual(len(calls),13)
                calls.clear()
                executor=dslab.Executor(task,Path(temporary),send=send)
                with self.assertRaisesRegex(RuntimeError,'step limit reached'):
                    executor.run('Plan',agent='planner',readonly=True,turns=12)
                self.assertEqual(len(calls),12)
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = {'model': 'claude-sonnet-5-5', 'dslab_capability': 'scoped-capability', 'reasoning_effort': 'high', 'dslab_max_calls': 10}

    def test_two_turn_tool_loop_preserves_tool_result_and_real_file_write(self):
        requests = []
        def send(payload, capability, **kwargs):
            requests.append(copy.deepcopy(payload))
            self.assertEqual(capability, 'scoped-capability')
            if len(requests) == 1:
                return [{'type': 'tool_use', 'id': 'tool-1', 'name': 'write_file', 'input': {'path': 'example.py', 'content': 'answer = 42\n'}}], 'tool_use'
            return [{'type': 'text', 'text': 'Created source.'}], 'end_turn'
        with redirect_stdout(io.StringIO()):
            result = dslab.Executor(self.task, self.root, send=send).run('Implement')
        self.assertEqual(result, 'Created source.')
        self.assertEqual((self.root / 'example.py').read_text(), 'answer = 42\n')
        self.assertEqual(requests[1]['messages'][-1]['content'][0]['tool_use_id'], 'tool-1')
        self.assertEqual(requests[0]['output_config']['effort'], 'high')

    def test_executor_claude_defaults_and_explicit_effort(self):
        for model,effort,expected in (('claude-sonnet-5-5','default','high'),
                ('claude-opus-5-5','default','medium'),('claude-sonnet-5-5','low','low'),
                ('claude-opus-5-5','high','high')):
            requests=[]
            def send(payload,*args,**kwargs):
                requests.append(payload)
                return [{'type':'text','text':'done'}],'end_turn'
            task={**self.task,'model':model,'reasoning_effort':effort}
            with redirect_stdout(io.StringIO()):dslab.Executor(task,self.root,send=send).run('Implement')
            self.assertEqual(requests[0]['output_config']['effort'],expected)
            self.assertEqual(requests[0]['max_tokens'],8192)

    def test_executor_litellm_legacy_effort_never_forces_thinking(self):
        for effort in ('default','high','max','none'):
            requests=[]
            def send(payload,*args,**kwargs):
                requests.append(payload)
                return [{'type':'text','text':'done'}],'end_turn'
            task={**self.task,'provider':'litellm','model':'deepseek-ai/DeepSeek-V4-Flash-0731',
                  'reasoning_effort':effort}
            with redirect_stdout(io.StringIO()):dslab.Executor(task,self.root,send=send).run('Implement')
            for field in ('output_config','thinking','reasoning_effort'):
                self.assertNotIn(field,requests[0])
            self.assertEqual(requests[0]['max_tokens'],8192)
            self.assertEqual(requests[0]['messages'][0]['content'],'Implement')

    def test_delegated_specialist_is_read_only_in_schema_and_runtime(self):
        requests = []
        def send(payload, capability, **kwargs):
            requests.append(payload)
            return [{'type': 'text', 'text': 'Independent review: NOT_TESTED gameplay.'}], 'end_turn'
        executor = dslab.Executor(self.task, self.root, send=send)
        with redirect_stdout(io.StringIO()):
            result = executor.tool('delegate', {'role': 'reviewer', 'task': 'Review source'})
        self.assertIn('NOT_TESTED', result)
        self.assertEqual({tool['name'] for tool in requests[0]['tools']},
                         {'read_file', 'list_files', 'read_lines', 'search_text', 'read_requirements','inspect_artifact','source_revision',
                          'query_memory','read_artifact','read_diff','record_note','read_execution'})
        with self.assertRaises(ValueError):
            executor.tool('write_file', {'path': 'unsafe.py', 'content': 'x'}, readonly=True)
        self.assertFalse((self.root / 'unsafe.py').exists())

    def test_incomplete_turn_never_claims_completion(self):
        executor = dslab.Executor(self.task, self.root, send=lambda *a, **k: ([{'type': 'text', 'text': 'partial'}], 'max_tokens'))
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            executor.run('Implement')

    def test_path_traversal_and_environment_modification_are_rejected(self):
        executor = dslab.Executor(self.task, self.root)
        with self.assertRaises(ValueError):
            executor.tool('write_file', {'path': '../outside.py', 'content': 'x'})
        with self.assertRaises(ValueError):
            executor.tool('write_file', {'path': '.env', 'content': 'SECRET=x'})

    def test_credential_file_read_is_rejected_before_content_is_returned(self):
        executor = dslab.Executor(self.task, self.root)
        for name in ('.env', '.env.production', 'auth.json', 'credentials'):
            (self.root / name).write_text('SYNTHETIC_SECRET=fixture', encoding='utf-8')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Credential'):
                executor.tool('read_file', {'path': name})

    def test_scoped_capability_is_redacted_from_nested_logged_fields(self):
        output = io.StringIO()
        with patch.object(dslab, '_CAPABILITY', 'private-capability-fixture'), redirect_stdout(output):
            dslab.event('item.completed', item={'text': 'private-capability-fixture', 'command': ['echo', 'private-capability-fixture']})
        self.assertNotIn('private-capability-fixture', output.getvalue())
        self.assertIn('REDACTED_TASK_CAPABILITY', output.getvalue())

    def test_isolated_startup_ignores_workspace_and_pythonpath_shadow_packages(self):
        marker = self.root / 'UNTRUSTED_IMPORT_EXECUTED'
        payload = 'from pathlib import Path\nPath(%r).write_text("unsafe import")\n' % str(marker)
        for package in ('agent', 'common'):
            (self.root / package).mkdir()
            (self.root / package / '__init__.py').write_text(payload, encoding='utf-8')
        (self.root / 'agent' / 'dslab.py').write_text(payload, encoding='utf-8')
        (self.root / 'common' / 'provider_client.py').write_text(payload, encoding='utf-8')
        (self.root / 'json.py').write_text(payload, encoding='utf-8')
        (self.root / 'sitecustomize.py').write_text(payload, encoding='utf-8')
        trusted = Path(daemon.__file__).resolve().with_name('dslab.py')
        environment = dict(os.environ, PYTHONPATH=str(self.root))
        result = subprocess.run([sys.executable, '-I', str(trusted)], cwd=self.root,
                                input='{}', env=environment, capture_output=True, text=True, timeout=15)
        # Missing model fails before any gateway request; even startup imports must
        # come exclusively from the installed executor/stdlib, never project code.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('KeyError', result.stderr)
        self.assertFalse(marker.exists(), result.stdout + result.stderr)

    def test_stream_codec_joins_tool_json_and_keeps_private_thinking_out_of_logs(self):
        events = [
            {'type': 'message_start', 'message': {'usage': {'input_tokens': 10}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'PRIVATE_THINKING'}},
            {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'tool_use', 'id': 'tool1', 'name': 'read_file', 'input': {}}},
            {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '{"path":'}},
            {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '"README.md"}'}},
            {'type': 'content_block_stop', 'index': 1},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 20}},
            {'type': 'message_stop'}]
        response = io.BytesIO(b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in events))
        response.status = 200
        connection = MagicMock()
        connection.getresponse.return_value = response
        output = io.StringIO()
        with redirect_stdout(output):
            content, reason = dslab.streamed_message({'model': 'claude-sonnet-5-5'}, 'scoped-capability', connection_factory=lambda **kwargs: connection)
        self.assertEqual(reason, 'tool_use')
        self.assertEqual(content[1]['input'], {'path': 'README.md'})
        self.assertNotIn('PRIVATE_THINKING', output.getvalue())
        self.assertNotIn('scoped-capability', output.getvalue())
        self.assertIn('usage.updated', output.getvalue())
        connection.close.assert_called_once()

    def test_stream_eof_without_message_stop_is_failure(self):
        response = io.BytesIO(b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n')
        response.status = 200
        connection = MagicMock()
        connection.getresponse.return_value = response
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'without completion'):
            dslab.streamed_message({}, 'scoped-capability', connection_factory=lambda **kwargs: connection)


class RealtimeGatewayTests(unittest.TestCase):
    def test_report_updates_while_upstream_stream_is_still_running(self):
        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                for event in [
                    {'type': 'message_start', 'message': {'usage': {'input_tokens': 123, 'output_tokens': 0}}},
                    {'type': 'message_delta', 'usage': {'output_tokens': 45}},
                    {'type': 'message_stop'}]:
                    yield ('data: ' + json.dumps(event) + '\n\n').encode()

        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'DSLAB_API_KEY': 'fake-provider-key', 'DSLAB_CONTROL_TOKEN': 'fake-control-token-for-independent-fixture', 'DSLAB_BASE_URL': 'https://api.dslab.tech'}):
            store = Store(Path(temporary) / 'usage.sqlite3')
            app = create_app(store=store, transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks())))
            client = TestClient(app)
            registered = client.post('/internal/tasks', headers={'authorization': 'Bearer ' + os.environ['DSLAB_CONTROL_TOKEN']}, json={'task_id': 'fixture-realtime', 'job_id': '89cd3a16ad2c4e', 'project_id': '94a2c07a7a5b44', 'model': 'claude-sonnet-5-5'}).json()
            raw = json.dumps({'model': 'claude-sonnet-5-5', 'stream': True, 'max_tokens': 1000, 'messages': [{'role': 'user', 'content': 'hello'}]}).encode()
            async def receive():
                return {'type': 'http.request', 'body': raw, 'more_body': False}
            request = Request({'type': 'http', 'method': 'POST', 'path': '/v1/messages', 'headers': [(b'authorization', ('Bearer ' + registered['capability']).encode())]}, receive=receive)
            handler = next(route.endpoint for route in app.routes if route.path == '/v1/messages')
            async def exercise():
                response = await handler(request)
                iterator = response.body_iterator
                await iterator.__anext__()
                midway = store.job_usage('89cd3a16ad2c4e')
                self.assertEqual(midway['totals']['input_tokens'], 123)
                self.assertEqual(midway['totals']['output_tokens'], 0)
                self.assertEqual(midway['requests'][0]['status'], 'streaming')
                self.assertEqual(midway['totals']['billing_unknown_requests'], 1)
                await iterator.__anext__()
                self.assertEqual(store.job_usage('89cd3a16ad2c4e')['totals']['output_tokens'], 45)
                async for _ in iterator:
                    pass
                final = store.job_usage('89cd3a16ad2c4e')
                self.assertEqual(final['requests'][0]['status'], 'completed')
                self.assertEqual(final['totals']['billing_unknown_requests'], 0)
            asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
