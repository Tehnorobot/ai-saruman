"""MiniMax Explore native Anthropic protocol fixtures; never use real credentials."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from provider_gateway.main import Store,create_app,minimax_request


class MiniMaxGatewayTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'usage.sqlite')
        config=patch.dict(os.environ,{'MINIMAX_API_KEY':'private-subscription-key',
            'MINIMAX_BASE_URL':'https://api.minimax.io/anthropic',
            'DSLAB_CONTROL_TOKEN':'test-control-secret-with-over-32-characters'})
        config.start();self.addCleanup(config.stop)
        self.calls=[];self.http_status=200
        self.events=[{'type':'message_start','message':{'usage':{
            'input_tokens':100,'cache_read_input_tokens':80,'output_tokens':0}}},
            {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
            {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'OK'}},
            {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':12}},
            {'type':'message_stop'}]
        self.client=TestClient(create_app(store=self.store,transport=httpx.MockTransport(self.upstream)))
        self.control={'authorization':'Bearer '+os.environ['DSLAB_CONTROL_TOKEN']}
        self.task={'task_id':'test-minimax-planner','job_id':'a'*14,'project_id':'b'*14,
            'provider':'minimax','model':'MiniMax-M3.1-Flash','effort':'default','role':'planner',
            'token_budget':1000000,'max_output_tokens':8192,'max_calls':10}

    def upstream(self,request):
        self.calls.append(request)
        self.assertEqual(str(request.url),'https://api.minimax.io/anthropic/v1/messages')
        self.assertEqual(request.headers['authorization'],'Bearer private-subscription-key')
        self.assertNotIn('x-api-key',request.headers)
        self.assertNotIn('test-control',str(request.headers))
        content=b''.join(('data: '+json.dumps(event)+'\n\n').encode() for event in self.events)
        return httpx.Response(self.http_status,headers={'content-type':'text/event-stream'},content=content)

    def register(self):
        response=self.client.post('/internal/tasks',headers=self.control,json=self.task)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['capability']

    def send(self,cap,**overrides):
        payload={'model':'MiniMax-M3.1-Flash','stream':True,'max_tokens':8192,
            'thinking':{'type':'adaptive'},'output_config':{'effort':'none'},
            'messages':[{'role':'user','content':'hello'}],
            'system':[{'type':'text','text':'instructions','cache_control':{'type':'ephemeral'}}]}
        payload.update(overrides)
        return self.client.post('/v1/messages',headers={'authorization':'Bearer '+cap},json=payload)

    def usage(self):
        response=self.client.get('/internal/jobs/'+'a'*14+'/usage',headers=self.control)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_native_stream_and_usage_unknown_subscription_cost(self):
        response=self.send(self.register())
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('message_stop',response.text)
        wire=json.loads(self.calls[0].content)
        self.assertEqual(wire['output_config'],{'effort':'max'})
        self.assertNotIn('thinking',wire)
        self.assertNotIn('cache_control',wire['system'][0])
        usage=self.usage()
        self.assertEqual(usage['provider'],'minimax')
        self.assertEqual(usage['totals']['input_tokens'],100)
        self.assertEqual(usage['totals']['output_tokens'],12)
        self.assertEqual(usage['totals']['cache_read_input_tokens'],80)
        self.assertIsNone(usage['totals']['estimated_rub'])
        self.assertIsNone(usage['roles'][0]['estimated_rub'])
        self.assertIsNone(usage['requests'][0]['estimated_rub'])
        self.assertEqual(usage['roles'][0]['role'],'planner')
        self.assertEqual(usage['totals']['billing_unknown_requests'],0)
        self.assertNotIn('private-subscription-key',json.dumps(usage))

    def test_all_documented_efforts_and_no_unrecognized_values(self):
        for effort in ('default','low','medium','high','xhigh','max'):
            self.task.update(task_id='minimax-'+effort,effort=effort)
            response=self.send(self.register())
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(json.loads(self.calls[-1].content)['output_config']['effort'],
                'max' if effort=='default' else effort)
        count=len(self.calls)
        for effort in ('none','minimal','ultra'):
            self.task.update(task_id='minimax-'+effort,effort=effort)
            self.assertEqual(self.client.post('/internal/tasks',headers=self.control,json=self.task).status_code,400)
        self.assertEqual(len(self.calls),count)

    def test_native_tool_and_thinking_history_remain_unchanged(self):
        thinking={'type':'thinking','thinking':'private chain','signature':'kept-signature'}
        tool={'type':'tool_use','id':'call-1','name':'read_file','input':{'path':'src/A.java','cache_control':'user-data'}}
        messages=[{'role':'assistant','content':[thinking,tool]},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'call-1','content':'source'}]}]
        original=copy.deepcopy(messages)
        self.send(self.register(),messages=messages,tools=[{'name':'read_file','description':'Read',
            'input_schema':{'type':'object','properties':{'cache_control':{'type':'string'}}}}],
            tool_choice={'type':'tool','name':'read_file'})
        wire=json.loads(self.calls[0].content)
        self.assertEqual(wire['messages'],original)
        self.assertEqual(wire['tool_choice'],{'type':'tool','name':'read_file'})
        self.assertIn('cache_control',wire['tools'][0]['input_schema']['properties'])
        self.assertNotIn('private chain',json.dumps(self.usage()))

    def test_stream_thinking_and_tool_call_are_native_not_parsed_from_prose(self):
        self.events=[self.events[0],
            {'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':''}},
            {'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'private reasoning'}},
            {'type':'content_block_delta','index':0,'delta':{'type':'signature_delta','signature':'retained'}},
            {'type':'content_block_stop','index':0},
            {'type':'content_block_start','index':1,'content_block':{
                'type':'tool_use','id':'call-a','name':'read_file','input':{}}},
            {'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':'{"path":"src/A.java"}'}},
            {'type':'content_block_stop','index':1},
            {'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':42}},
            {'type':'message_stop'}]
        response=self.send(self.register(),tools=[{'name':'read_file','description':'Read',
            'input_schema':{'type':'object','properties':{'path':{'type':'string'}}}}])
        self.assertEqual(response.status_code,200)
        wire=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(wire,self.events)
        self.assertEqual(self.usage()['totals']['output_tokens'],42)
        self.assertNotIn('private reasoning',json.dumps(self.usage()))

    def test_error_stream_redacts_provider_message_and_retains_partial_usage(self):
        self.events=self.events[:1]+[{'type':'error','error':{'message':'private-subscription-key'}}]
        response=self.send(self.register())
        self.assertNotIn('private-subscription-key',response.text)
        self.assertIn('Provider stream interrupted',response.text)
        self.assertEqual(self.usage()['totals']['input_tokens'],100)
        self.assertEqual(self.usage()['totals']['billing_unknown_requests'],1)

    def test_strict_endpoint_does_not_send_key_elsewhere(self):
        cap=self.register()
        for base in ('http://api.minimax.io/anthropic','https://evil.example/anthropic',
                'https://api.minimax.io:444/anthropic','https://user:pass@api.minimax.io/anthropic',
                'https://api.minimax.io/anthropic?key=x','https://api.minimax.io/v1',
                'https://api.minimax.io/anthropic#frag'):
            with self.subTest(base=base),patch.dict(os.environ,{'MINIMAX_BASE_URL':base}):
                self.assertEqual(self.send(cap).status_code,503)
        self.assertFalse(self.calls)

    def test_no_fallback_to_dslab_key_when_subscription_key_missing(self):
        with patch.dict(os.environ,{'MINIMAX_API_KEY':'','DSLAB_API_KEY':'other-provider-key'}):
            self.assertEqual(self.send(self.register()).status_code,503)
        self.assertFalse(self.calls)

    def test_recovery_cannot_change_provider_model_effort_or_reset_budget(self):
        cap=self.register();self.send(cap)
        self.assertEqual(self.register(),cap)
        self.assertEqual(self.usage()['totals']['requests'],1)
        for field,value in (('provider','dslab'),('model','MiniMax-M3'),('effort','high'),('token_budget',2000000)):
            task={**self.task,field:value}
            self.assertIn(self.client.post('/internal/tasks',headers=self.control,json=task).status_code,(400,409))

    def test_budget_exhaustion_has_no_retry(self):
        self.task['max_calls']=1;cap=self.register()
        self.assertEqual(self.send(cap).status_code,200)
        self.assertEqual(self.send(cap).status_code,429)
        self.assertEqual(len(self.calls),1)

    def test_incomplete_stream_keeps_usage_and_blocks_new_budgeted_request(self):
        self.events=self.events[:-1];cap=self.register()
        response=self.send(cap)
        self.assertIn('Provider stream incomplete',response.text)
        self.assertEqual(self.usage()['totals']['output_tokens'],12)
        self.assertEqual(self.usage()['totals']['billing_unknown_requests'],1)
        self.assertEqual(self.send(cap).status_code,429)
        self.assertEqual(len(self.calls),1)

    def test_upstream_errors_and_redirects_are_not_followed_or_exposed(self):
        self.http_status=302
        response=self.send(self.register())
        self.assertEqual(response.status_code,502)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('private-subscription-key',response.text)

    def test_minimax_only_health_is_ready_without_other_provider_keys(self):
        with patch.dict(os.environ,{'DSLAB_API_KEY':'','LITELLM_API_KEY':''}):
            self.assertEqual(self.client.get('/health').status_code,200)

    def test_conversion_does_not_mutate_input_or_keep_claude_options(self):
        original={'messages':[],'thinking':{'type':'enabled','budget_tokens':5000},
            'output_config':{'effort':'none','format':{'type':'json'}},'temperature':.2,'top_p':.9}
        before=copy.deepcopy(original)
        converted=minimax_request(original,{'effort':'medium'})
        self.assertEqual(original,before)
        self.assertEqual(converted['output_config'],{'effort':'medium'})
        self.assertNotIn('thinking',converted)
        self.assertNotIn('temperature',converted)
