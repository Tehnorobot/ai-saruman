"""Saruman integration contracts. No account, model call, real world or audio devices."""

import argparse
import importlib.util
import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch

import jsonschema
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from saruman import codex_rpc
from saruman.chat_model import CodexChatModel
from saruman.doctor import validate_profile
from saruman.launcher import ROOT, load_environment, runtime_args


@tool
def walk(x: int, z: int) -> str:
    """Walk to a position in the test world."""
    raise AssertionError("The model adapter must not execute tools")


class ProfileTests(unittest.TestCase):
    def test_both_environment_templates_select_the_same_character_and_models(self):
        from dotenv import dotenv_values

        canonical = dotenv_values(ROOT / ".env.saruman.example")
        self.assertEqual(canonical, dotenv_values(ROOT / ".env.example"))
        self.assertEqual(canonical["SARUMAN_CHARACTER_NAME"], "Saruman")
        self.assertEqual(canonical["CORE_LLM_MODEL_NAME"], "codex/auto")
        self.assertEqual(canonical["STT_BACKEND"], "parakeet")

    def test_character_settings_default_and_environment_precedence(self):
        from config_schema.settings import CharacterSettings

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            primary, fallback = Path(directory) / ".env.saruman", Path(directory) / ".env"
            primary.write_text("SARUMAN_CHARACTER_NAME=Primary\n", encoding="utf-8")
            fallback.write_text("SARUMAN_CHARACTER_NAME=Fallback\n", encoding="utf-8")
            self.assertEqual(CharacterSettings(_env_file=None).name, "Saruman")
            self.assertEqual(CharacterSettings(_env_file=(fallback, primary)).name, "Primary")
            os.environ["SARUMAN_CHARACTER_NAME"] = "Operator"
            self.assertEqual(CharacterSettings(_env_file=(fallback, primary)).name, "Operator")

    def test_voice_files_are_inside_the_active_profile(self):
        import yaml
        from character.character_manager import CharacterManager

        character = CharacterManager("Saruman", force_new=True)
        voice_dir = Path(character.voice_refs_dir).resolve()
        refs = yaml.safe_load(Path(character.voice_refs_manifest_path).read_text(encoding="utf-8"))
        for emotion in refs:
            with self.subTest(emotion=emotion):
                text, filename = character.resolve_voice_ref(emotion)
                self.assertTrue(text)
                self.assertTrue(Path(filename).resolve().is_relative_to(voice_dir))
                self.assertTrue(Path(filename).is_file())

    def test_character_contract_and_stock_voice_files(self):
        with patch.dict(os.environ, {"SARUMAN_CHARACTER_NAME": "Saruman"}):
            self.assertEqual(validate_profile(ROOT), [])
            from character.character_manager import CharacterManager

            character = CharacterManager("Saruman", force_new=True)
            self.assertEqual(character.get_names().Minecraft, "Saruman")
            self.assertEqual(character.get_character_config().GodUsers, [])
            for mode in ("streamer_abstract", "saruman", "saruman_combat"):
                prompt = character.read_character_prompt(mode)
                self.assertIn("Саруман", prompt)
                self.assertNotIn("F9iUx", prompt)
                self.assertIn("Ты — Саруман", prompt)

    def test_no_profile_path_traversal(self):
        for name in ("../OtherCharacter", "..", "x\\y", ""):
            with (
                self.subTest(name=name),
                patch.dict(os.environ, {"SARUMAN_CHARACTER_NAME": name}),
            ):
                self.assertTrue(validate_profile(ROOT))

    def test_environment_respects_explicit_operator_override(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            path = Path(directory) / "settings.env"
            path.write_text(
                "SARUMAN_CHARACTER_NAME=Saruman\nPOSTGRES_DB=saruman\n",
                encoding="utf-8",
            )
            os.environ["POSTGRES_DB"] = "operator_selected"
            load_environment(path)
            self.assertEqual(os.environ["POSTGRES_DB"], "operator_selected")
            self.assertEqual(os.environ["SARUMAN_CHARACTER_NAME"], "Saruman")

    def test_missing_environment_is_not_silently_created(self):
        with self.assertRaises(ValueError):
            load_environment(Path("/nonexistent/saruman-test.env"))

    def test_start_is_paused_and_socials_are_opt_in(self):
        args = argparse.Namespace(
            auto_start=False,
            with_social=False,
            no_stt=False,
            no_filter=False,
            no_redis=False,
        )
        self.assertEqual(
            runtime_args(args), ["saruman-runtime", "--chat", "--stop-auto", "--offline"]
        )
        args.auto_start, args.with_social, args.no_stt = True, True, True
        self.assertEqual(runtime_args(args), ["saruman-runtime", "--chat", "--no-stt"])

    def test_gateway_settings_do_not_start_a_second_agent(self):
        from saruman.minecraft import connect

        env = {
            "MINECRAFT_GATEWAY_HOST": "game-host",
            "MINECRAFT_GATEWAY_PORT": "25400",
            "MINECRAFT_CALLBACK_HOST": "127.0.0.1",
            "MINECRAFT_CALLBACK_PORT": "25401",
            "MINECRAFT_GATEWAY_TIMEOUT": "3",
        }
        with (
            patch.dict(os.environ, env),
            patch("saruman.minecraft.JavaGateway") as factory,
        ):
            callback = object()
            connect(callback)
            options = factory.call_args.kwargs
            self.assertIs(options["python_server_entry_point"], callback)
            self.assertEqual(options["gateway_parameters"].address, "game-host")
            self.assertEqual(options["gateway_parameters"].port, 25400)
            self.assertEqual(options["gateway_parameters"].read_timeout, 3)
            self.assertEqual(options["callback_server_parameters"].port, 25401)

    def test_offline_doctor_never_connects_or_generates(self):
        from saruman.doctor import check

        with (
            patch.dict(
                os.environ,
                {
                    "SARUMAN_CHARACTER_NAME": "Saruman",
                    "POSTGRES_PASSWORD": "fixture",
                    "CORE_LLM_MODEL_NAME": "codex/auto",
                },
            ),
            patch("socket.create_connection", side_effect=AssertionError("network")),
            patch(
                "saruman.codex_rpc.complete", side_effect=AssertionError("generation")
            ),
            redirect_stdout(io.StringIO()),
        ):
            check(live=False)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.result = {"content": "Даже мудрость требует наблюдения.", "tool_calls": []}
        self.call = patch(
            "saruman.codex_rpc.complete",
            return_value=(self.result, {"model": "fixture"}),
        ).start()
        self.addCleanup(patch.stopall)
        self.model = CodexChatModel(model="fixture")
        self.messages = [
            SystemMessage(content="Ты Саруман"),
            HumanMessage(content="Осмотрись"),
        ]

    def test_plain_reply_and_unknown_usage(self):
        response = self.model.invoke(self.messages)
        self.assertEqual(response.content, self.result["content"])
        self.assertIsNone(response.usage_metadata)
        self.assertEqual(response.tool_calls, [])
        self.assertIn("Ты Саруман", self.call.call_args.args[0])

    def test_bound_tools_return_validated_proposals_not_actions(self):
        self.result["tool_calls"] = [{"name": "walk", "arguments": '{"x":10,"z":20}'}]
        response = self.model.bind_tools([walk]).invoke(self.messages)
        self.assertEqual(response.tool_calls[0]["args"], {"x": 10, "z": 20})
        self.assertTrue(response.tool_calls[0]["id"])

    def test_buffered_stream_works_with_existing_chat_agent(self):
        self.result["tool_calls"] = [{"name": "walk", "arguments": '{"x":1,"z":2}'}]
        chunks = list(
            self.model.bind_tools([walk]).stream(self.messages, max_tokens=512)
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].tool_calls[0]["name"], "walk")
        self.call.assert_called_once()

    def test_malformed_outputs_cannot_reach_tools(self):
        bad = [
            [],
            None,
            {"content": 1, "tool_calls": []},
            {"content": "", "tool_calls": [{"name": "unknown", "arguments": "{}"}]},
            {"content": "", "tool_calls": [{"name": "walk", "arguments": "null"}]},
            {
                "content": "",
                "tool_calls": [{"name": "walk", "arguments": '{"x":"wrong","z":2}'}],
            },
            {"content": "", "tool_calls": [{"name": "walk", "arguments": "{"}]},
        ]
        for item in bad:
            self.call.return_value = (item, {})
            with (
                self.subTest(item=item),
                self.assertRaises((ValueError, jsonschema.ValidationError)),
            ):
                self.model.bind_tools([walk]).invoke(self.messages)

    def test_unbound_model_rejects_tool_calls(self):
        self.result["tool_calls"] = [{"name": "walk", "arguments": '{"x":1,"z":2}'}]
        with self.assertRaises(jsonschema.ValidationError):
            self.model.invoke(self.messages)

    def test_required_tool_and_none_contracts(self):
        with self.assertRaises(jsonschema.ValidationError):
            self.model.bind_tools([walk], tool_choice="required").invoke(self.messages)
        response = self.model.bind_tools([walk], tool_choice="none").invoke(
            self.messages
        )
        self.assertFalse(response.tool_calls)

    def test_history_contains_tool_result_and_image_is_not_json_dumped(self):
        self.messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "one", "name": "walk", "args": {"x": 1, "z": 2}}
                    ],
                ),
                ToolMessage(content="arrived", tool_call_id="one"),
                HumanMessage(
                    content=[
                        {"type": "text", "text": "Что видишь?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,fixture"},
                        },
                    ]
                ),
            ]
        )
        self.model.invoke(self.messages)
        inputs = self.call.call_args.args[1]
        self.assertEqual(
            inputs[1], {"type": "image", "url": "data:image/png;base64,fixture"}
        )
        self.assertIn('"tool_call_id": "one"', inputs[0]["text"])
        self.assertIn("arrived", inputs[0]["text"])
        self.assertNotIn("base64,fixture", inputs[0]["text"])

    def test_observed_usage_is_forwarded(self):
        self.call.return_value = (
            self.result,
            {"usage": {"inputTokens": 30, "outputTokens": 5, "totalTokens": 35}},
        )
        self.assertEqual(
            self.model.invoke(self.messages).usage_metadata["total_tokens"], 35
        )

    def test_generation_error_is_not_retried_by_adapter(self):
        self.call.side_effect = RuntimeError("fixture failure")
        with self.assertRaises(RuntimeError):
            self.model.invoke(self.messages)
        self.call.assert_called_once()


class FakeRpc:
    events: ClassVar[list] = []
    account: ClassVar[dict] = {"type": "chatgpt"}
    requests: ClassVar[list] = []

    def __init__(self, *args):
        self.events = list(type(self).events)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def request(self, method, params):
        type(self).requests.append((method, params))
        if method == "account/read":
            return {"account": self.account}
        if method == "model/list":
            return {"data": [{"model": "fixture", "isDefault": True}]}
        if method == "thread/start":
            return {"thread": {"id": "t"}, "model": "fixture"}
        return {}

    def notification(self):
        return self.events.pop(0)


class TransportTests(unittest.TestCase):
    def setUp(self):
        FakeRpc.requests = []
        FakeRpc.account = {"type": "chatgpt"}
        FakeRpc.events = [
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "t",
                    "tokenUsage": {"total": {"totalTokens": 3}},
                },
            },
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "t",
                    "tokenUsage": {"total": {"totalTokens": 5}},
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "t",
                    "item": {
                        "type": "agentMessage",
                        "id": "a",
                        "text": '{"content":"ok","tool_calls":[]}',
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {"threadId": "t", "turn": {"status": "completed"}},
            },
        ]

    def test_turn_protocol_usage_and_no_native_tool_execution(self):
        with patch("saruman.codex_rpc.Rpc", FakeRpc):
            result, trace = codex_rpc.complete(
                "persona", [{"type": "text", "text": "fixture"}], {}, "auto"
            )
        self.assertEqual(result["content"], "ok")
        self.assertEqual(trace["usage"]["totalTokens"], 5)
        thread = next(p for m, p in FakeRpc.requests if m == "thread/start")
        self.assertTrue(thread["ephemeral"])
        self.assertEqual(thread["sandbox"], "read-only")
        self.assertEqual(thread["approvalPolicy"], "never")

    def test_api_key_login_rejected_before_starting_turn(self):
        FakeRpc.account = {"type": "apiKey"}
        with patch("saruman.codex_rpc.Rpc", FakeRpc), self.assertRaises(RuntimeError):
            codex_rpc.complete("", [], {})
        self.assertEqual([m for m, p in FakeRpc.requests], ["account/read"])

    def test_native_codex_tool_requests_are_denied(self):
        rpc = codex_rpc.Rpc()
        rpc.send = Mock()
        rpc.incoming.put({"id": 7, "method": "item/commandExecution/requestApproval"})
        self.assertEqual(rpc._next(time.monotonic() + 1), {})
        self.assertEqual(rpc.send.call_args.args[0]["error"]["code"], -32601)

    def test_eof_and_expired_deadline(self):
        rpc = codex_rpc.Rpc()
        rpc.incoming.put(None)
        with self.assertRaises(RuntimeError):
            rpc._next(time.monotonic() + 1)
        with self.assertRaises(RuntimeError):
            rpc._next(time.monotonic() - 1)

    def test_invalid_and_missing_model_output(self):
        for value in ("not JSON", None):
            self.setUp()
            if value is None:
                FakeRpc.events = [FakeRpc.events[-1]]
            else:
                FakeRpc.events[2]["params"]["item"]["text"] = value
            with (
                patch("saruman.codex_rpc.Rpc", FakeRpc),
                self.assertRaises(RuntimeError),
            ):
                codex_rpc.complete("", [], {}, "fixture")


@unittest.skipUnless(
    importlib.util.find_spec("langchain_litellm"),
    "Full Saruman runtime dependencies required",
)
class SarumanIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(
            os.environ,
            {
                "SARUMAN_CHARACTER_NAME": "Saruman",
                "CORE_LLM_MODEL_NAME": "codex/fixture",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                "HF_HUB_OFFLINE": "1",
            },
        )
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        from config_schema.settings import Settings

        Settings.reset()
        from agent.multi.chat_agent import ChatAgent
        from agent.tools import minecraft_tools, tools_config
        from data_collectors.minebridge import chat_queue_handle

        cls.chat_class = ChatAgent
        cls.tools = minecraft_tools
        cls.config = tools_config
        cls.queue_handle = staticmethod(chat_queue_handle)

    def test_real_tool_to_queue_to_gateway(self):
        from langchain_core.tools import StructuredTool

        from agent.multi.chat_agent import _execute_tool_call

        ctx = {"chat_queue": [], "game": {}, "filter": {"chat_filter_enabled": False}}
        proposed = {
            "content": "Последую за тобой, путник.",
            "tool_calls": [
                {"name": "agent_action", "arguments": '{"action":"@follow Alex"}'}
            ],
        }
        gateway = Mock()
        with (
            patch("saruman.codex_rpc.complete", return_value=(proposed, {})),
            patch.object(self.config, "ctx_swarm", ctx),
            patch.object(self.tools, "_add_tool_run_log", return_value=1),
            patch.object(self.tools, "_add_tool_result_log"),
            patch.object(self.tools, "_add_tool_event_waiter"),
        ):
            model = CodexChatModel().bind_tools(
                [StructuredTool.from_function(self.tools.agent_action)]
            )
            response = model.invoke([HumanMessage(content="Иди за мной")])
            bank = {
                "agent_action": {"function": self.tools.agent_action, "disabled": False}
            }
            call = response.tool_calls[0]
            self.assertTrue(_execute_tool_call(bank, call["name"], call["args"]))
            self.assertEqual(ctx["chat_queue"], ["@t follow Alex"])
            self.queue_handle(gateway, ctx)
        gateway.RunInnerCommand.assert_called_once_with("@t follow Alex")
        gateway.ChatMessage.assert_not_called()
        self.assertEqual(ctx["chat_queue"], [])

    def test_tool_events_use_the_configured_minecraft_identity(self):
        from data_schema.event_convert import convert_tool_call_to_event, convert_autogpt_action_to_event
        from data_schema.chat_structures import ToolCall

        character = Mock()
        character.get_names.return_value.Minecraft = "OperatorSelectedNick"
        with patch("data_schema.event_convert.CharacterManager.get_main_character", return_value=character):
            events = [
                convert_tool_call_to_event("walk", {"x": 1, "z": 2}),
                convert_autogpt_action_to_event(ToolCall(name="walk", args={"x": 1, "z": 2})),
                convert_autogpt_action_to_event(ToolCall(name="thoughts", args={"summary": "Observe"})),
            ]
        self.assertEqual([e.user for e in events], ["OperatorSelectedNick"] * 3)

    def test_shared_reminder_does_not_override_the_character_profile(self):
        from agent.prompt_generation.prompt_constructor import DEFAULT_GOAL

        self.assertNotIn("female", DEFAULT_GOAL)
        self.assertIn("character profile", DEFAULT_GOAL)

    def test_combat_mode_preserves_saruman_identity_and_existing_state(self):
        from agent.tools.control_tools import enter_combat_mode

        context = {"states": {"thoughts": {"summary": "Keep an existing promise"}}, "fx_queue": Mock()}
        handler = Mock()
        with (
            patch.object(self.config, "ctx_swarm", context),
            patch.object(self.config, "ctx_handler", handler),
            patch("agent.tools.control_tools._send_priveleged_command") as send,
        ):
            enter_combat_mode()
        self.assertEqual(context["states"]["personality"], "saruman_combat")
        self.assertEqual(context["states"]["thoughts"]["summary"], "Keep an existing promise")
        context["fx_queue"].put.assert_not_called()
        self.assertIn("Preserve your character and relationships", handler.add_message.call_args.args[0])
        self.assertIn('@stop', send.call_args.args[0])
        self.assertNotIn('@forget', send.call_args.args[0])

    def test_unknown_personality_cannot_replace_the_active_profile(self):
        from agent.tools.control_tools import change_personality

        context = {"states": {"personality": "saruman"}}
        with patch.object(self.config, "ctx_swarm", context):
            result = change_personality("missing_mode")
        self.assertIn("Unknown personality", result)
        self.assertEqual(context["states"]["personality"], "saruman")

    def test_dialogue_uses_the_same_character_profile(self):
        from agent.tools import dialogue_tools
        from character.character_manager import CharacterManager

        expected = CharacterManager.get_main_character().read_character_prompt()
        with (
            patch.object(self.config, "db") as database,
            patch.object(dialogue_tools, "_get_dialogue", return_value=[{"role": "assistant", "content": "Слушаю."}]),
            patch.object(dialogue_tools, "llm") as model,
            patch.object(dialogue_tools, "history", []),
        ):
            database.get_user_id.return_value = 1
            model.invoke.return_value = "Слушаю тебя, путник."
            dialogue_tools.dialogue_step("Alex", "Здравствуйте")
        self.assertEqual(model.invoke.call_args.args[0][0], {"role": "system", "content": expected})

    def test_disabled_game_tool_is_not_executed(self):
        from agent.multi.chat_agent import _execute_tool_call

        action = Mock()
        result = _execute_tool_call(
            {"agent_action": {"function": action, "disabled": True}}, "agent_action", {}
        )
        self.assertIn("disabled", result)
        action.assert_not_called()

    def test_single_nearby_player_status(self):
        ctx = {"game": {"nearby_players": [{"name": "Alex", "health": 20.0}]}}
        with patch.object(self.config, "ctx_swarm", ctx):
            self.assertEqual(self.tools.get_player_status("Alex")["health"], 20.0)
            self.assertEqual(self.tools.get_player_status("Unknown"), {})

    def test_existing_chat_agent_stream_accepts_codex_proposals(self):
        agent = object.__new__(self.chat_class)
        agent.llm = CodexChatModel()
        agent.llm_with_tools = agent.llm.bind_tools([walk])
        agent._can_interrupt = lambda: False
        result = {
            "content": "Ступаю.",
            "tool_calls": [{"name": "walk", "arguments": '{"x":1,"z":2}'}],
        }
        with (
            patch("saruman.codex_rpc.complete", return_value=(result, {})),
            redirect_stdout(io.StringIO()),
        ):
            text, calls, message, interrupted = agent._stream_response(
                [HumanMessage(content="Иди")], Mock()
            )
        self.assertEqual(text, "Ступаю.")
        self.assertEqual(calls[0]["name"], "walk")
        self.assertEqual(message.tool_calls, calls)
        self.assertFalse(interrupted)

    def test_profile_prompt_does_not_require_blind_obedience(self):
        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = {"states": {"personality": "streamer_abstract"}}
        prompt = agent._build_system_prompt()
        self.assertIn("Ты — Саруман", prompt)
        self.assertIn("You may refuse", prompt)
        self.assertNotIn("Players DON'T", prompt)

    def test_model_factory_selects_subscription_adapter(self):
        from agent.llm_clients.lc_clients import get_llm_chain

        self.assertIsInstance(get_llm_chain("codex/fixture"), CodexChatModel)

    def test_full_game_supervisor_assembles_without_external_services(self):
        from agent.multiagent import MultiAgent
        from data_schema.structure_templates import CTX_SWARM_EMPTY

        context = dict(CTX_SWARM_EMPTY)
        handler = Mock()
        handler.ctx_swarm = context
        with (
            patch.dict(os.environ, {"EMBEDDINGS_MODEL": "NONE"}),
            patch.object(self.config, "StreamerDatabase", return_value=Mock()),
            patch.object(self.config, "ctx_swarm", {}),
            patch.object(self.config, "tool_bank", {}),
            patch(
                "saruman.codex_rpc.complete",
                side_effect=AssertionError("No generation during assembly"),
            ),
            redirect_stdout(io.StringIO()),
        ):
            agent = MultiAgent(
                context, ctx_handler=handler, ctx_host=Mock(), use_chat_agent=True
            )
        self.assertTrue(agent.initialized)
        self.assertTrue(agent.chat_agent._tools_supported)
        self.assertIn("agent_action", agent.chat_agent._tool_names)
        self.assertIn("chat", agent.chat_agent._tool_names)
        self.assertFalse(agent.chat_agent._keepalive_enabled)
        self.assertIsNone(agent.core_agent.rag_model)


if __name__ == "__main__":
    unittest.main()
