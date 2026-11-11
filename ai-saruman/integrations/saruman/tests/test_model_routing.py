"""Provider selection reaches actual game, legacy and vision requests; no paid calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langchain_core.messages import HumanMessage
from agent.llm_clients.lc_clients import get_llm_chain
from saruman.role_client import RoleClient, RoleRequestError
from saruman.role_settings import RoleStore, default_profile


class ModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{"SARUMAN_ROLE_DB":str(Path(self.temp.name)/"roles.sqlite"),"MINIMAX_API_KEY":"test-secret"})
        self.env.start();self.addCleanup(self.env.stop)
        self.store=RoleStore()

    def select(self, role, **changes):
        p=self.store.profiles()[role];p.pop("revision")
        self.store.save(role,{**p,**changes},paused=True)

    def response(self,value,reason="tool_use"):
        return httpx.Response(200,request=httpx.Request("POST","https://api.minimax.io/anthropic/v1/messages"),json={
            "model":"MiniMax-M3.1-Flash","stop_reason":reason,"content":[{"type":"thinking","thinking":"internal"},
            {"type":"tool_use","name":"submit_role_result","input":value}],
            "usage":{"input_tokens":10,"output_tokens":7,"cache_read_input_tokens":20}})

    def test_action_and_persona_select_independent_providers(self):
        self.select("action",provider="minimax",model="MiniMax-M3.1-Flash",reasoning="low",max_output=32768)
        value={"result":"action"}
        with patch("saruman.role_client.httpx.Client") as http,patch("saruman.codex_rpc.complete",return_value=({"result":"persona"},{"usage":{"totalTokens":3}})) as codex:
            http.return_value.__enter__.return_value.post.return_value=self.response(value)
            self.assertEqual(RoleClient(self.store,"action","s").complete("s",{}, {"type":"object"}),value)
            self.assertEqual(RoleClient(self.store,"persona","s").complete("s",{}, {"type":"object"}),{"result":"persona"})
            body=http.return_value.__enter__.return_value.post.call_args.kwargs["json"]
            self.assertEqual(body["output_config"],{"effort":"low"})
            codex.assert_called_once()
        rows={r["role"]:r for r in self.store.history()}
        self.assertEqual(rows["action"]["provider"],"minimax")
        self.assertEqual(rows["action"]["usage"]["input_tokens"],30)
        self.assertEqual(rows["persona"]["provider"],"codex")

    def test_legacy_vision_routes_images_and_reloads_model_after_save(self):
        role="tool_vision"
        self.select(role,provider="minimax",model="MiniMax-M3.1-Flash",reasoning="low",max_output=32768)
        model=get_llm_chain(role=role)
        with patch("saruman.role_client.httpx.Client") as http,patch("saruman.codex_rpc.complete",return_value=({"content":"Codex view","tool_calls":[]},{"model":"gpt-6.1-terra","usage":{"inputTokens":3,"outputTokens":2,"totalTokens":5}})) as codex:
            post=http.return_value.__enter__.return_value.post
            post.return_value=self.response({"content":"MiniMax view","tool_calls":[]})
            message=model.invoke([HumanMessage(content=[{"type":"text","text":"Look"},{"type":"image_url","image_url":{"url":"data:image/png;base64,YQ=="}}])])
            self.assertEqual(message.content,"MiniMax view")
            self.assertEqual(message.response_metadata["provider"],"minimax")
            self.assertEqual(message.usage_metadata["input_tokens"],30)
            body=post.call_args.kwargs["json"]
            self.assertEqual(body["messages"][0]["content"][1]["type"],"image")
            self.select(role,provider="codex",model="gpt-6.1-terra",reasoning="high")
            message=model.invoke([HumanMessage(content="Look again")])
            self.assertEqual(message.content,"Codex view")
            self.assertEqual(codex.call_args.args[3],"gpt-6.1-terra")
            self.assertEqual(codex.call_args.kwargs["reasoning_effort"],"high")

    def test_no_fallback_on_minimax_unauthorized_and_error_is_sanitized(self):
        self.select("action",provider="minimax",model="MiniMax-M3.1-Flash")
        client=RoleClient(self.store,"action","s")
        with patch("saruman.role_client.httpx.Client") as http,patch("saruman.codex_rpc.complete") as codex:
            http.return_value.__enter__.return_value.post.return_value=httpx.Response(401,request=httpx.Request("POST","https://api.minimax.io/anthropic/v1/messages"),json={"error":"test-secret"})
            with self.assertRaises(RoleRequestError) as caught:
                client.complete("s",{}, {"type":"object"})
            self.assertNotIn("test-secret",str(caught.exception))
            self.assertIn("HTTP 401",str(caught.exception))
            codex.assert_not_called()

    def test_text_json_is_accepted_but_incomplete_output_keeps_usage(self):
        p={**default_profile(),"provider":"minimax","model":"MiniMax-M3.1-Flash"}
        response=httpx.Response(200,request=httpx.Request("POST","https://api.minimax.io/anthropic/v1/messages"),json={
            "stop_reason":"end_turn","content":[{"type":"text","text":'{"ok":true}'}],"usage":{"input_tokens":2,"output_tokens":3}})
        with patch("saruman.role_client.httpx.Client") as http:
            post=http.return_value.__enter__.return_value.post
            post.return_value=response
            self.assertEqual(RoleClient._http(p,"s","u",{})[0],{"ok":True})
            post.return_value=self.response({"ok":True},reason="max_tokens")
            with self.assertRaises(ValueError) as caught:
                RoleClient._http(p,"s","u",{})
            self.assertEqual(caught.exception.usage["total_tokens"],37)
            post.return_value=httpx.Response(200,request=httpx.Request("POST","https://api.minimax.io/anthropic/v1/messages"),json=[])
            with self.assertRaises(ValueError) as malformed:
                RoleClient._http(p,"s","u",{})
            self.assertEqual(malformed.exception.usage,{})
