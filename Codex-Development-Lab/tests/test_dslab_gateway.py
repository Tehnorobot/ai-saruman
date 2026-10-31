"""Gateway contract checks with synthetic SSE only; never call a paid provider."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from provider_gateway.main import SSEUsage, Store, create_app, LITELLM_MODELS, OpenAIStream, openai_request


def sse(event):
    return ("data: " + json.dumps(event) + "\n\n").encode()


class GatewayTests(unittest.TestCase):
    def test_large_user_limits_survive_registration_and_output_is_not_clamped(self):
        self.registration.update(token_budget=3000000000,max_output_tokens=131072,max_calls=50000)
        cap=self.register()
        self.assertEqual(self.send(cap,max_tokens=131072).status_code,200)
        self.assertEqual(json.loads(self.calls[-1].content)['max_tokens'],131072)
        self.assertEqual(self.send(cap,max_tokens=131073).status_code,400)
        stored=self.store.task(cap)
        self.assertEqual((stored['token_budget'],stored['max_output_tokens'],stored['max_calls']),
                         (3000000000,131072,50000))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / "usage.sqlite3")
        self.config = patch.dict(os.environ, {"DSLAB_API_KEY": "private-provider-key",
                                "DSLAB_CONTROL_TOKEN": "trusted-control-credential-1234567890",
                                "DSLAB_BASE_URL": "https://api.dslab.tech"})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 100, "output_tokens": 0, "cache_read_input_tokens": 20}}},
            {"type": "message_delta", "usage": {"output_tokens": 40}},
            {"type": "message_delta", "usage": {"output_tokens": 40}},
            {"type": "message_stop"}]
        self.calls = []
        self.provider_status = 200
        self.client = TestClient(create_app(store=self.store, transport=httpx.MockTransport(self.upstream)))
        self.control = {"authorization": "Bearer " + os.environ["DSLAB_CONTROL_TOKEN"]}
        self.registration = {"task_id": "89cd3a16ad2c4e-round20-fixture", "job_id": "89cd3a16ad2c4e", "project_id": "94a2c07a7a5b44", "model": "claude-sonnet-5-5", "token_budget": 1000000, "max_output_tokens": 8192}

    def upstream(self, request):
        self.calls.append(request)
        self.assertEqual(request.headers["x-api-key"], "private-provider-key")
        self.assertNotIn("trusted-control", str(request.headers))
        self.assertEqual(str(request.url), "https://api.dslab.tech/v1/messages")
        return httpx.Response(self.provider_status, headers={"content-type": "text/event-stream"}, content=b"".join(map(sse, self.events)))

    def register(self):
        response = self.client.post("/internal/tasks", headers=self.control, json=self.registration)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["capability"]

    def send(self, cap, **overrides):
        payload = {"model": "claude-sonnet-5-5", "stream": True, "max_tokens": 8192,
                   "messages": [{"role": "user", "content": "hello"}]}
        payload.update(overrides)
        return self.client.post("/v1/messages", headers={"authorization": "Bearer " + cap}, json=payload)

    def usage(self):
        response = self.client.get("/internal/jobs/89cd3a16ad2c4e/usage", headers=self.control)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_stream_reports_cumulative_usage_without_duplicate_counting(self):
        response = self.send(self.register())
        self.assertEqual(response.status_code, 200, response.text)
        totals = self.usage()["totals"]
        self.assertEqual(totals["input_tokens"], 100)
        self.assertEqual(totals["output_tokens"], 40)
        self.assertEqual(totals["cache_read_input_tokens"], 20)
        self.assertAlmostEqual(totals["estimated_rub"], .0604)
        self.assertEqual(totals["billing_unknown_requests"], 0)
        self.assertNotIn("private-provider", json.dumps(self.usage()))

    def test_registration_recovery_preserves_capability_and_budget(self):
        cap = self.register()
        self.send(cap)
        self.assertEqual(self.register(), cap)
        self.assertEqual(self.usage()["totals"]["requests"], 1)
        self.registration["token_budget"] = 2000000
        self.assertEqual(self.client.post("/internal/tasks", headers=self.control, json=self.registration).status_code, 409)

    def test_roles_separate_usage_and_cannot_change_on_recovery(self):
        self.registration['role']='planner'
        cap=self.register();self.send(cap)
        self.registration['role']='coder'
        self.assertEqual(self.client.post('/internal/tasks',headers=self.control,json=self.registration).status_code,409)
        self.registration['task_id']+='-coder'
        self.send(self.register())
        usage=self.usage()
        roles={r['role']:r for r in usage['roles']}
        self.assertEqual(set(roles),{'planner','coder'})
        self.assertEqual(roles['planner']['input_tokens'],100)
        self.assertEqual(sum(r['output_tokens'] for r in roles.values()),usage['totals']['output_tokens'])
        self.assertEqual({r['role'] for r in usage['requests']},{'planner','coder'})

    def test_legacy_role_unknown_and_reopening_preserves_usage(self):
        self.send(self.register())
        with self.store.connect() as conn:conn.execute('DELETE FROM task_roles')
        reopened=Store(self.store.path).job_usage(self.registration['job_id'])
        self.assertEqual(reopened['roles'][0]['role'],'unknown')
        self.assertEqual(reopened['totals']['input_tokens'],100)
        self.assertNotIn('capability',json.dumps(reopened))

    def test_auth_scope_and_revocation(self):
        cap = self.register()
        self.assertEqual(self.client.get("/internal/jobs/89cd3a16ad2c4e/usage", headers={"authorization": "Bearer " + cap}).status_code, 401)
        self.assertEqual(self.send("not-a-capability").status_code, 401)
        response = self.client.post("/internal/tasks/" + self.registration["task_id"] + "/revoke", headers=self.control)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.send(cap).status_code, 401)

    def test_budget_and_model_override_are_rejected_before_provider(self):
        self.registration["token_budget"] = 100
        cap = self.register()
        self.assertEqual(self.send(cap, model="claude-opus-5-5").status_code, 400)
        self.assertEqual(self.send(cap).status_code, 429)
        self.assertFalse(self.calls)

    def test_partial_usage_is_retained_and_future_budget_is_blocked(self):
        self.events = self.events[:2]
        cap = self.register()
        response = self.send(cap)
        self.assertIn("Provider stream incomplete", response.text)
        totals = self.usage()["totals"]
        self.assertEqual(totals["input_tokens"], 100)
        self.assertEqual(totals["billing_unknown_requests"], 1)
        self.assertEqual(self.send(cap).status_code, 429)

    def test_provider_error_body_is_not_exposed(self):
        self.provider_status = 401
        self.events = [{"type": "error", "message": "private-provider-key"}]
        response = self.send(self.register())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-key", response.text)

    def test_split_stream_error_is_sanitized(self):
        parser = SSEUsage()
        parser.feed(b'data: {"type": "er')
        with self.assertRaises(ValueError):
            parser.feed(b'ror", "message": "private-provider-key"}\n\n')
        self.events.append({"type": "error", "message": "private-provider-key"})
        response = self.send(self.register())
        self.assertNotIn("private-provider-key", response.text)

    def test_ssrf_and_unknown_multimodal_input_are_rejected(self):
        cap = self.register()
        with patch.dict(os.environ, {"DSLAB_BASE_URL": "https://127.0.0.1"}):
            self.assertEqual(self.send(cap).status_code, 503)
        response = self.send(cap, messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://example.com"}}]}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.calls)

    def test_persistent_usage_is_available_after_gateway_restart(self):
        self.send(self.register())
        reopened = Store(self.store.path)
        self.assertEqual(reopened.job_usage("89cd3a16ad2c4e")["totals"]["output_tokens"], 40)

    def test_repeated_requests_count_separately_and_call_budget_is_enforced(self):
        self.registration["max_calls"] = 2
        cap = self.register()
        self.assertEqual(self.send(cap).status_code, 200)
        self.assertEqual(self.send(cap).status_code, 200)
        self.assertEqual(self.send(cap).status_code, 429)
        self.assertEqual(self.usage()["totals"]["output_tokens"], 80)
        self.assertEqual(len(self.calls), 2)

    def test_transport_exception_cannot_leak_key(self):
        def broken(request):
            raise httpx.ConnectError("private-provider-key")
        self.client = TestClient(create_app(store=self.store, transport=httpx.MockTransport(broken)))
        response = self.send(self.register())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-key", response.text)
        self.assertEqual(self.usage()["totals"]["billing_unknown_requests"], 1)

    def test_registered_effort_cannot_be_escalated_by_task(self):
        self.registration["effort"] = "low"
        self.assertEqual(self.send(self.register(), output_config={"effort": "max"}).status_code, 200)
        forwarded = json.loads(self.calls[0].content)
        self.assertEqual(forwarded["output_config"]["effort"], "low")

    def test_claude_model_defaults_and_explicit_effort_are_preserved(self):
        for index,(model,effort,expected) in enumerate((
                ('claude-sonnet-5-5',None,'high'),('claude-opus-5-5',None,'medium'),
                ('claude-sonnet-5-5','default','high'),('claude-opus-5-5','default','medium'),
                ('claude-sonnet-5-5','low','low'),('claude-opus-5-5','high','high'))):
            self.registration.update(model=model,task_id='claude-default-'+str(index))
            if effort is None:self.registration.pop('effort',None)
            else:self.registration['effort']=effort
            cap=self.register()
            self.assertEqual(self.send(cap,model=model).status_code,200)
            self.assertEqual(json.loads(self.calls[-1].content)['output_config']['effort'],expected)


class LiteLLMGatewayTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.store=Store(Path(temporary.name)/'usage.sqlite3')
        config=patch.dict(os.environ,{'DSLAB_CONTROL_TOKEN':'trusted-control-credential-1234567890',
            'DSLAB_API_KEY':'native-private-key','LITELLM_API_KEY':'litellm-private-key',
            'LITELLM_BASE_URL':'https://proxy.example.com/v1'})
        config.start();self.addCleanup(config.stop)
        self.calls=[];self.status=200
        self.expected_url='https://proxy.example.com/v1/chat/completions'
        self.events=[{'choices':[{'index':0,'delta':{'role':'assistant','content':'Complete'},'finish_reason':None}]},
            {'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':25,
                'prompt_tokens_details':{'cached_tokens':30},'completion_tokens_details':{'reasoning_tokens':10}}}]
        self.done=True
        self.client=TestClient(create_app(store=self.store,transport=httpx.MockTransport(self.upstream)))
        self.control={'authorization':'Bearer '+os.environ['DSLAB_CONTROL_TOKEN']}
        self.task={'task_id':'89cd3a16ad2c4e-round21-fixture','job_id':'89cd3a16ad2c4e',
            'project_id':'94a2c07a7a5b44','model':'Qwen/Qwen3.8-27B','provider':'litellm',
            'role':'planner','token_budget':1000000,'max_output_tokens':8192}

    def upstream(self,request):
        self.calls.append(request)
        self.assertEqual(str(request.url),self.expected_url)
        self.assertEqual(request.headers['authorization'],'Bearer litellm-private-key')
        self.assertNotIn('x-api-key',request.headers)
        self.assertNotIn('anthropic-version',request.headers)
        body=b''.join(sse(event) for event in self.events)
        if self.done:body+=b'data: [DONE]\n\n'
        return httpx.Response(self.status,headers={'content-type':'text/event-stream'},content=body)

    def register(self):
        result=self.client.post('/internal/tasks',headers=self.control,json=self.task)
        self.assertEqual(result.status_code,200,result.text)
        return result.json()['capability']

    def send(self,cap,**overrides):
        data={'model':self.task['model'],'stream':True,'max_tokens':8192,
              'messages':[{'role':'user','content':'Implement one check'}]}
        data.update(overrides)
        return self.client.post('/v1/messages',headers={'authorization':'Bearer '+cap},json=data)

    def usage(self):return self.store.job_usage(self.task['job_id'])

    def test_text_usage_cache_and_unknown_price_are_honest(self):
        response=self.send(self.register())
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('"stop_reason": "end_turn"',response.text)
        self.assertIn('message_stop',response.text)
        usage=self.usage();totals=usage['totals']
        self.assertEqual(usage['provider'],'litellm')
        self.assertEqual(totals['input_tokens'],70)
        self.assertEqual(totals['cache_read_input_tokens'],30)
        self.assertEqual(totals['output_tokens'],25)  # Reasoning is already included.
        self.assertEqual(totals['billing_unknown_requests'],0)
        self.assertIsNone(totals['estimated_rub'])
        self.assertFalse(usage['cost_available'])
        self.assertIsNone(usage['roles'][0]['estimated_rub'])
        self.assertEqual(usage['requests'][0]['role'],'planner')
        self.assertEqual(usage['requests'][0]['provider'],'litellm')
        self.assertNotIn('private-key',response.text+json.dumps(usage))
        forwarded=json.loads(self.calls[0].content)
        self.assertEqual(forwarded['stream_options'],{'include_usage':True})
        self.assertNotIn('reasoning_effort',forwarded)

    def test_five_models_and_efforts_are_validated(self):
        self.assertEqual(len(LITELLM_MODELS),5)
        for index,model in enumerate(LITELLM_MODELS):
            self.task.update(model=model,task_id='fixture-model-'+str(index))
            self.register()
        self.task.update(model='arbitrary/model',task_id='bad-model')
        self.assertEqual(self.client.post('/internal/tasks',headers=self.control,json=self.task).status_code,400)
        self.task.update(model='zai-org/GLM-5.3-Flash',effort='medium',task_id='bad-effort')
        self.assertEqual(self.client.post('/internal/tasks',headers=self.control,json=self.task).status_code,400)
        self.assertFalse(self.calls)

    def test_litellm_never_forwards_effort_even_for_legacy_tasks(self):
        cap=self.register()
        with self.store.connect() as conn:
            conn.execute('UPDATE tasks SET effort=? WHERE task_id=?',('high',self.task['task_id']))
        self.send(cap,output_config={'effort':'max'},thinking={'type':'enabled','budget_tokens':2048})
        forwarded=json.loads(self.calls[-1].content)
        self.assertNotIn('reasoning_effort',forwarded)
        self.assertNotIn('thinking',forwarded)
        self.assertNotIn('output_config',forwarded)
        for field in ('enable_thinking','chat_template_kwargs','extra_body'):
            self.assertNotIn(field,forwarded)

    def test_litellm_new_registrations_accept_only_default(self):
        for index,effort in enumerate(('low','medium','high','max','none','xhigh')):
            self.task.update(effort=effort,task_id='forbidden-effort-'+str(index))
            response=self.client.post('/internal/tasks',headers=self.control,json=self.task)
            self.assertEqual(response.status_code,400)
        self.assertFalse(self.calls)

    def test_native_parallel_tool_arguments_and_reasoning_survive_fragments(self):
        self.events=[
            {'choices':[{'delta':{'reasoning_content':'private thinking'},'index':0,'finish_reason':None}]},
            {'choices':[{'delta':{'tool_calls':[
                {'index':0,'id':'call_a','type':'function','function':{'name':'read_file','arguments':'{"pa'}},
                {'index':1,'id':'call_b','type':'function','function':{'name':'list_files','arguments':'{'}},
            ]},'index':0,'finish_reason':None}]},
            {'choices':[{'delta':{'tool_calls':[
                {'index':0,'function':{'arguments':'th":"src.java"}'}},
                {'index':1,'function':{'arguments':'"path":"."}'}},
            ]},'index':0,'finish_reason':'tool_calls'}]},
            {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':40}},
        ]
        response=self.send(self.register())
        self.assertIn('"type": "thinking_delta"',response.text)
        self.assertIn('"stop_reason": "tool_use"',response.text)
        parser=SSEUsage()
        blocks=parser.feed(response.content)
        calls=[];args={}
        for block in blocks:
            event=json.loads(block.split(b'data: ',1)[1])
            if event['type']=='content_block_start' and event['content_block']['type']=='tool_use':
                calls.append(event['content_block']);args[event['index']]=''
            if event['type']=='content_block_delta' and event['delta']['type']=='input_json_delta':
                args[event['index']]+=event['delta']['partial_json']
        self.assertEqual([c['id'] for c in calls],['call_a','call_b'])
        self.assertEqual([json.loads(a) for a in args.values()],[{'path':'src.java'},{'path':'.'}])
        self.assertTrue(parser.stopped)
        # Feed the converted wire events to the actual unmodified agent parser.
        from agent.dslab import streamed_message
        wire=io.BytesIO(response.content);wire.status=200
        connection=Mock();connection.getresponse.return_value=wire
        with patch('sys.stdout',new_callable=io.StringIO) as public:
            content,reason=streamed_message({},'scoped-capability',connection_factory=lambda **_:connection)
        self.assertEqual(reason,'tool_use')
        self.assertEqual([b['input'] for b in content if b['type']=='tool_use'],[{'path':'src.java'},{'path':'.'}])
        self.assertEqual(content[0]['thinking'],'private thinking')
        self.assertNotIn('private thinking',public.getvalue())
        self.assertNotIn('scoped-capability',public.getvalue())

    def test_tool_history_maps_native_calls_and_results_without_prose_parsing(self):
        messages=[{'role':'user','content':'Need a file'},
            {'role':'assistant','content':[{'type':'thinking','thinking':'retained reasoning'},
                {'type':'tool_use','id':'call_a','name':'read_file','input':{'path':'src.java'}},
                {'type':'text','text':'Reading'}]},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'call_a','content':'file contents','is_error':True}]}]
        tools=[{'name':'read_file','description':'Read file','input_schema':{'type':'object'}}]
        response=self.send(self.register(),system=[{'type':'text','text':'Rules','cache_control':{'type':'ephemeral'}}],
            messages=messages,tools=tools,tool_choice={'type':'auto'})
        self.assertEqual(response.status_code,200)
        data=json.loads(self.calls[0].content)
        self.assertEqual(data['messages'][0],{'role':'system','content':'Rules'})
        self.assertEqual(data['messages'][2]['reasoning_content'],'retained reasoning')
        self.assertEqual(data['messages'][2]['tool_calls'][0]['function']['name'],'read_file')
        self.assertEqual(data['messages'][3],{'role':'tool','tool_call_id':'call_a','content':'Tool error: file contents'})
        self.assertEqual(data['tools'][0]['type'],'function')

    def test_truncated_stream_or_missing_usage_does_not_claim_completed(self):
        self.done=False
        cap=self.register();response=self.send(cap)
        self.assertIn('Provider stream incomplete',response.text)
        self.assertNotIn('message_stop',response.text)
        self.assertEqual(self.usage()['totals']['billing_unknown_requests'],1)
        self.assertEqual(self.send(cap).status_code,429)

    def test_done_without_usage_retains_unknown_billing(self):
        self.events=self.events[:2]
        response=self.send(self.register())
        self.assertIn('Provider stream incomplete',response.text)
        self.assertEqual(self.usage()['totals']['billing_unknown_requests'],1)

    def test_malformed_tools_and_reasoning_only_output_fail_closed(self):
        for delta,finish in (({'tool_calls':[{'index':0,'id':'call_a','function':{'name':'read_file','arguments':'not json'}}]},'tool_calls'),
                             ({'reasoning_content':'No final answer'},'stop')):
            self.task['task_id']+='x'
            self.events=[{'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}]
            response=self.send(self.register())
            self.assertIn('Provider stream interrupted',response.text)
            self.assertNotIn('message_stop',response.text)

    def test_upstream_errors_and_redirects_do_not_leak_credentials(self):
        for status in (401,429,302):
            self.task['task_id']+='x';self.status=status
            self.events=[{'error':{'message':'litellm-private-key'}}]
            response=self.send(self.register())
            self.assertEqual(response.status_code,502)
            self.assertNotIn('litellm-private-key',response.text)
        self.assertEqual(len(self.calls),3)  # SDK retries must remain disabled.

    def test_sdk_connection_error_is_sanitized_and_not_retried(self):
        calls=[]
        def broken(request):
            calls.append(request)
            raise httpx.ConnectError('litellm-private-key')
        self.client=TestClient(create_app(store=self.store,transport=httpx.MockTransport(broken)))
        response=self.send(self.register())
        self.assertEqual(response.status_code,502)
        self.assertIn('Provider connection failed',response.text)
        self.assertNotIn('litellm-private-key',response.text)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.usage()['totals']['billing_unknown_requests'],1)

    def test_sdk_rejects_successful_non_sse_response(self):
        def invalid(request):
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'Wrong protocol'}}]})
        self.client=TestClient(create_app(store=self.store,transport=httpx.MockTransport(invalid)))
        response=self.send(self.register())
        self.assertEqual(response.status_code,502)
        self.assertEqual(self.usage()['totals']['billing_unknown_requests'],1)

    def test_sdk_reads_real_async_chunks_with_wire_terminal_guard(self):
        raw=b''.join(sse(e) for e in self.events)+b'data: [DONE]\n\n'
        class Bytes(httpx.AsyncByteStream):
            async def __aiter__(self):
                for offset in range(0,len(raw),11):yield raw[offset:offset+11]
        def chunks(request):
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Bytes())
        self.client=TestClient(create_app(store=self.store,transport=httpx.MockTransport(chunks)))
        response=self.send(self.register())
        self.assertEqual(response.status_code,200)
        self.assertIn('"stop_reason": "end_turn"',response.text)
        self.assertEqual(self.usage()['totals']['billing_unknown_requests'],0)

    def test_endpoint_is_environment_only_and_rejects_unsafe_shapes(self):
        cap=self.register()
        for url in ('http://proxy.example.com','https://name:password@proxy.example.com',
                    'https://proxy.example.com/v1?key=secret','https://proxy.example.com/other',
                    'https://proxy.example.com:bad'):
            with patch.dict(os.environ,{'LITELLM_BASE_URL':url}):
                self.assertEqual(self.send(cap).status_code,503)
        self.assertEqual(self.send(cap,base_url='https://attacker.example.com').status_code,400)
        self.assertFalse(self.calls)

    def test_authorized_campus_http_proxy_canonical_paths(self):
        cap=self.register()
        self.expected_url='http://lnsigo.mipt.ru:4000/v1/chat/completions'
        for suffix in ('','/','/v1','/v1/','/v1/chat/completions','/v1/chat/completions/'):
            with self.subTest(suffix=suffix), patch.dict(os.environ,{
                    'LITELLM_BASE_URL':'http://lnsigo.mipt.ru:4000'+suffix}):
                self.assertEqual(self.send(cap).status_code,200)
        self.assertEqual(len(self.calls),6)

    def test_campus_http_exception_rejects_lookalikes_and_other_hosts_ports(self):
        cap=self.register()
        for url in ('http://lnsigo.mipt.ru/v1','http://lnsigo.mipt.ru:80/v1',
                    'http://lnsigo.mipt.ru:4001/v1','http://lnsigo.mipt.ru.evil.test:4000/v1',
                    'http://evil-lnsigo.mipt.ru:4000/v1','http://lnsigo.mipt.ru.:4000/v1',
                    'http://127.0.0.1:4000/v1','http://192.168.1.1:4000/v1',
                    'http://169.254.169.254:4000/v1','http://proxy.example.com:4000/v1',
                    'http://user:password@lnsigo.mipt.ru:4000/v1',
                    'http://lnsigo.mipt.ru:4000/v1?next=http://127.0.0.1',
                    'http://lnsigo.mipt.ru:4000/v1#fragment',
                    'http://lnsigo.mipt.ru:4000/v1/../private'):
            with self.subTest(url=url),patch.dict(os.environ,{'LITELLM_BASE_URL':url}):
                self.assertEqual(self.send(cap).status_code,503)
        self.assertFalse(self.calls)

    def test_provider_cannot_change_on_registration_recovery(self):
        self.register()
        self.task.update(provider='dslab',model='claude-sonnet-5-5')
        self.assertEqual(self.client.post('/internal/tasks',headers=self.control,json=self.task).status_code,409)

    def test_reopened_store_keeps_provider_and_unknown_cost(self):
        self.send(self.register())
        usage=Store(self.store.path).job_usage(self.task['job_id'])
        self.assertEqual(usage['provider'],'litellm')
        self.assertFalse(usage['cost_available'])

    def test_litellm_only_configuration_can_be_healthy(self):
        with patch.dict(os.environ,{'DSLAB_API_KEY':''}):
            self.assertEqual(self.client.get('/health').status_code,200)

    def test_bridge_accepts_split_utf8_sse_and_counts_tokens_once(self):
        parser=OpenAIStream(self.task['model'])
        raw=b''.join(sse(e) for e in self.events)+b'data: [DONE]\n\n'
        output=[]
        for offset in range(0,len(raw),7):output.extend(parser.feed(raw[offset:offset+7]))
        self.assertTrue(parser.stopped)
        self.assertEqual(parser.usage['input_tokens']+parser.usage['cache_read_input_tokens'],100)
        self.assertEqual(parser.usage['output_tokens'],25)
        self.assertEqual(sum(b'message_stop' in block for block in output),1)


if __name__ == "__main__":
    unittest.main()
