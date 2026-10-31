"""Saruman integration contracts. No account, model call, real world or audio devices."""

import argparse
import importlib.util
import io
import os
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stdout
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
    def test_inventory_reports_hunger_separately_from_health_and_omits_empty_slots(self):
        from saruman.minecraft import read_player_inventory

        with patch("saruman.minecraft.JavaGateway") as factory:
            entry = factory.return_value.entry_point
            entry.getInventoryFull.return_value = {"ok": True, "slots": [
                {"item": "minecraft:bread", "count": 33, "index": 7}, {"empty": True, "index": 0}]}
            entry.getGameState.return_value = {"self": {"hp": 20, "food": 15, "saturation": 0, "pos": "1,63,2"}}
            result = read_player_inventory()
        self.assertEqual((result["health"], result["food"]), (20, 15))
        self.assertEqual(len(result["items"]), 1)
        factory.return_value.close.assert_called_once()

    def test_eating_verifies_consumption_and_always_releases_use(self):
        from saruman.minecraft import consume_inventory_food

        before = {"ok": True, "selectedHotbar": 0,
                  "slots": [{"index": 7, "item": "minecraft:bread", "count": 3}]}
        after = {"ok": True, "slots": [{"index": 7, "item": "minecraft:bread", "count": 2}]}
        for fail in (False, True):
            with self.subTest(fail=fail), patch("saruman.minecraft.JavaGateway") as factory:
                gateway = factory.return_value
                gateway.jvm.adris.altoclef.TaskCatalogue.getItemMatches.return_value = [object()]
                entry = gateway.entry_point
                entry.getInventoryFull.side_effect = [before, after]
                entry.getGameState.side_effect = [{"self": {"food": 15}}, {"self": {"food": 20}}]
                entry.useHeldItem.return_value = {"ok": True}
                if fail:
                    entry.useHeldItem.side_effect = OSError("test connection loss")
                    with self.assertRaises(OSError):
                        consume_inventory_food("bread")
                else:
                    result = consume_inventory_food("bread")
                    self.assertTrue(result["consumed"])
                    self.assertEqual((result["count_before"], result["count_after"]), (3, 2))
                self.assertEqual([c.args[0] for c in entry.executeAgentActions.call_args_list], [{"use": 1}, {"use": 0}])
                self.assertEqual([c.args[0] for c in entry.selectHotbar.call_args_list], [7, 0])
                gateway.close.assert_called_once()

    def test_full_hunger_does_not_press_use_or_change_inventory(self):
        from saruman.minecraft import consume_inventory_food

        with patch("saruman.minecraft.JavaGateway") as factory:
            gateway = factory.return_value
            gateway.jvm.adris.altoclef.TaskCatalogue.getItemMatches.return_value = [object()]
            entry = gateway.entry_point
            entry.getInventoryFull.return_value = {"ok": True}
            entry.getGameState.return_value = {"self": {"food": 20}}
            self.assertEqual(consume_inventory_food("bread")["reason"], "hunger_full")
            entry.executeAgentActions.assert_not_called()
            entry.selectHotbar.assert_not_called()
            entry.clickUiSlot.assert_not_called()

    def test_game_action_rejects_prose_unknown_commands_and_command_chains(self):
        from saruman.minecraft import validate_action_command

        for action in ("Осмотри окрестности", "Проверь task_chain", "Explore the area",
                       "@goto nearby water", "@goto", "@stop now", "@invented 1",
                       "@goto 1 64 2; stop", "@follow Alex\n@stop", "", "@"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate_action_command(action)
        self.assertEqual(validate_action_command("goto 10 64 -20"), "@goto 10 64 -20")
        self.assertEqual(validate_action_command("@stop"), "@stop")
        self.assertEqual(validate_action_command("@follow saruman-mag"), "@follow saruman-mag")
        with self.assertRaisesRegex(ValueError, "forbidden"):
            validate_action_command("@custom2 test", ["custom2"])

    def test_block_observation_closes_its_connection_without_running_commands(self):
        from saruman.minecraft import read_nearby_blocks

        with patch("saruman.minecraft.JavaGateway") as factory:
            gateway = factory.return_value
            gateway.entry_point.inGame.return_value = True
            gateway.entry_point.getBlocksAround.return_value = [{"block": "water", "position": "1, 63, 2"}]
            self.assertEqual(read_nearby_blocks(2), [{"block": "water", "position": "1, 63, 2"}])
            gateway.close.assert_called_once()
            gateway.entry_point.RunInnerCommand.assert_not_called()
            gateway.entry_point.ChatMessage.assert_not_called()
        with patch("saruman.minecraft.JavaGateway") as factory, self.assertRaises(ValueError):
            read_nearby_blocks(6)
        factory.assert_not_called()

    def test_operator_text_is_a_directive_even_in_the_tool_console(self):
        from saruman.control import submit_operator_input

        handler, command_queue = Mock(), Mock()
        for force_directive in (False, True):
            submit_operator_input("  Осмотрись и пройди несколько блоков.  ", handler, command_queue,
                                  directive=force_directive)
        self.assertEqual(handler.add_message.call_count, 2)
        handler.add_message.assert_called_with("Осмотрись и пройди несколько блоков.", duplicate_check=False)
        command_queue.put.assert_not_called()

    def test_operator_tool_commands_still_use_the_existing_executor(self):
        from saruman.control import submit_operator_input

        handler, command_queue = Mock(), Mock()
        for command in ("@stop", "/spawn", "!interrupt_voice", "> hello"):
            submit_operator_input(command, handler, command_queue)
            command_queue.put.assert_called_with({"command": command, "env": "system"})
        handler.add_message.assert_not_called()
        command_queue.reset_mock()
        submit_operator_input("  ", handler, command_queue)
        submit_operator_input("hello", None, command_queue)
        command_queue.put.assert_not_called()
        handler.add_message.assert_not_called()

    def test_both_environment_templates_select_the_same_character_and_models(self):
        from dotenv import dotenv_values

        canonical = dotenv_values(ROOT / ".env.saruman.example")
        self.assertEqual(canonical, dotenv_values(ROOT / ".env.example"))
        self.assertEqual(canonical["SARUMAN_CHARACTER_NAME"], "Saruman")
        self.assertEqual(canonical["CORE_LLM_MODEL_NAME"], "codex/gpt-6-sol")
        self.assertEqual(canonical["CODEX_REASONING_EFFORT"], "low")
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
        self.assertEqual(Path(character.resolve_voice_ref("angry")[1]).name, "evil.wav")
        self.assertEqual(character.resolve_voice_ref("battle"), character.resolve_voice_ref("angry"))
        self.assertEqual(character.resolve_voice_ref("happy"), character.resolve_voice_ref("neutral"))
        self.assertEqual(character.resolve_voice_ref("unknown"), character.resolve_voice_ref("neutral"))

    def test_character_contract_and_voice_files(self):
        with patch.dict(os.environ, {"SARUMAN_CHARACTER_NAME": "Saruman"}):
            self.assertEqual(validate_profile(ROOT), [])
            from character.character_manager import CharacterManager

            character = CharacterManager("Saruman", force_new=True)
            self.assertEqual(character.get_names().Minecraft, "saruman-mag")
            self.assertEqual(character.get_character_config().GodUsers, [])
            self.assertEqual(character.get_character_config().VoicePlaybackRate, 1.0)
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

    def test_vanilla_chat_author_is_recovered_without_rewriting_server_messages(self):
        from saruman.minecraft import normalize_chat_message

        self.assertEqual(
            normalize_chat_message({"user": None, "msg": "<saruman-mag> hello"}),
            {"user": "saruman-mag", "msg": "hello"},
        )
        self.assertEqual(
            normalize_chat_message({"user": None, "msg": "<Alex_123> hello"}),
            {"user": "Alex_123", "msg": "hello"},
        )
        for event in (
            {"user": None, "msg": "Welcome to the server"},
            {"user": "Alex", "msg": "<saruman-mag> quoted message"},
        ):
            self.assertEqual(normalize_chat_message(event), event)

    def test_filter_readiness_requires_loaded_models_and_a_real_http_response(self):
        from saruman.doctor import filter_is_ready

        for body, expected in (
            (b'{"status":"ok","models_loaded":true}', True),
            (b'{"status":"ok","models_loaded":false}', False),
            (b'{"status":"ok"}', False),
            (b'[]', False),
            (b'not json', False),
        ):
            with self.subTest(body=body), patch("saruman.doctor.urlopen") as request:
                request.return_value.__enter__.return_value = io.BytesIO(body)
                self.assertEqual(filter_is_ready("http://filter:22230/"), expected)
                request.assert_called_once_with("http://filter:22230/health", timeout=3)
        with patch("saruman.doctor.urlopen", side_effect=OSError("connection closed")):
            self.assertFalse(filter_is_ready("http://filter:22230"))

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

    def request(self, method, params, **kwargs):
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
        from saruman.usage import UsageTracker
        self.tracker = UsageTracker()
        self.telemetry = patch.object(codex_rpc, "usage_tracker", self.tracker)
        self.telemetry.start()
        self.addCleanup(self.telemetry.stop)
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

    def test_low_effort_live_cumulative_tokens_and_global_limits(self):
        limits = {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 22, "windowDurationMins": 300}}}
        FakeRpc.events.insert(0, {"method": "account/rateLimits/updated", "params": limits})
        original_notification = FakeRpc.notification
        live_totals = []

        def notification(rpc):
            live_totals.append(self.tracker.snapshot()["totals"]["totalTokens"])
            return original_notification(rpc)

        with patch("saruman.codex_rpc.Rpc", FakeRpc), patch.object(FakeRpc, "notification", notification):
            codex_rpc.complete("", [], {}, "gpt-6-sol", reasoning_effort="low")
        turn = next(p for m, p in FakeRpc.requests if m == "turn/start")
        self.assertEqual((turn["model"], turn["effort"]), ("gpt-6-sol", "low"))
        self.assertIn(3, live_totals)
        self.assertIn(5, live_totals)
        state = self.tracker.snapshot()
        self.assertEqual(state["totals"]["totalTokens"], 5)  # not 3 + 5
        self.assertEqual(state["limits"]["codex"]["primary"]["usedPercent"], 22)
        self.assertEqual(state["finished"], 1)
        self.assertFalse(state["active"])

    def test_failed_turn_keeps_observed_usage_and_has_no_live_request(self):
        FakeRpc.events[-1]["params"]["turn"]["status"] = "failed"
        with patch("saruman.codex_rpc.Rpc", FakeRpc), self.assertRaises(RuntimeError):
            codex_rpc.complete("", [], {}, "fixture")
        state = self.tracker.snapshot()
        self.assertEqual(state["failed"], 1)
        self.assertEqual(state["totals"]["totalTokens"], 5)
        self.assertEqual(state["recent"][0]["status"], "ошибка")
        self.assertFalse(state["active"])

    def test_limits_failure_does_not_prevent_generation(self):
        original_request = FakeRpc.request

        def request(rpc, method, params, **kwargs):
            if method == "account/rateLimits/read":
                raise RuntimeError("telemetry offline")
            return original_request(rpc, method, params, **kwargs)

        with patch("saruman.codex_rpc.Rpc", FakeRpc), patch.object(FakeRpc, "request", request):
            result, _ = codex_rpc.complete("", [], {}, "fixture")
        self.assertEqual(result["content"], "ok")
        self.assertTrue(self.tracker.snapshot()["limits_failed"])

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
    @staticmethod
    @contextmanager
    def redis_game():
        """Production Redis proxies/JSON codec over an isolated hash transport."""
        from data_flow.redis_collections import RedisDict

        values = {}
        client = Mock()
        client.hget.side_effect = lambda key, field: values.get((key, field))

        def hset(key, field, value):
            values[key, field] = value.encode("utf-8")
            return 1

        client.hset.side_effect = hset
        with patch("data_flow.redis_collections.get_redis", return_value=client):
            game = RedisDict("test_tree_search")
            game["tree_search"] = {}
            yield game

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
        from tests.fake_world_rules import FakeWorldRules
        rules=patch("saruman.trees.NativeWorldRules",FakeWorldRules)
        rules.start()
        cls.addClassCleanup(rules.stop)

    def test_minecraft_identity_is_our_own_history_not_an_external_speaker(self):
        from agent.prompt_generation.message_builder import _is_self_event
        from agent.prompt_generation.prompt_constructor import UserInfo
        from character.character_manager import CharacterManager

        nick = CharacterManager.get_main_character().get_names().Minecraft
        own_tool = {"user": nick, "type": "tool_call", "self": True}
        self.assertTrue(_is_self_event(own_tool, "Саруман"))
        self.assertTrue(_is_self_event({"user": nick, "type": "chat"}, "Саруман"))
        self.assertFalse(_is_self_event({"user": "Alex", "type": "chat"}, "Саруман"))
        self.assertIn("is yourself", str(UserInfo(name=nick)))

    def test_real_tool_to_queue_to_gateway(self):
        from langchain_core.tools import StructuredTool

        from agent.multi.chat_agent import _execute_tool_call
        from agent.prompt_generation.message_builder import events_to_messages
        from data_schema.event_convert import convert_to_chat_message
        from saruman.control import submit_operator_input

        ctx = {"chat_queue": [], "game": {}, "filter": {"chat_filter_enabled": False}}
        proposed = {
            "content": "Последую за тобой, путник.",
            "tool_calls": [
                {"name": "agent_action", "arguments": '{"action":"@follow Alex"}'}
            ],
        }
        gateway = Mock()
        events = []
        handler, command_queue = Mock(), Mock()
        handler.add_message.side_effect = lambda text, **kwargs: events.append(convert_to_chat_message(text).to_dict())
        submit_operator_input("Иди за мной", handler, command_queue)
        self.assertEqual(events[0]["type"], "directive")
        messages, _ = events_to_messages(events)
        self.assertIn("Иди за мной", "\n".join(str(message.content) for message in messages))
        command_queue.put.assert_not_called()
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
            response = model.invoke(messages)
            bank = {
                "agent_action": {"function": self.tools.agent_action, "disabled": False}
            }
            call = response.tool_calls[0]
            self.assertTrue(_execute_tool_call(bank, call["name"], call["args"]))
            self.assertEqual(ctx["chat_queue"], ["@follow Alex"])
            self.queue_handle(gateway, ctx)
        gateway.RunInnerCommand.assert_called_once_with("@follow Alex")
        gateway.ChatMessage.assert_not_called()
        self.assertEqual(ctx["chat_queue"], [])

    def test_rejected_prose_action_is_corrected_after_tool_error_despite_voice_preamble(self):
        from agent.multi.chat_agent import _execute_tool_call

        ctx = {"chat_queue": [], "game": {}, "filter": {"chat_filter_enabled": False}}
        agent = object.__new__(self.chat_class)
        agent._cached_prefix = [SystemMessage(content="Test persona")]
        agent.tool_bank = {"agent_action": {"function": self.tools.agent_action, "disabled": False}}
        bad = {"name": "agent_action", "args": {"action": "Осмотри окрестности и найди землю у воды; не ломай постройки."}, "id": "bad"}
        good = {"name": "agent_action", "args": {"action": "@goto 10 64 20"}, "id": "good"}
        final = "Команда движения передана; проверю результат. *neutral*"
        agent._stream_response = Mock(side_effect=[
            ("Начинаю искать землю. *neutral*", [bad], AIMessage(content="preamble", tool_calls=[bad]), False),
            ("Ступаю по выбранному пути. *neutral*", [good], AIMessage(content="preamble", tool_calls=[good]), False),
            (final, [], AIMessage(content=final), False),
        ])
        agent._send_as_speech = Mock()
        waiter, gateway = Mock(), Mock()
        waiter.check.return_value = False
        with (
            patch.object(self.config, "ctx_swarm", ctx),
            patch.object(self.tools, "_add_tool_run_log", return_value=1) as run_log,
            patch.object(self.tools, "_add_tool_result_log"),
            patch.object(self.tools, "_add_tool_event_waiter"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(agent._respond_with_tools([HumanMessage(content="Осмотрись")], waiter), final)
            run_log.assert_called_once()
            self.assertEqual(ctx["chat_queue"], ["@goto 10 64 20"])
            self.queue_handle(gateway, ctx)
        gateway.RunInnerCommand.assert_called_once_with("@goto 10 64 20")
        results = [m.content for m in agent._cached_prefix if isinstance(m, ToolMessage)]
        self.assertTrue(results[0].startswith("Error"))
        self.assertEqual(agent._stream_response.call_count, 3)
        agent._send_as_speech.assert_called_once_with(final)

    def test_native_stop_is_not_replaced_by_forget(self):
        ctx = {"chat_queue": [], "game": {"available_commands": ["stop", "forget"]}}
        with (
            patch.object(self.config, "ctx_swarm", ctx),
            patch.object(self.tools, "_add_tool_run_log", return_value=1),
            patch.object(self.tools, "_add_tool_result_log"),
            patch.object(self.tools, "_add_tool_event_waiter"),
        ):
            self.assertTrue(self.tools.agent_action("@stop"))
        self.assertEqual(ctx["chat_queue"], ["@stop"])

    def test_bread_uses_existing_wheat_and_native_crop_gathering_is_rejected(self):
        for item in ("wooden_hoe", "bread"):
            ctx = {"chat_queue": [], "game": {"available_commands": ["get"]}}
            with (
                self.subTest(item=item),
                patch.object(self.config, "ctx_swarm", ctx),
                patch.object(self.tools, "_add_tool_run_log", return_value=1),
                patch.object(self.tools, "_add_tool_result_log"),
                patch.object(self.tools, "_add_tool_event_waiter"),
                patch.object(self.tools, "read_player_inventory", return_value={
                    "items": [{"item": "minecraft:wheat", "count": 3}]}),
            ):
                result = self.tools.obtain_item(item, 1)
            self.assertEqual(ctx["chat_queue"], [f"@get {item} 1"])
            self.assertIn("Completion not confirmed", result)
        for item in ("wheat_seeds", "wheat", "carrot", "potato", "beetroot_seeds"):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "farm_crops"):
                self.tools.obtain_item(item, 1)
        with patch.object(self.tools, "read_player_inventory", return_value={"items": []}), self.assertRaisesRegex(ValueError, "already harvested wheat"):
            self.tools.obtain_item("bread", 1)
        with patch.object(self.config, "ctx_swarm", {"chat_queue": [], "game": {"available_commands": ["get"]}}), self.assertRaisesRegex(ValueError, "animals"):
            self.tools.obtain_item("bed", 1)

    def test_bridge_rechecks_bread_and_rejects_raw_seed_collection(self):
        for command, inventory, accepted in (
            ("@get wheat_seeds 4", {"ok": True, "slots": []}, False),
            ("@get bread 1", {"ok": True, "slots": []}, False),
            ("@get bread 1", {"ok": False}, False),
            ("@get bread 1", None, False),
            ("@get bread 1", {"ok": True, "slots": [
                {"item": "minecraft:wheat", "count": 3}]}, True),
        ):
            with self.subTest(command=command, inventory=inventory):
                ctx = {"chat_queue": [command], "game": {}, "filter": {"chat_filter_enabled": False}}
                gateway = Mock()
                gateway.getInventoryFull.return_value = inventory
                self.queue_handle(gateway, ctx)
                if accepted:
                    gateway.RunInnerCommand.assert_called_once_with(command)
                else:
                    gateway.RunInnerCommand.assert_not_called()
                gateway.ChatMessage.assert_not_called()

    def test_death_blocks_game_tools_and_discards_queued_commands(self):
        from agent.multi.chat_agent import _execute_tool_call

        game = {"alive": False, "respawn": {"status": "respawn_requested", "death_id": "fixture"}}
        action = Mock()
        result = _execute_tool_call({"farm_crops": {"function": action}}, "farm_crops", {}, game=game)
        self.assertEqual(result["status"], "waiting_for_respawn")
        action.assert_not_called()
        observation = Mock(return_value={"food": 20})
        _execute_tool_call({"get_inventory": {"function": observation}}, "get_inventory", {}, game=game)
        observation.assert_called_once()
        gateway = Mock()
        ctx = {"chat_queue": ["@goto 10 63 20"], "game": game}
        self.queue_handle(gateway, ctx)
        self.assertEqual(ctx["chat_queue"], [])
        gateway.RunInnerCommand.assert_not_called()

    def test_death_discards_old_procedure_once_and_keeps_operator_pause(self):
        agent = self.chat_class.__new__(self.chat_class)
        agent.ctx_swarm = {"game": {"respawn": {"death_id": "first"}}}
        agent._minecraft_procedure = Mock()
        agent._autonomy = Mock()
        agent._sync_player_lifecycle()
        self.assertIsNone(agent._minecraft_procedure)
        agent._autonomy.end_activity.assert_called_once()
        agent._sync_player_lifecycle()
        agent._autonomy.end_activity.assert_called_once()

    def test_water_rescue_blocks_model_commands_queued_commands_and_background_work(self):
        from agent.multi.chat_agent import _execute_tool_call
        from saruman.autonomy import Autonomy

        game = {"alive": True, "ingame": True, "survival": {"active": True, "status": "surfacing"}}
        work = Mock()
        result = _execute_tool_call({"farm_crops": {"function": work}}, "farm_crops", {}, game=game)
        self.assertEqual(result["status"], "waiting_for_survival")
        work.assert_not_called()
        gateway = Mock()
        ctx = {"game": game, "chat_queue": ["@follow Alex"]}
        self.queue_handle(gateway, ctx)
        gateway.RunInnerCommand.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            autonomy = Autonomy(clock=lambda: 100, settings_path=Path(directory) / "settings.json")
            self.assertIsNone(autonomy.tick(game, work))
            self.assertEqual(autonomy.snapshot(game)["phase"], "survival")
            work.assert_not_called()

    def test_water_rescue_cancels_old_continuation_once_without_resuming_operator_pause(self):
        agent = self.chat_class.__new__(self.chat_class)
        agent.ctx_swarm = {"game": {"survival": {"active": True}}}
        agent._minecraft_procedure = Mock()
        agent._autonomy = Mock()
        agent._sync_player_lifecycle()
        self.assertIsNone(agent._minecraft_procedure)
        agent._sync_player_lifecycle()
        agent._autonomy.yield_to_combat.assert_called_once()
        agent._autonomy.directive.assert_not_called()
        agent.ctx_swarm["game"]["survival"]["active"] = False
        agent._sync_player_lifecycle()
        agent.ctx_swarm["game"]["survival"]["active"] = True
        agent._sync_player_lifecycle()
        self.assertEqual(agent._autonomy.yield_to_combat.call_count, 2)

    def test_server_player_messages_preempt_background_in_fifo_order(self):
        from data_schema.chat_structures import CtxEventBase
        from saruman.autonomy import Autonomy
        from agent.multi.chat_agent import _execute_tool_call

        with tempfile.TemporaryDirectory() as directory:
            agent = self.chat_class.__new__(self.chat_class)
            game = {"ingame": True, "alive": True, "hasActiveTask": False}
            agent.ctx_swarm = {"game": game, "chat_queue": []}
            agent._last_directive_ts = 0
            agent.ctx_handler = Mock()
            events = [CtxEventBase(msg="Come here", user=user, date="", env="minecraft",
                                   type="chat", processing_timestamp=index,
                                   additional_fields={"filter_results": {"acceptable": True}})
                      for index, user in [(1, "Alex"), (2, "Bob")]]
            agent.ctx_handler.get_ctx_chat.return_value = list(reversed(events))
            tracker = agent.ctx_handler.activity_tracker
            tracker.level, tracker.idle_seconds = "HIGH_ACTIVITY", 0
            agent._autonomy = Autonomy(clock=lambda: 9, settings_path=Path(directory) / "settings.json")
            job = next(a for a in agent._autonomy.activities if a["id"] == "farm")
            agent._autonomy.job = {**job, "steps": 1, "started": 0}
            agent._autonomy.last_selection = {"id": "farm", "status": "planted"}
            game["hasActiveTask"] = True
            stop, work = Mock(return_value=True), Mock()
            agent.tool_bank = {"agent_action": {"function": stop}, "farm_crops": {"function": work}}
            results = []
            def reply(event):
                result = _execute_tool_call(agent.tool_bank, "farm_crops", {}, game=game)
                results.append(result)
            agent.respond = Mock(side_effect=reply)
            agent.step()
            self.assertEqual(agent.respond.call_args.args[0].user, "Alex")
            stop.assert_called_once_with(action="@stop")
            work.assert_not_called()
            self.assertEqual(agent._autonomy.pending[0]["id"], "farm")
            agent.step()
            self.assertEqual(agent.respond.call_args.args[0].user, "Bob")
            self.assertEqual(agent.respond.call_count, 2)
            self.assertEqual(len(agent._player_inbox.pending), 0)
            self.assertEqual([result["status"] for result in results], ["waiting_for_task_stop"] * 2)
            work.assert_not_called()
            agent.ctx_handler.wait_for_sync.return_value = events[0]
            agent.step()
            self.assertEqual(agent.respond.call_count, 2)
            stop.assert_called_once_with(action="@stop")

    def test_background_tree_tool_plants_without_overwriting_a_saved_goal(self):
        saved = {"status": "paused", "config": {"count": 2}, "targets": [[2, 63, 4]]}
        ctx = {"game": {"hasActiveTask": False, "tree_task": saved}}
        with (
            patch.object(self.config, "ctx_swarm", ctx),
            patch.object(self.tools, "read_player_inventory", return_value={
                "items": [{"item": "minecraft:birch_sapling", "count": 1}]}),
            patch.object(self.tools, "find_tree_spots", return_value={
                "spots": [{"position": [3, 63, 4], "within_reach": True}]}),
            patch.object(self.tools, "plant_sapling_at", return_value={"planted": True}) as plant,
            patch.object(self.tools, "apply_bone_meal_at") as fertilize,
        ):
            result = self.tools.tend_trees()
        self.assertEqual(result["status"], "planted")
        plant.assert_called_once_with(3, 63, 4, sapling="birch_sapling")
        fertilize.assert_not_called()
        self.assertEqual(ctx["game"]["tree_task"], saved)
        self.assertEqual(ctx["game"]["background_tree_sites"], [[3, 63, 4]])

    def test_background_tree_rejections_are_not_unknown_interactions(self):
        ctx = {"game": {}}
        with (patch.object(self.config, "ctx_swarm", ctx),
              patch.object(self.tools, "read_player_inventory", return_value={"items": [{"item": "minecraft:oak_sapling", "count": 1}]}),
              patch.object(self.tools, "find_tree_spots", return_value={"spots": [{"position": [3,63,4], "within_reach": True}]}),
              patch.object(self.tools, "plant_sapling_at") as plant,
              patch.object(self.tools, "tree_approach", return_value=None)):
            for status in ("protected_or_obstructed", "invalid_soil_or_occupied", "aim_failed", "needs_move"):
                plant.return_value = {"planted": False, "status": status}
                result = self.tools.tend_trees()
                self.assertEqual(result["status"], "blocked", status)
                self.assertEqual(result["placement_status"], status)
                self.assertEqual(result["position"], [3,63,4])

    def test_background_tree_needs_move_proposes_a_real_approach(self):
        with (patch.object(self.config, "ctx_swarm", {"game": {}}),
              patch.object(self.tools, "read_player_inventory", return_value={"items": [{"item": "minecraft:oak_sapling", "count": 1}]}),
              patch.object(self.tools, "find_tree_spots", return_value={"spots": [{"position": [3,63,4], "within_reach": True}]}),
              patch.object(self.tools, "plant_sapling_at", return_value={"planted": False, "status": "needs_move"}),
              patch.object(self.tools, "tree_approach", return_value={"command": "@goto 2 63 3"})):
            result = self.tools.tend_trees()
            self.assertEqual(result["status"], "needs_move")
            self.assertEqual(result["next_action"]["arguments"], {"action": "@goto 2 63 3"})

    def test_player_conversation_suspends_and_resumes_counted_goal_without_reset(self):
        from saruman.tree_tasks import new_task, task_config
        from saruman.autonomy import Autonomy
        from data_schema.chat_structures import CtxEventBase

        with tempfile.TemporaryDirectory() as directory:
            now = [9]
            task = new_task(task_config(2, "plant"))
            task["targets"] = [{"position": [1, 63, 2], "stage": "planted", "owned": True}]
            game = {"ingame": True, "alive": True, "hasActiveTask": True,
                    "tree_task": task, "tree_search": {"visited": [[1, 63, 2]]}}
            agent = self.chat_class.__new__(self.chat_class)
            agent.ctx_swarm = {"game": game, "chat_queue": []}
            agent._autonomy = Autonomy(clock=lambda: now[0], settings_path=Path(directory) / "settings.json")
            stop = Mock(return_value=True)
            agent.tool_bank = {"agent_action": {"function": stop}}
            request = CtxEventBase(msg="Hello", user="Alex", env="minecraft", type="chat", date="", processing_timestamp=1)
            agent._begin_player_turn(request)
            self.assertEqual(game["tree_task"]["status"], "paused")
            self.assertEqual(len(game["deferred_goals"]), 1)
            stop.assert_called_once_with(action="@stop")
            agent._end_player_turn()
            game["hasActiveTask"] = False
            now[0] = 20
            agent._resume_deferred_goal(agent._autonomy, game)
            self.assertEqual(game["tree_task"]["status"], "active")
            self.assertEqual(game["tree_task"]["id"], task["id"])
            self.assertEqual(game["tree_task"]["targets"], task["targets"])
            self.assertEqual(game["deferred_goals"], [])

    def test_suspended_player_goal_is_resumed_ahead_of_background_goal(self):
        from saruman.tree_tasks import new_task, task_config
        from saruman.autonomy import Autonomy

        with tempfile.TemporaryDirectory() as directory:
            background = new_task(task_config(1, "plant"))
            player = new_task(task_config(2, "grow"))
            player["priority_source"] = "player"
            game = {"ingame": True, "alive": True, "hasActiveTask": False,
                    "deferred_goals": [{"task": background, "search": {}}, {"task": player, "search": {}}]}
            agent = self.chat_class.__new__(self.chat_class)
            agent.ctx_swarm = {"game": game}
            agent._autonomy = Autonomy(clock=lambda: 9, settings_path=Path(directory) / "settings.json")
            agent._resume_deferred_goal(agent._autonomy, game)
            self.assertEqual(game["tree_task"]["id"], player["id"])
            self.assertEqual(game["deferred_goals"][0]["task"]["id"], background["id"])

            # A restart checkpoint requires explicit continuation, even if the
            # most recent foreground goal has already finished.
            game["tree_task"]["status"] = "completed"
            game["deferred_goals"][0]["task"].update(
                status="paused", pause_reason="Runtime restarted; verify the world before resuming")
            agent._resume_deferred_goal(agent._autonomy, game)
            self.assertEqual(game["tree_task"]["id"], player["id"])
            self.assertEqual(len(game["deferred_goals"]), 1)

    def test_bone_meal_tool_is_registered_and_codex_proposal_reaches_targeted_interaction(self):
        from agent.tools.tools import ALL_TOOLS
        from agent.multi.chat_agent import _execute_tool_call

        self.assertIn(self.tools.apply_bone_meal, ALL_TOOLS)
        proposal = {"content": "", "tool_calls": [{"name": "apply_bone_meal", "arguments": '{"x":1,"y":63,"z":2}'}]}
        expected = {"applied": True, "consumed": 1, "tree_grown": False}
        with (
            patch("saruman.codex_rpc.complete", return_value=(proposal, {})),
            patch.object(self.tools, "apply_bone_meal_at", return_value=expected) as apply,
            redirect_stdout(io.StringIO()),
        ):
            message = CodexChatModel().bind_tools([self.tools.apply_bone_meal]).invoke([HumanMessage(content="Примени костную муку к саженцу")])
            tool_call = message.tool_calls[0]
            result = _execute_tool_call(
                {"apply_bone_meal": {"function": self.tools.apply_bone_meal, "disabled": False}},
                tool_call["name"], tool_call["args"],
            )
        apply.assert_called_once_with(1, 63, 2)
        self.assertEqual(result, expected)

    def test_water_checkpoint_resumes_automatically_without_resetting_progress_or_budget(self):
        from copy import deepcopy
        from saruman.autonomy import Autonomy
        from saruman.tree_tasks import new_task, task_config
        task = new_task(task_config(2, "grow"))
        task.update(status="paused", pause_reason="Water rescue", bone_meal_charged=3, resume_world=7)
        task["targets"] = [{"position": [1,63,2], "stage": "grown", "owned": True},
                           {"position": [5,63,2], "stage": "planted", "owned": True}]
        game = {"ingame": True, "alive": True, "hasActiveTask": False, "tree_task": task, "world_session": 7,
                "deferred_goals": [{"task": deepcopy(task), "search": {"moves_used": 2}}]}
        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = {"game": game}
        with tempfile.TemporaryDirectory() as directory:
            autonomy = Autonomy(clock=lambda: 9, settings_path=Path(directory)/"settings.json")
            agent._resume_deferred_goal(autonomy, game)
            # A different connection/world must never inherit these coordinates.
            game["tree_task"]["status"] = "paused"
            game["world_session"] = 8
            agent._resume_deferred_goal(autonomy, game)
            self.assertEqual(game["tree_task"]["status"], "paused")
            game["tree_task"]["status"] = "active"
        self.assertEqual(game["tree_task"]["status"], "active")
        self.assertEqual(game["tree_task"]["id"], task["id"])
        self.assertEqual(game["tree_task"]["bone_meal_charged"], 3)
        self.assertEqual(game["tree_task"]["targets"][1]["position"], [5,63,2])
        self.assertEqual(game["tree_search"]["moves_used"], 2)
        self.assertEqual(game["deferred_goals"], [])

    def test_bridge_combat_reads_shared_permissions_and_tools_do_not_duplicate_attacks(self):
        from agent.tools.status_tools import publish_combat_controls, toggle_tool
        game = {"combat_runtime": "reflex-v2", "self_defense": {"result": {"status": "attacked"}}}
        registry = {"defend_self": {"disabled": False}, "cast_scepter": {"disabled": False}}
        with (patch.object(self.config, "tool_bank", registry),
              patch.object(self.config, "ctx_swarm", {"game": game}),
              patch("saruman.hunting.defend_step") as attack,
              redirect_stdout(io.StringIO())):
            publish_combat_controls()
            self.assertEqual(game["combat_controls"], {"defend_self": True, "cast_scepter": True})
            self.assertEqual(self.tools.defend_self()["status"], "attacked")
            attack.assert_not_called()
            toggle_tool("defend_self", True)
            self.assertFalse(game["combat_controls"]["defend_self"])
            toggle_tool("cast_scepter", True)
            self.assertFalse(game["combat_controls"]["cast_scepter"])

    def test_background_search_shares_dead_end_retreat_across_activities(self):
        from saruman.terrain_search import plan_exploration

        game = {"world_session": 7}
        center = [0, 63, 0]
        paths = {(0, 63, 0): 0, (1, 63, 0): 1, (2, 63, 0): 2}

        def step(failures, state, steps):
            result = plan_exploration(paths, tuple(center), state, steps, failures)
            return {**result, "status": "needs_move" if result.get("next_action") else "no_work"}

        with patch.object(self.config, "ctx_swarm", {"game": game}), patch.object(self.tools, "exploration_step", side_effect=step):
            self.assertEqual(self.tools.explore_nearby(purpose="farm")["position"], [2, 63, 0])
            center[:] = [2, 63, 0]
            paths = {(2, 63, 0): 0, (1, 63, 0): 1, (0, 63, 0): 2}
            retreat = self.tools.explore_nearby(purpose="farm")
            self.assertEqual(retreat["route_kind"], "backtrack")
            center[:] = [0, 63, 0]
            paths = {(0, 63, 0): 0, (1, 63, 0): 1, (2, 63, 0): 2, (0, 63, 2): 2}
            next_branch = self.tools.explore_nearby(purpose="hunt")
        self.assertEqual(next_branch["position"], [0, 63, 2])
        self.assertIsNone(game["work_search_retreat"])

    def test_scepter_tools_are_registered_with_real_function_calling_schemas(self):
        from agent.tools.tools import ALL_TOOLS
        from agent.tools.status_tools import get_langchain_tool

        for name in ("get_scepter_status", "ensure_scepter", "cast_scepter", "defend_self"):
            function = getattr(self.tools, name)
            self.assertIn(function, ALL_TOOLS)
            tool = get_langchain_tool(function)
            self.assertEqual(tool.name, name)
        schema = get_langchain_tool(self.tools.cast_scepter).args_schema.model_json_schema()
        self.assertEqual(set(schema["properties"]), {"action", "uuid", "slot"})

    def test_terrain_summary_keeps_all_types_with_bounded_positions(self):
        blocks = [{"block": "stone", "position": f"{i},60,1"} for i in range(1000)]
        blocks.append({"block": "red bed", "position": "2,64,2"})
        with patch.object(self.tools, "read_nearby_blocks", return_value=blocks):
            result = self.tools.get_nearby_blocks(5)
        self.assertTrue(result["truncated"])
        groups = {entry["block"]: entry for entry in result["blocks_by_type"]}
        self.assertEqual(groups["stone"]["count"], 1000)
        self.assertEqual(len(groups["stone"]["positions"]), 12)
        self.assertEqual(groups["red bed"]["positions"], ["2,64,2"])

    def test_tool_rounds_refresh_game_state_after_waiting(self):
        agent = object.__new__(self.chat_class)
        agent._cached_prefix = [SystemMessage(content="Test persona")]
        agent.tool_bank = {"wait": {"function": lambda seconds: True, "disabled": False}}
        wait_call = {"name": "wait", "args": {"seconds": 2}, "id": "waiting"}
        agent._stream_response = Mock(side_effect=[
            ("Wait for movement", [wait_call], AIMessage(content="Waiting", tool_calls=[wait_call]), False),
            ("Arrived. *neutral*", [], AIMessage(content="Arrived. *neutral*"), False),
        ])
        agent._send_as_speech = Mock()
        refresh = Mock(side_effect=[HumanMessage(content="hasActiveTask: True"), HumanMessage(content="hasActiveTask: False; arrived")])
        waiter = Mock()
        waiter.check.return_value = False
        with redirect_stdout(io.StringIO()):
            agent._respond_with_tools([HumanMessage(content="old position")], waiter, refresh_context=refresh)
        tails = [call.args[0][-1].content for call in agent._stream_response.call_args_list]
        self.assertEqual(tails, ["hasActiveTask: True", "hasActiveTask: False; arrived"])

    def test_directive_submitted_before_start_is_processed_once_without_waiting(self):
        from data_schema.activity_tracker import ActivityTracker
        from data_schema.event_convert import convert_to_chat_message

        agent = object.__new__(self.chat_class)
        agent.ctx_handler = Mock()
        agent.ctx_handler.activity_tracker = ActivityTracker()
        agent.ctx_handler.ctx_swarm = {"chat_queue": [], "tts_queue": []}
        agent._last_directive_ts = time.time_ns()
        stale = convert_to_chat_message("Directive from previous session")
        stale.processing_timestamp = agent._last_directive_ts - 1
        pending = convert_to_chat_message("Walk a few blocks and stop")
        pending.processing_timestamp = agent._last_directive_ts + 1
        agent.ctx_handler.get_ctx_chat.return_value = [stale, pending]
        agent.respond = Mock()
        agent.ctx_handler.wait_for_sync.return_value = None
        with redirect_stdout(io.StringIO()):
            agent.step()
            agent.ctx_handler.wait_for_sync.assert_not_called()
            agent.ctx_handler.wait_for_sync.return_value = pending
            agent.step()
        agent.respond.assert_called_once_with(pending)
        agent.ctx_handler.wait_for_sync.assert_called_once()

    def test_navigation_failure_wakes_procedure_once_and_preserves_original_goal(self):
        from data_schema.activity_tracker import ActivityTracker
        from saruman.procedures import ProcedureContinuation

        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = {"game": {"hasActiveTask": False, "navigation": {
            "status": "blocked", "event_id": 123, "reason": "No net approach",
        }}}
        agent.ctx_handler = Mock()
        agent.ctx_handler.activity_tracker = ActivityTracker()
        agent.ctx_handler.ctx_swarm = {"chat_queue": [], "tts_queue": []}
        agent.ctx_handler.get_ctx_chat.return_value = []
        agent.ctx_handler.wait_for_sync.return_value = None
        agent._last_directive_ts = time.time_ns()
        agent._minecraft_procedure = ProcedureContinuation("Вырастить дерево")
        agent._minecraft_procedure.pending = True
        agent.respond = Mock()
        with redirect_stdout(io.StringIO()):
            agent.step()
            agent.ctx_handler.wait_for_sync.assert_not_called()
            agent.step()
        agent.respond.assert_called_once()
        self.assertIn("Вырастить дерево", agent.respond.call_args.args[0].msg)
        self.assertIn("No net approach", agent.respond.call_args.args[0].msg)
        self.assertFalse(agent._minecraft_procedure.pending)
        self.assertEqual(agent._minecraft_procedure.remaining, 3)
        agent.ctx_handler.wait_for_sync.assert_called_once()

    def test_failed_destination_is_rejected_before_enqueue_and_bridge_checks_live_y(self):
        from saruman.navigation import NavigationGuard

        ctx = {"chat_queue": [], "game": {"navigation": {"recent_failures": [
            {"target": [10, 63, 0], "until": time.time() + 120, "reason": "loop"}
        ]}}}
        with patch.object(self.config, "ctx_swarm", ctx), self.assertRaisesRegex(ValueError, "recently failed"):
            self.tools.agent_action("@goto 11 63 0")
        self.assertFalse(ctx["chat_queue"])
        ctx["chat_queue"] = ["@goto 10 64 0"]
        gateway = Mock()
        gateway.getBlockAt.return_value = {"id": "minecraft:air", "is_air": "true", "replaceable": "true"}
        with redirect_stdout(io.StringIO()), patch("data_collectors.minebridge.traceback.print_exc"):
            rules=Mock()
            rules.support.return_value=False
            rules.standing.return_value=False
            self.queue_handle(gateway, ctx, NavigationGuard(rules_factory=lambda: rules))
        gateway.RunInnerCommand.assert_not_called()
        self.assertEqual(ctx["game"]["navigation"]["status"], "rejected")

    def test_tree_search_tool_keeps_memory_and_new_directive_resets_it(self):
        from data_schema.event_convert import convert_to_chat_message
        from langchain_core.tools import StructuredTool

        ctx = {"game": {}}
        with (patch.object(self.config, "ctx_swarm", ctx),
              patch("saruman.trees._gateway") as gateway):
            entry = gateway.return_value.entry_point
            entry.getGameState.return_value = {"self": {"pos": "0,63,0"}}
            entry.getBlocksAround.return_value = [
                {"position": f"{x},62,{z}", "block": "stone"}
                for x in range(-5, 6) for z in range(-5, 6)]
            tool = StructuredTool.from_function(self.tools.find_tree_planting_spots)
            first = tool.invoke({"search_steps": 1, "candidate_limit": 256})
            second = tool.invoke({"search_steps": 1, "candidate_limit": 256})
            self.assertEqual(first["exploration"]["command"], second["exploration"]["command"])
            self.assertEqual(len(ctx["game"]["tree_search"]["proposals"]), 1)
        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = ctx
        agent.initialized = False
        agent.respond(convert_to_chat_message("Посади саженец дуба"))
        self.assertEqual(ctx["game"]["tree_search"], {})

    def test_tree_search_tools_persist_plain_state_through_real_redis_proxy(self):
        from data_flow.redis_collections import RedisNestedDict
        from data_schema.event_convert import convert_to_chat_message
        from langchain_core.tools import StructuredTool

        for function in (self.tools.find_tree_planting_spots, self.tools.grow_tree):
            with (self.subTest(tool=function.__name__), self.redis_game() as game,
                  patch.object(self.config, "ctx_swarm", {"game": game}),
                  patch("saruman.trees._gateway") as gateway,
                  patch("saruman.trees._game_busy", return_value=False),
                  patch("saruman.trees.read_player_inventory", return_value={"position": "0,63,0", "items": [
                      {"item": "minecraft:oak_sapling", "count": 1},
                      {"item": "minecraft:bone_meal", "count": 3}]})):
                self.assertIsInstance(game["tree_search"], RedisNestedDict)
                entry = gateway.return_value.entry_point
                entry.getGameState.return_value = {"self": {"pos": "0,63,0"}}
                entry.getBlocksAround.return_value = [
                    {"position": f"{x},62,{z}", "block": "stone"}
                    for x in range(-5, 6) for z in range(-5, 6)]
                tool = StructuredTool.from_function(function)
                first = tool.invoke({"sapling": "oak_sapling"})
                first_scan = first.get("scan", first)
                second = tool.invoke({"sapling": "oak_sapling"})
                self.assertEqual(first_scan["exploration"]["command"], second.get("scan", second)["exploration"]["command"])
                persisted = dict(game["tree_search"].items())
                self.assertIsInstance(persisted, dict)
                self.assertEqual(len(persisted["visited"]), 1)
                self.assertEqual(len(persisted["proposals"]), 1)
                # A new scan from the proposed position must append and persist
                # another area/goal instead of mutating a detached proxy list.
                position = first_scan["exploration"]["position"]
                x, y, z = position
                entry.getGameState.return_value = {"self": {"pos": f"{x},{y},{z}"}}
                entry.getBlocksAround.return_value = [
                    {"position": f"{i},62,{j}", "block": "stone"}
                    for i in range(x - 5, x + 6) for j in range(z - 5, z + 6)]
                tool.invoke({"sapling": "oak_sapling"})
                self.assertEqual(len(game["tree_search"]["visited"]), 2)
                self.assertEqual(len(game["tree_search"]["proposals"]), 2)
                tool.invoke({"sapling": "birch_sapling"})
                self.assertEqual(game["tree_search"]["sapling"], "birch_sapling")
                self.assertEqual(len(game["tree_search"]["proposals"]), 1)
                agent = object.__new__(self.chat_class)
                agent.ctx_swarm = {"game": game}
                agent.initialized = False
                agent.respond(convert_to_chat_message("Посади саженец дуба"))
                self.assertEqual(dict(game["tree_search"].items()), {})
                entry.RunInnerCommand.assert_not_called()
                entry.placeBlockLooking.assert_not_called()

    def test_failed_tree_scan_does_not_commit_partial_state_in_either_backend(self):
        import copy

        for redis_backend in (False, True):
            with self.subTest(redis=redis_backend), self.redis_game() as redis_game:
                game = redis_game if redis_backend else {}
                original = {"sapling": "oak_sapling", "visited": [[0, 63, 0]], "proposals": []}
                game["tree_search"] = copy.deepcopy(original)

                def failed_scan(*args, **kwargs):
                    state = args[4]
                    state["visited"].append([4, 63, 0])
                    raise RuntimeError("snapshot failed")

                for function, target in ((self.tools.find_tree_planting_spots, "find_tree_spots"),
                                         (self.tools.grow_tree, "grow_tree_fast")):
                    with (patch.object(self.config, "ctx_swarm", {"game": game}),
                          patch.object(self.tools, target, side_effect=failed_scan),
                          self.assertRaisesRegex(RuntimeError, "snapshot failed")):
                        function()
                    self.assertEqual(dict(game["tree_search"].items()), original)

    def test_counted_tree_task_persists_progress_in_redis_and_stop_preserves_it(self):
        from langchain_core.tools import StructuredTool
        from agent.tools.tools import ALL_TOOLS

        self.assertIn(self.tools.manage_trees, ALL_TOOLS)
        world = {}
        inventory = {"position": "0,63,0", "items": [{"item": "minecraft:oak_sapling", "count": 2}]}
        def plant(x, y, z, sapling):
            world[x,y,z] = "minecraft:" + sapling
            inventory["items"][0]["count"] -= 1
            return {"planted": True}
        with (self.redis_game() as game,
              patch.object(self.config, "ctx_swarm", {"game": game}),
              patch("saruman.tree_tasks.trees._game_busy", return_value=False),
              patch("saruman.tree_tasks.trees.read_player_inventory", return_value=inventory),
              patch("saruman.tree_tasks._block", side_effect=lambda p: world.get(tuple(p), "minecraft:air")),
              patch("saruman.tree_tasks.trees.find_tree_spots", return_value={"spots": [
                  {"position": [2,63,0], "within_reach": True}, {"position": [-2,63,0], "within_reach": True}], "existing_saplings": []}),
              patch("saruman.tree_tasks.trees.plant_sapling_at", side_effect=plant) as planting):
            tool = StructuredTool.from_function(self.tools.manage_trees)
            first = tool.invoke({"count": 2})
            self.assertEqual(first["progress"]["completed"], 1)
            self.assertEqual(game["tree_task"]["progress"]["completed"], 1)
            for action in (lambda: self.tools.plant_sapling(1,63,1),
                           lambda: self.tools.grow_tree(), lambda: self.tools.apply_bone_meal(2,63,0)):
                with self.assertRaisesRegex(ValueError, "counted tree task"):
                    action()
            with self.assertRaises(ValueError):
                tool.invoke({"count": 3})
            second = tool.invoke({"count": 2})
            self.assertEqual(second["status"], "completed")
            self.assertEqual(tool.invoke({"count": 2})["progress"]["completed"], 2)
            self.assertEqual(planting.call_count, 2)
            task = dict(game["tree_task"].items())
            task["status"] = "active"
            game["tree_task"] = task
            agent = object.__new__(self.chat_class)
            agent.ctx_swarm = {"game": game}
            with redirect_stdout(io.StringIO()):
                agent.stop()
            self.assertEqual(game["tree_task"]["status"], "cancelled")
            self.assertEqual(game["tree_task"]["progress"]["completed"], 2)
            self.assertEqual(tool.invoke({"count": 2})["status"], "cancelled")

    def test_resume_directive_and_tool_preserve_saved_redis_goal_after_restart(self):
        from data_schema.event_convert import convert_to_chat_message
        from langchain_core.tools import StructuredTool
        from saruman.tree_tasks import new_task, task_config

        task = new_task(task_config(2, "grow", max_bone_meal=10))
        task.update(status="cancelled", steps=7, bone_meal_charged=3)
        task["targets"] = [
            {"position": [1170, 63, -481], "stage": "grown", "owned": False},
            {"position": [1176, 63, -481], "stage": "planned", "owned": False},
        ]
        with (self.redis_game() as game,
              patch.object(self.config, "ctx_swarm", {"game": game}),
              patch("saruman.tree_tasks.trees._game_busy", return_value=True)):
            game["tree_task"] = task
            game["tree_search"] = {"visited": [[1170, 63, -481]]}
            agent = object.__new__(self.chat_class)
            agent.ctx_swarm = {"game": game}
            agent.initialized = False
            agent.respond(convert_to_chat_message("Продолжи выращивание деревьев"))
            self.assertEqual(game["tree_task"]["status"], "paused")
            result = StructuredTool.from_function(self.tools.manage_trees).invoke(task["config"])
            self.assertEqual(result["status"], "waiting_for_game_task")
            self.assertEqual(result["progress"]["completed"], 1)
            self.assertEqual(result["task_id"], task["id"])
            self.assertEqual(game["tree_task"]["targets"], task["targets"])
            self.assertEqual(game["tree_task"]["steps"], 7)
            self.assertEqual(game["tree_task"]["bone_meal_charged"], 3)
            self.assertEqual(game["tree_search"]["visited"], [[1170, 63, -481]])
            agent.respond(convert_to_chat_message("Посади два дуба"))
            self.assertFalse(game["tree_task"])
            self.assertFalse(game["tree_search"])

    def test_self_initiated_counted_task_installs_continuation_without_directive(self):
        from data_schema.activity_tracker import ActivityTracker

        agent = object.__new__(self.chat_class)
        agent._cached_prefix = [SystemMessage(content="Test persona")]
        agent.ctx_swarm = {"game": {"hasActiveTask": False}}
        response = {"task_id": "self-chosen", "goal": "Plant 2 saplings", "status": "in_progress",
                    "progress": {"completed": 1, "requested": 2},
                    "next_action": {"tool": "manage_trees", "arguments": {"count": 2}}}
        agent.tool_bank = {"manage_trees": {"function": Mock(return_value=response), "disabled": False}}
        call = {"name": "manage_trees", "args": {"count": 2}, "id": "trees"}
        agent._stream_response = Mock(side_effect=[
            ("", [call], AIMessage(content="", tool_calls=[call]), False),
            ("Продолжу посадку. *neutral*", [], AIMessage(content="Continue"), False)])
        agent._send_as_speech = Mock()
        waiter = Mock()
        waiter.check.return_value = False
        with patch("saruman.procedures.time.monotonic", return_value=0), redirect_stdout(io.StringIO()):
            agent._respond_with_tools([HumanMessage(content="I choose to plant two trees")], waiter)
        self.assertTrue(agent._minecraft_procedure.batch)
        self.assertTrue(agent._minecraft_procedure.pending)
        agent.ctx_handler = Mock()
        agent.ctx_handler.activity_tracker = ActivityTracker()
        agent.ctx_handler.ctx_swarm = {"chat_queue": [], "tts_queue": []}
        agent.ctx_handler.get_ctx_chat.return_value = []
        agent._last_directive_ts = time.time_ns()
        agent.respond = Mock()
        with patch("saruman.procedures.time.monotonic", return_value=3), redirect_stdout(io.StringIO()):
            agent.step()
        agent.ctx_handler.wait_for_sync.assert_not_called()
        self.assertIn("Plant 2 saplings", agent.respond.call_args.args[0].msg)

    def test_counted_task_cannot_resume_before_accepted_collection_reaches_the_mod(self):
        agent = object.__new__(self.chat_class)
        agent._cached_prefix = [SystemMessage(content="Test persona")]
        agent.ctx_swarm = {"game": {"tree_task": {"status": "active", "id": "job"}}}
        request = {"item": "oak_sapling", "count": 2}
        manage = Mock(return_value={"task_id": "job", "goal": "Plant 2 saplings", "status": "needs_materials",
                                    "next_action": {"tool": "obtain_item", "arguments": request}})
        obtain = Mock(return_value="Queued obtaining/crafting oak_sapling")
        agent.tool_bank = {"manage_trees": {"function": manage, "disabled": False},
                           "obtain_item": {"function": obtain, "disabled": False}}
        first = {"name": "manage_trees", "args": {"count": 2}, "id": "plan"}
        queued = [{"name": "obtain_item", "args": request, "id": "collect"},
                  {"name": "manage_trees", "args": {"count": 2}, "id": "early"}]
        agent._stream_response = Mock(side_effect=[
            ("", [first], AIMessage(content="", tool_calls=[first]), False),
            ("", queued, AIMessage(content="", tool_calls=queued), False),
            ("Жду саженцы. *neutral*", [], AIMessage(content="Wait"), False)])
        agent._send_as_speech = Mock()
        waiter = Mock()
        waiter.check.return_value = False
        with redirect_stdout(io.StringIO()):
            agent._respond_with_tools([HumanMessage(content="Plant two trees")], waiter)
        manage.assert_called_once()
        obtain.assert_called_once()
        self.assertTrue(agent._minecraft_procedure.pending)
        self.assertFalse(agent._minecraft_procedure.batch_scheduled)

    def test_tree_material_collection_completion_wakes_agent_without_idle_wait(self):
        from data_schema.activity_tracker import ActivityTracker
        from saruman.procedures import ProcedureContinuation

        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = {"game": {"hasActiveTask": False}}
        agent.ctx_handler = Mock()
        agent.ctx_handler.activity_tracker = ActivityTracker()
        agent.ctx_handler.ctx_swarm = {"chat_queue": [], "tts_queue": []}
        agent.ctx_handler.get_ctx_chat.return_value = []
        agent._last_directive_ts = time.time_ns()
        agent._minecraft_procedure = ProcedureContinuation("Вырастить дерево")
        with patch("saruman.procedures.time.monotonic", return_value=0):
            agent._minecraft_procedure.observe("obtain_item", {}, "Queued obtaining/crafting oak_sapling")
        agent.respond = Mock()
        with patch("saruman.procedures.time.monotonic", return_value=3), redirect_stdout(io.StringIO()):
            agent.step()
        agent.ctx_handler.wait_for_sync.assert_not_called()
        event = agent.respond.call_args.args[0]
        self.assertEqual(event.type, "system")
        self.assertIn("Вырастить дерево", event.msg)
        self.assertIn("# grow_tree", agent._build_reminder(event))
        with redirect_stdout(io.StringIO()):
            agent.stop()
        self.assertIsNone(agent._minecraft_procedure)

    def test_procedure_blocks_second_material_task_in_same_model_response(self):
        from saruman.procedures import ProcedureContinuation

        agent = object.__new__(self.chat_class)
        agent._cached_prefix = [SystemMessage(content="Test persona")]
        agent._minecraft_procedure = ProcedureContinuation("Вырастить дерево")
        obtain = Mock(return_value="Queued obtaining/crafting oak_sapling")
        agent.tool_bank = {"obtain_item": {"function": obtain, "disabled": False}}
        calls = [
            {"name": "obtain_item", "args": {"item": "oak_sapling", "count": 1}, "id": "first"},
            {"name": "obtain_item", "args": {"item": "bone_meal", "count": 8}, "id": "second"},
        ]
        agent._stream_response = Mock(side_effect=[
            ("", calls, AIMessage(content="", tool_calls=calls), False),
            ("Жду сбор саженца. *neutral*", [], AIMessage(content="Waiting"), False),
        ])
        agent._send_as_speech = Mock()
        waiter = Mock()
        waiter.check.return_value = False
        with redirect_stdout(io.StringIO()):
            agent._respond_with_tools([HumanMessage(content="Вырастить дерево")], waiter)
        obtain.assert_called_once_with(item="oak_sapling", count=1)
        result_messages = [m.content for m in agent._cached_prefix if isinstance(m, ToolMessage)]
        self.assertIn("waiting_for_game_task", result_messages[-1])

    def test_new_directive_replaces_pending_procedure_and_stop_clears_it(self):
        from data_schema.event_convert import convert_to_chat_message
        from saruman.procedures import ProcedureContinuation

        agent = object.__new__(self.chat_class)
        old = ProcedureContinuation("Вырастить дерево")
        agent._minecraft_procedure = old
        agent.initialized = False  # Return before any model/DB work.
        agent.respond(convert_to_chat_message("Посади саженец берёзы"))
        self.assertIsNot(agent._minecraft_procedure, old)
        self.assertEqual(agent._minecraft_procedure.goal, "Посади саженец берёзы")
        agent.respond(convert_to_chat_message("Остановись и жди"))
        self.assertIsNone(agent._minecraft_procedure)

    def test_background_tools_execute_through_registry_without_llm_generation(self):
        from agent.tools.tools import ALL_TOOLS
        from saruman.autonomy import Autonomy
        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = {"game": {"ingame": True, "hasActiveTask": False}}
        agent._minecraft_procedure = None
        now = [0]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        agent._autonomy = Autonomy(clock=lambda: now[0], rng=Mock(), settings_path=Path(temporary.name) / "weights.json")
        agent._autonomy.enabled = True
        agent._autonomy.rng.choices.side_effect = lambda choices, **kw: [choices[0]]
        now[0] = 10
        agent._send_as_speech = Mock()
        agent.llm = Mock()
        funcs = (self.tools.get_inventory, self.tools.farm_crops, self.tools.hunt_hostiles, self.tools.attack_hostile, self.tools.explore_nearby)
        for function in funcs:
            self.assertIn(function, ALL_TOOLS)
        agent.tool_bank = {f.__name__: {"function": f, "disabled": False} for f in funcs}

        def plant(*args, **kwargs):
            state = agent.ctx_swarm["game"]["autonomy"]
            self.assertEqual(state["activity"], "farm")
            self.assertEqual(state["last_selection"]["status"], "selected")
            self.assertEqual(state["action"]["tool"], "farm_crops")
            return {"status": "planted"}

        with patch.object(self.tools, "read_player_inventory", return_value={"health": 20, "food": 20, "items": []}), patch.object(self.tools, "farm_step", side_effect=plant), redirect_stdout(io.StringIO()):
            self.assertTrue(agent._background_step())
        self.assertEqual(agent.ctx_swarm["game"]["autonomy"]["result"]["status"], "planted")
        agent._send_as_speech.assert_called_once()
        self.assertTrue(agent._send_as_speech.call_args.args[0].endswith("*neutral*"))
        agent._autonomy.activities = [a for a in agent._autonomy.activities if a["id"] == "hunt"]
        agent._autonomy.job = None
        now[0] = 13
        with patch.object(self.config, "ctx_swarm", agent.ctx_swarm), patch.object(self.tools, "read_player_inventory", return_value={"health": 20, "food": 20, "items": []}), patch.object(self.tools, "hunt_step", return_value={"status": "attacked"}), redirect_stdout(io.StringIO()):
            self.assertTrue(agent._background_step())
        self.assertTrue(agent._send_as_speech.call_args.args[0].endswith("*angry*"))
        now[0] = 16
        agent.ctx_swarm["game"]["hasActiveTask"] = True
        self.assertFalse(agent._background_step())
        self.assertEqual(agent.ctx_swarm["game"]["autonomy"]["phase"], "native_task")
        agent.llm.invoke.assert_not_called()

    def test_background_respects_disabled_registry_tool_and_recovers_after_enable(self):
        from saruman.autonomy import Autonomy

        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = {"game": {"ingame": True, "hasActiveTask": False}}
        agent._minecraft_procedure = None
        agent._send_as_speech = Mock()
        inventory = Mock(return_value={"health": 20, "food": 20, "items": []})
        hunt = Mock(return_value={"status": "no_target"})
        agent.tool_bank = {
            "get_inventory": {"function": inventory, "disabled": False},
            "hunt_hostiles": {"function": hunt, "disabled": True},
            "attack_hostile": {"function": Mock(), "disabled": False},
        }
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            now = [0]
            agent._autonomy = Autonomy(clock=lambda: now[0], settings_path=Path(directory) / "weights.json")
            agent._autonomy.enabled = True
            now[0] = 9
            self.assertTrue(agent._background_step())
            hunt.assert_not_called()
            self.assertFalse(agent._autonomy.paused)
            self.assertEqual(agent.ctx_swarm["game"]["autonomy"]["phase"], "unavailable")
            agent.tool_bank["hunt_hostiles"]["disabled"] = False
            now[0] = 11
            self.assertFalse(agent._background_step())
            now[0] = 13
            self.assertTrue(agent._background_step())
        hunt.assert_called_once_with()
        self.assertEqual(agent.ctx_swarm["game"]["autonomy"]["last_selection"]["id"], "hunt")

    def test_model_route_stop_ends_one_activity_without_pausing_autonomous_life(self):
        from saruman.autonomy import Autonomy

        agent = object.__new__(self.chat_class)
        agent.ctx_swarm = {"game": {"ingame": True, "hasActiveTask": False}}
        agent._cached_prefix = [SystemMessage(content="Test persona")]
        agent._minecraft_procedure = None
        agent._send_as_speech = Mock()
        agent.tool_bank = {"agent_action": {"function": Mock(return_value=True), "disabled": False}}
        call = {"name": "agent_action", "args": {"action": "@stop"}, "id": "stop-route"}
        final = "Этот путь закрыт; займусь другим делом. *neutral*"
        agent._stream_response = Mock(side_effect=[
            ("", [call], AIMessage(content="", tool_calls=[call]), False),
            (final, [], AIMessage(content=final), False),
        ])
        waiter = Mock()
        waiter.check.return_value = False
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            now = [0]
            controller = agent._autonomy = Autonomy(clock=lambda: now[0], settings_path=Path(directory) / "weights.json")
            controller.enabled = True
            now[0] = 9
            controller.rng = Mock()
            controller.rng.choices.side_effect = lambda choices, **kwargs: [choices[0]]
            controller.tick(agent.ctx_swarm["game"], lambda name, args: {"food": 20} if name == "get_inventory" else {"status": "planted"})
            self.assertEqual(controller.job["id"], "farm")
            agent._respond_with_tools([HumanMessage(content="Navigation failed")], waiter)
        self.assertFalse(controller.paused)
        self.assertIsNone(controller.job)
        self.assertEqual(controller.history[-1]["status"], "stopped")
        self.assertIn("farm", controller.cooldowns)

    def test_tree_instructions_and_new_fast_tools_are_registered(self):
        from agent.tools.tools import ALL_TOOLS
        from agent.tools.status_tools import get_langchain_tool
        from data_schema.event_convert import convert_to_chat_message

        for tool in (self.tools.get_minecraft_procedure, self.tools.find_tree_planting_spots,
                     self.tools.plant_sapling, self.tools.grow_tree):
            self.assertIn(tool, ALL_TOOLS)
            self.assertEqual(get_langchain_tool(tool).name, tool.__name__)
        agent = object.__new__(self.chat_class)
        reminder = agent._build_reminder(convert_to_chat_message("Вырастить дерево"))
        self.assertIn("# grow_tree", reminder)
        self.assertIn("по умолчанию 5×N", reminder)
        self.assertNotIn("# plant_sapling — только", reminder)

    def test_action_type_error_is_not_retried_after_possible_world_side_effects(self):
        from agent.multi.chat_agent import _execute_tool_call

        interaction = Mock(side_effect=TypeError("failure after click"))
        result = _execute_tool_call({"grow_tree": {"function": interaction, "disabled": False}},
                                    "grow_tree", {"sapling": "oak_sapling"})
        self.assertTrue(result.startswith("Error"))
        interaction.assert_called_once_with(sapling="oak_sapling")

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

    def test_model_factory_passes_low_reasoning_to_codex_turn(self):
        from agent.llm_clients.lc_clients import get_llm_chain

        with (
            patch.dict(os.environ, {"CORE_LLM_MODEL_NAME": "codex/gpt-6-sol", "CODEX_REASONING_EFFORT": "low"}),
            patch("saruman.codex_rpc.complete", return_value=({"content": "ok", "tool_calls": []}, {})) as complete,
        ):
            model = get_llm_chain()
            model.invoke([HumanMessage(content="fixture")])
        self.assertEqual(model.model, "gpt-6-sol")
        self.assertEqual(complete.call_args.kwargs["reasoning_effort"], "low")

    def test_full_game_supervisor_assembles_without_external_services(self):
        from agent.multiagent import MultiAgent
        from data_schema.structure_templates import CTX_SWARM_EMPTY

        context = dict(CTX_SWARM_EMPTY)
        handler = Mock()
        handler.ctx_swarm = context
        with (
            patch.dict(os.environ, {"EMBEDDINGS_MODEL": "NONE", "SARUMAN_AUTOMATION_MODE": "legacy"}),
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
        for name in ("grow_tree", "find_tree_planting_spots", "plant_sapling", "get_minecraft_procedure"):
            self.assertIn(name, agent.chat_agent._tool_names)
        self.assertFalse(agent.chat_agent._keepalive_enabled)
        self.assertIsNone(agent.core_agent.rag_model)


if __name__ == "__main__":
    unittest.main()
