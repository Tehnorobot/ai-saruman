"""Mock provider errors retain useful detail without credential leakage."""
import asyncio
import json
import unittest

import httpx

from provider_gateway.diagnostics import rejection, sanitize
from tests import test_minimax_gateway as fixture
from tests import test_dslab_gateway as sdk_fixture


class RejectionDiagnosticsTests(unittest.TestCase):
    def test_long_body_and_protocol_summary_are_preserved_and_redacted(self):
        body={'error':{'message':'bad tool_result ordering '+('explanation '*400),
                       'api_key':'arbitrary-private','detail':'Bearer another-secret'},
              'echo':'provider-secret cap-secret'}
        response=httpx.Response(400,json=body,headers={'x-request-id':'req-42','set-cookie':'secret-cookie'})
        data={'model':'model','max_tokens':32192,'output_config':{'effort':'high'},
              'messages':[{'role':'user','content':'private prompt'},
                          {'role':'assistant','content':[{'type':'tool_use','id':'u1','name':'read_file','input':{'path':'private path'}}]},
                          {'role':'user','content':[{'type':'tool_result','tool_use_id':'wrong','content':'private result'}]}],
              'tools':[]}
        detail=asyncio.run(rejection(response,data,provider='minimax',request_id='gateway-42',secrets=('provider-secret','cap-secret')))
        raw=json.dumps(detail)
        for secret in ('arbitrary-private','another-secret','provider-secret','cap-secret','secret-cookie','private prompt','private result','private path'):
            self.assertNotIn(secret,raw)
        self.assertIn('bad tool_result ordering',raw)
        self.assertTrue(detail['diagnostic']['response_body']['error']['message'].endswith('explanation '))
        self.assertEqual(detail['diagnostic']['request']['messages'][2]['blocks'][0]['tool_use_id'],'wrong')
        self.assertEqual(detail['diagnostic']['response_headers']['x-request-id'],'req-42')
        self.assertFalse(detail['diagnostic']['body_truncated'])

    def test_non_json_rejection_has_readable_body(self):
        response=httpx.Response(400,text='parameter max_tokens invalid; api_key=private-value')
        detail=asyncio.run(rejection(response,{'messages':[]},provider='minimax',request_id='42'))
        self.assertIn('parameter max_tokens invalid',detail['diagnostic']['response_body'])
        self.assertNotIn('private-value',str(detail))

    def test_malformed_nested_and_surrogate_bodies_do_not_crash_diagnostics(self):
        for body in ('['*1100+'0'+']'*1100, '{"error":"\\ud800"}'):
            detail=asyncio.run(rejection(httpx.Response(400,text=body),{'messages':[]},provider='minimax',request_id='42'))
            json.dumps(detail,ensure_ascii=False).encode('utf-8')
        self.assertNotIn('provider-secret',json.dumps(sanitize({'provider-secret':'echo'},('provider-secret',))))


class NativeGatewayDiagnosticTests(unittest.TestCase):
    setUp=fixture.MiniMaxGatewayTests.setUp
    register=fixture.MiniMaxGatewayTests.register
    send=fixture.MiniMaxGatewayTests.send
    usage=fixture.MiniMaxGatewayTests.usage
    upstream=fixture.MiniMaxGatewayTests.upstream

    def test_rejection_details_are_persisted_for_usage_history(self):
        self.http_status=400
        self.events=[{'error':{'message':'invalid tools: '+('long detail '*200),'api_key':'private-subscription-key'}}]
        cap=self.register();response=self.send(cap)
        self.assertEqual(response.status_code,502)
        detail=response.json()['detail']
        self.assertEqual(detail['diagnostic']['upstream_status'],400)
        self.assertNotIn('private-subscription-key',response.text)
        self.assertNotIn(cap,response.text)
        request=self.usage()['requests'][0]
        self.assertEqual(json.loads(request['diagnostic_json']),detail)
        self.assertEqual(request['status'],'upstream_rejected')


class SDKGatewayDiagnosticTests(unittest.TestCase):
    setUp=sdk_fixture.LiteLLMGatewayTests.setUp
    register=sdk_fixture.LiteLLMGatewayTests.register
    send=sdk_fixture.LiteLLMGatewayTests.send
    usage=sdk_fixture.LiteLLMGatewayTests.usage
    upstream=sdk_fixture.LiteLLMGatewayTests.upstream

    def test_sdk_http_error_uses_same_diagnostics_and_persistence(self):
        self.status=400
        cap=self.register();response=self.send(cap)
        self.assertEqual(response.status_code,502)
        detail=response.json()['detail']
        self.assertIsInstance(detail,dict)
        self.assertEqual(detail['diagnostic']['provider'],'litellm')
        self.assertEqual(detail['diagnostic']['upstream_status'],400)
        self.assertEqual(json.loads(self.usage()['requests'][0]['diagnostic_json']),detail)


if __name__=='__main__':unittest.main()
