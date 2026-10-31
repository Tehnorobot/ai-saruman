"""Offline role separation, raw-request integrity, provider protocols and Gradio."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gradio as gr
import httpx

from agent.multi.hybrid_agent import HybridAgent, route_request
from saruman.mod_goals import GoalStore
from saruman.persona_context import PersonaContext
from saruman.role_client import RoleClient, endpoint
from saruman.role_panel import (
    RolePanel,
    add_role_panel,
    model_changed,
    provider_changed,
)
from saruman.role_prompts import PERSONA_SCHEMA
from saruman.role_settings import (
    RoleStore,
    catalogue,
    default_profile,
    read_profiles,
    validate_profile,
)


def persona_output(chat="Слушаю.", voice="Слушаю тебя.", needs=False):
    return {
        "chat_text": chat,
        "voice_text": voice,
        "emotion": "neutral",
        "needs_action": needs,
    }


def action_output(name=None, args=None, decision="act", after_goal="finish"):
    return {
        "decision": decision,
        "motive": "Порядок мне по нраву",
        "detail": "",
        "after_goal": after_goal,
        "tool_calls": [{"name": name, "arguments": json.dumps(args or {})}]
        if name
        else [],
    }


def grow_trees(count: int = 1) -> dict:
    """Grow trees using native TreeGrow.

    Args:
        count: Requested tree count.
    """
    return {}


def agent_action(action: str) -> dict:
    """Submit one guarded native command.

    Args:
        action: Native command.
    """
    return {}


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(
            "os.environ",
            {
                "SARUMAN_ROLE_DB": str(Path(self.temp.name) / "roles.sqlite"),
                "SARUMAN_GOALS_DB": str(Path(self.temp.name) / "goals.sqlite"),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = RoleStore()


class SettingsTests(Fixtures):
    def test_ui_rejects_unconfigured_paid_provider_without_saving(self):
        panel = RolePanel(lambda: ({}, False), self.store)
        values = [
            "litellm",
            next(iter(catalogue()["litellm"])),
            "default",
            1024,
            60,
            100,
            100000,
        ]
        with patch.dict("os.environ", LITELLM_API_KEY="", LITELLM_BASE_URL=""):
            self.assertIn("LITELLM_API_KEY", panel.save("persona", *values))
        self.assertEqual(self.store.profiles()["persona"]["provider"], "codex")

    def test_saved_unconfigured_profile_is_reported_without_network_call(self):
        profile = {
            **default_profile(),
            "provider": "litellm",
            "model": next(iter(catalogue()["litellm"])),
            "reasoning": "default",
        }
        self.store.save("persona", profile, paused=True)
        with (
            patch.dict("os.environ", LITELLM_API_KEY=""),
            patch("saruman.role_client.httpx.Client") as client,
        ):
            summary = RolePanel(lambda: ({}, False), self.store).status()[0]
        self.assertIn("LITELLM_API_KEY", summary)
        client.assert_not_called()

    def test_doctor_can_read_defaults_without_creating_a_database(self):
        absent = Path(self.temp.name) / "absent.sqlite"
        self.assertEqual(set(read_profiles(absent)), {"action", "persona"})
        self.assertFalse(absent.exists())

    def test_recovery_does_not_mark_another_live_runtime_unknown(self):
        self.store.begin("action", "live", self.store.profiles()["action"], 100)
        with patch("saruman.role_settings.pid_alive", return_value=True):
            self.store.recover()
        self.assertEqual(self.store.history()[0]["state"], "running")
        with patch("saruman.role_settings.pid_alive", return_value=False):
            self.store.recover()
        self.assertEqual(self.store.history()[0]["state"], "unknown")

    def test_default_roles_use_existing_subscription_not_paid_provider(self):
        with patch.dict(
            "os.environ",
            CORE_LLM_MODEL_NAME="codex/gpt-6-sol",
            CODEX_REASONING_EFFORT="low",
        ):
            self.assertEqual(self.store.profiles()["persona"]["provider"], "codex")
            self.assertEqual(self.store.profiles()["action"]["model"], "gpt-6-sol")

    def test_independent_profiles_persist_and_validate_effort(self):
        profile = {
            **default_profile(),
            "provider": "litellm",
            "model": next(iter(catalogue()["litellm"])),
            "reasoning": "default",
        }
        self.store.save("persona", profile, paused=True)
        self.assertEqual(RoleStore().profiles()["persona"]["provider"], "litellm")
        self.assertEqual(RoleStore().profiles()["action"]["provider"], "codex")
        with self.assertRaises(ValueError):
            validate_profile({**profile, "reasoning": "high"})

    def test_active_or_inflight_requests_block_settings_mutation(self):
        profile = self.store.profiles()["action"]
        request = self.store.begin("action", "test", profile, 20)
        new = {k: v for k, v in profile.items() if k != "revision"}
        with self.assertRaises(ValueError):
            self.store.save("action", new, paused=True)
        self.store.finish(request, {"total_tokens": 10})
        with self.assertRaises(ValueError):
            self.store.save("action", new, paused=False)
        self.store.save("action", new, paused=True)

    def test_budget_reserves_unknown_usage_and_call_limit(self):
        profile = {
            **self.store.profiles()["action"],
            "token_budget": 1000,
            "max_calls": 2,
        }
        self.store.begin("action", "budget", profile, 600)
        with self.assertRaises(RuntimeError):
            self.store.begin("action", "budget", profile, 600)
        self.store.begin("action", "budget", profile, 100)
        with self.assertRaises(RuntimeError):
            self.store.begin("action", "budget", profile, 1)

    def test_unknown_config_fields_and_model_strings_rejected(self):
        for value in (
            {**default_profile(), "api_key": "must not be stored"},
            {**default_profile(), "model": "gpt-6-sol;command"},
        ):
            with self.assertRaises(ValueError):
                validate_profile(value)

    def test_ui_catalogue_dropdowns_do_not_call_a_model(self):
        with patch("saruman.codex_rpc.complete") as codex:
            with gr.Blocks() as app:
                panel = add_role_panel(lambda: ({}, False), self.store)
            panel.status()
            self.assertIn(
                "Настроить PersonaAgent",
                str([getattr(b, "label", "") for b in app.blocks.values()]),
            )
            self.assertEqual(
                provider_changed("litellm")[0].value, next(iter(catalogue()["litellm"]))
            )
            self.assertEqual(
                model_changed("litellm", next(iter(catalogue()["litellm"]))).choices,
                [("default", "default")],
            )
            codex.assert_not_called()

    def test_ui_save_is_role_scoped_and_denies_when_enabled(self):
        enabled = [True]
        panel = RolePanel(lambda: ({}, enabled[0]), self.store)
        values = panel.load("persona")
        self.assertIn("Stop", panel.save("persona", *values))
        enabled[0] = False
        self.assertIn("Сохранено", panel.save("persona", *values))
        self.assertEqual(self.store.profiles()["action"]["revision"], 0)


class PersonaContextTests(unittest.TestCase):
    def test_real_bank_uses_the_same_topics_as_the_selector(self):
        from saruman.action_speech import seed_records
        from saruman.persona_context import TOPICS

        topics = {
            r["event"].removeprefix("lore.")
            for r in seed_records()
            if r["kind"] == "authored_backstory"
        }
        self.assertEqual(topics, set(TOPICS))
        cards = PersonaContext().select(
            {"original_request": "Расскажи о своей ошибке"}
        )["suggestions"]
        teaching = {r["text"] for r in seed_records() if r["event"] == "lore.teaching"}
        self.assertTrue(set(cards) <= teaching)

    def test_relevant_fictional_memories_rotate_without_overriding_world_facts(self):
        records = [
            {
                "kind": "authored_backstory",
                "event": "lore.trees",
                "text": f"Garden memory {i}",
            }
            for i in range(4)
        ] + [
            {
                "kind": "authored_backstory",
                "event": "lore.books",
                "text": "Library memory",
            }
        ]
        context = PersonaContext(records)
        event = {
            "original_request": "Вырасти дерево",
            "facts": {"goals": [{"kind": "trees", "completed": 0}]},
        }
        first = context.select(event)
        second = context.select(event)
        self.assertTrue(all("Garden memory" in s for s in first["suggestions"]))
        self.assertTrue(set(first["suggestions"]).isdisjoint(second["suggestions"]))
        self.assertEqual(event["facts"]["goals"][0]["completed"], 0)
        self.assertIn("not Tolkien canon", first["boundary"])

    def test_disabled_and_empty_database_banks_do_not_reappear_as_local_memories(self):
        for records in [
            [],
            [
                {
                    "kind": "authored_backstory",
                    "enabled": False,
                    "text": "Secret disabled scene",
                }
            ],
        ]:
            self.assertEqual(PersonaContext(records).select({})["suggestions"], [])


class ProviderTests(Fixtures):
    def test_missing_key_and_invalid_endpoint_have_safe_actionable_errors(self):
        self.profile()
        for environment, expected in [
            ({"LITELLM_API_KEY": "", "LITELLM_BASE_URL": ""}, "LITELLM_API_KEY"),
            (
                {
                    "LITELLM_API_KEY": "private-value",
                    "LITELLM_BASE_URL": "https://user:private-value@example.test",
                },
                "LITELLM_BASE_URL",
            ),
        ]:
            with (
                patch.dict("os.environ", environment),
                patch("saruman.role_client.httpx.Client") as transport,
                self.assertRaisesRegex(RuntimeError, expected),
            ):
                RoleClient(self.store, "persona", "test").complete(
                    "test", {}, PERSONA_SCHEMA
                )
            transport.assert_not_called()
            reason = self.store.history()[0]["reason"]
            self.assertIn(expected, reason)
            self.assertNotIn("private-value", reason)

    def test_invalid_provider_output_preserves_known_paid_usage(self):
        self.profile()
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
            json={
                "choices": [{"message": {"content": "not structured"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 4,
                    "total_tokens": 104,
                },
            },
        )
        with (
            patch.dict(
                "os.environ",
                LITELLM_API_KEY="fake",
                LITELLM_BASE_URL="https://example.test/v1",
            ),
            patch("saruman.role_client.httpx.Client") as factory,
        ):
            factory.return_value.__enter__.return_value.post.return_value = response
            with self.assertRaises(RuntimeError):
                RoleClient(self.store, "persona", "test").complete(
                    "test", {}, PERSONA_SCHEMA
                )
        self.assertEqual(self.store.history()[0]["usage"]["total_tokens"], 104)

    def profile(self, provider="litellm"):
        profile = {
            **default_profile(),
            "provider": provider,
            "model": next(iter(catalogue()[provider])),
            "reasoning": "default",
        }
        self.store.save("persona", profile, paused=True)
        return self.store.profiles()["persona"]

    def test_http_endpoint_normalization_and_credential_free_errors(self):
        with patch.dict(
            "os.environ", LITELLM_BASE_URL="http://localhost:4000/v1/chat/completions"
        ):
            self.assertEqual(
                endpoint("litellm"), "http://localhost:4000/v1/chat/completions"
            )
        with patch.dict("os.environ", DSLAB_BASE_URL="https://api.dslab.tech/v1"):
            self.assertEqual(endpoint("dslab"), "https://api.dslab.tech/v1/messages")
        with (
            patch.dict(
                "os.environ", LITELLM_BASE_URL="https://user:password@example.test/v1"
            ),
            self.assertRaises(ValueError),
        ):
            endpoint("litellm")

    def test_litellm_native_tools_protocol_and_usage(self):
        profile = self.profile()
        payload = persona_output()
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "submit_role_result",
                                        "arguments": json.dumps(payload),
                                    }
                                }
                            ]
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )
        with (
            patch.dict(
                "os.environ",
                LITELLM_API_KEY="test-secret",
                LITELLM_BASE_URL="https://example.test/v1",
            ),
            patch("saruman.role_client.httpx.Client") as factory,
        ):
            post = factory.return_value.__enter__.return_value.post
            post.return_value = response
            value, usage = RoleClient._http(profile, "system", "raw", PERSONA_SCHEMA)
            body = post.call_args.kwargs["json"]
            self.assertNotIn("reasoning_effort", body)
            self.assertEqual(
                body["tool_choice"]["function"]["name"], "submit_role_result"
            )
            self.assertEqual(value, payload)
            self.assertEqual(usage["total_tokens"], 15)

    def test_dslab_uses_native_messages_and_effort(self):
        profile = self.profile("dslab")
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.dslab.tech/v1/messages"),
            json={
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "submit_role_result",
                        "input": persona_output(),
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 3,
                },
            },
        )
        with (
            patch.dict("os.environ", DSLAB_API_KEY="test-secret"),
            patch("saruman.role_client.httpx.Client") as factory,
        ):
            post = factory.return_value.__enter__.return_value.post
            post.return_value = response
            _, usage = RoleClient._http(profile, "system", "raw", PERSONA_SCHEMA)
            self.assertEqual(
                post.call_args.kwargs["json"]["output_config"], {"effort": "high"}
            )
            self.assertEqual(usage["total_tokens"], 22)

    def test_provider_error_blocks_automatic_retries_and_hides_secrets(self):
        self.profile()
        transport = Mock(side_effect=RuntimeError("test-secret credential"))
        client = RoleClient(self.store, "persona", "test", transport)
        for _ in range(2):
            with self.assertRaises(RuntimeError) as error:
                client.complete("test", {}, PERSONA_SCHEMA)
            self.assertNotIn("test-secret", str(error.exception))
        transport.assert_called_once()
        self.assertNotIn("test-secret", str(self.store.history()))
        self.assertEqual(self.store.history()[0]["state"], "error")

    def test_schema_rejects_extra_tool_proposals_in_persona_output(self):
        self.profile()
        client = RoleClient(
            self.store,
            "persona",
            "test",
            Mock(
                return_value=(
                    {**persona_output(), "tool_calls": [{"command": "@kill"}]},
                    {},
                )
            ),
        )
        with self.assertRaises(RuntimeError):
            client.complete("test", {}, PERSONA_SCHEMA)

    def test_codex_role_uses_its_profile_and_records_tokens(self):
        client = RoleClient(self.store, "persona", "test")
        with patch(
            "saruman.codex_rpc.complete",
            return_value=(
                persona_output(),
                {"usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12}},
            ),
        ) as codex:
            client.complete("persona", {}, PERSONA_SCHEMA)
        self.assertEqual(
            codex.call_args.args[3], self.store.profiles()["persona"]["model"]
        )
        self.assertEqual(self.store.history()[0]["usage"]["total_tokens"], 12)


class HybridTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.events = []
        self.ctx = {
            "env": {"agent_enabled": True, "actived": True},
            "game": {"automation_mode": "mods", "alive": True},
            "chat_queue": [],
            "tts_queue": [],
        }
        self.clients = {"action": Mock(), "persona": Mock()}
        self.agent = HybridAgent(
            self.ctx,
            Mock(get_ctx_chat=lambda **_: self.events),
            {
                "grow_trees": {"function": grow_trees},
                "agent_action": {"function": agent_action},
            },
            self.store,
            self.clients,
            persona_prompt="Saruman",
            clock=lambda: self.now,
        )
        self.agent.running = True
        self.agent.generation = 1
        self.addCleanup(self.agent.cleanup)

    def event(self, text, identifier=1, event_type="chat"):
        from data_schema.chat_structures import CtxEventBase

        return CtxEventBase(
            msg=text,
            user="Alex",
            date="",
            processing_timestamp=identifier,
            env="minecraft",
            type=event_type,
            additional_fields={"filter_results": {"acceptable": True}},
        )

    def submit_action(self, text="вырасти два дерева"):
        self.agent.submit(self.event(text))
        return self.agent.action_queue.get_nowait()

    def test_routing_is_conservative_and_raw_constraints_survive(self):
        self.assertEqual(route_request("привет"), "persona")
        self.assertEqual(route_request("расскажи о себе"), "persona")
        self.assertEqual(route_request("расскажи о себе и посади дерево"), "action")
        raw = "Посади 2 дуба, но костную муку не используй!"
        request = self.submit_action(raw)
        self.clients["action"].complete.return_value = action_output(decision="refused")
        self.agent.action.decide(request)
        self.assertEqual(
            self.clients["action"].complete.call_args.args[1]["original_request"], raw
        )

    def test_persona_has_no_executor_and_text_cannot_become_command(self):
        self.agent.submit(self.event("привет"))
        request = self.agent.requests["1"]
        self.assertFalse(hasattr(self.agent.persona, "execute"))
        for text in ("@goto 1 2 3", "/give @s diamond", "Привет\n!grow_trees 99"):
            self.assertFalse(self.agent.publish(request, persona_output(chat=text)))
        self.assertEqual(self.ctx["chat_queue"], [])
        self.assertEqual(GoalStore().list(), [])

    def test_action_cannot_call_chat_or_voice_or_unknown_tools(self):
        request = self.submit_action()
        self.clients["action"].complete.return_value = action_output(
            "chat", {"message": "fake"}
        )
        self.agent.action.execute = Mock()
        with self.assertRaises(ValueError):
            self.agent.action.decide(request)
        self.agent.action.execute.assert_not_called()

    def test_multiple_mutations_rejected_before_any_execution(self):
        request = self.submit_action()
        output = action_output("grow_trees", {"count": 2})
        output["tool_calls"] *= 2
        self.clients["action"].complete.return_value = output
        self.agent.action.execute = Mock()
        with self.assertRaises(ValueError):
            self.agent.action.decide(request)
        self.agent.action.execute.assert_not_called()

    def test_unknown_mutating_outcome_is_never_retried_automatically(self):
        request = self.submit_action()
        self.clients["action"].complete.return_value = action_output(
            "agent_action", {"action": "@get stone 1"}
        )
        self.agent.action.execute = Mock(return_value="Error after possible delivery")
        result = self.agent.action.decide(request)
        self.assertEqual(result["decision"], "unresolved")
        self.agent.action.execute.assert_called_once()
        self.clients["action"].complete.assert_called_once()

    def test_speech_worker_does_not_block_action_worker(self):
        blocked = threading.Event()
        release = threading.Event()
        action_done = threading.Event()

        def slow_persona(*_):
            blocked.set()
            release.wait(3)
            return persona_output()

        def fast_action(*_):
            action_done.set()
            return action_output(decision="refused")

        self.clients["persona"].complete.side_effect = slow_persona
        self.clients["action"].complete.side_effect = fast_action
        self.agent.start_workers()
        self.agent.submit(self.event("привет", 1))
        self.assertTrue(blocked.wait(1))
        self.agent.submit(self.event("вырасти дерево", 2))
        self.assertTrue(
            action_done.wait(1), "slow speech blocked independent action worker"
        )
        release.set()

    def test_slow_action_does_not_block_another_players_conversation(self):
        blocked = threading.Event()
        release = threading.Event()
        spoken = threading.Event()

        def slow_action(*_):
            blocked.set()
            release.wait(3)
            return action_output(decision="refused")

        def speech(*_):
            spoken.set()
            return persona_output()

        self.clients["action"].complete.side_effect = slow_action
        self.clients["persona"].complete.side_effect = speech
        self.agent.start_workers()
        self.agent.submit(self.event("вырасти дерево", 1))
        self.assertTrue(blocked.wait(1))
        self.agent.submit(self.event("привет", 2))
        self.assertTrue(spoken.wait(1))
        release.set()

    def test_stale_speech_is_discarded_after_new_result_or_stop(self):
        request = self.submit_action()
        self.agent.speak(request, "accepted", {})
        job = self.agent.persona_queue.get_nowait()
        self.agent.speak(request, "completed", {})
        self.agent.results.put(("persona", job, persona_output(), None))
        self.agent.handle_results()
        self.assertEqual(self.ctx["tts_queue"], [])
        self.agent.stop()
        self.agent.results.put(
            ("persona", self.agent.persona_queue.get_nowait(), persona_output(), None)
        )
        self.agent.handle_results()
        self.assertEqual(self.ctx["tts_queue"], [])

    def test_current_speech_can_take_longer_than_45_seconds_within_model_timeout(self):
        self.agent.submit(self.event("привет"))
        job = self.agent.persona_queue.get_nowait()
        self.now += 60
        self.agent.results.put(("persona", job, persona_output(), None))
        self.agent.handle_results()
        self.assertEqual(len(self.ctx["tts_queue"]), 1)

    def test_waiting_for_action_does_not_start_speculative_speech_request(self):
        self.submit_action()
        self.now += 5
        self.agent.step()
        self.assertTrue(self.agent.persona_queue.empty())
        self.clients["persona"].complete.assert_not_called()

    def test_speech_past_its_configured_deadline_is_discarded(self):
        self.agent.submit(self.event("привет"))
        job = self.agent.persona_queue.get_nowait()
        self.now = job["deadline"] + 1
        self.agent.results.put(("persona", job, persona_output(), None))
        self.agent.handle_results()
        self.assertEqual(self.ctx["tts_queue"], [])

    def test_escalation_forwards_original_once_without_publishing_promise(self):
        self.agent.submit(self.event("привет"))
        job = self.agent.persona_queue.get_nowait()
        self.agent.results.put(
            ("persona", job, persona_output(chat="Сейчас сделаю", needs=True), None)
        )
        self.agent.handle_results()
        self.assertEqual(self.agent.action_queue.get_nowait()["raw"], "привет")
        self.assertEqual(self.ctx["chat_queue"], [])

    def test_accepted_tree_executes_native_goal_and_completes_without_another_strong_call(
        self,
    ):
        from agent.tools import tools_config
        from agent.tools.mod_tools import grow_trees as real_grow

        self.agent.tools["grow_trees"]["function"] = real_grow
        request = self.submit_action()
        self.clients["action"].complete.return_value = action_output(
            "grow_trees", {"count": 2}
        )
        with patch.object(tools_config, "ctx_swarm", self.ctx):
            value = self.agent.action.decide(request)
        self.agent.results.put(("action", request, value, None))
        self.agent.handle_results()
        saved = self.agent.requests["1"]
        self.assertEqual(saved["state"], "waiting")
        goal = GoalStore().list()[0]
        self.assertEqual(goal["count"], 2)
        GoalStore().update(goal, state="completed", completed=2)
        self.agent.poll_goals()
        self.assertEqual(saved["state"], "finished")
        self.clients["action"].complete.assert_called_once()

    def test_compound_tree_then_command_returns_to_action_specialist(self):
        self.submit_action("вырасти два дерева, а потом подойди ко мне")
        goal = GoalStore().submit(
            "trees", source="player", request_id="1:trees", count=2
        )
        saved = self.agent.requests["1"]
        saved.update(state="waiting", goal_ids=[goal["id"]], after_goal="review")
        GoalStore().update(goal, state="completed", completed=2)
        self.agent.poll_goals()
        self.assertEqual(saved["state"], "deciding")
        next_request = self.agent.action_queue.get_nowait()
        self.assertEqual(
            next_request["raw"], "вырасти два дерева, а потом подойди ко мне"
        )
        self.assertEqual(next_request["observations"][0]["completed"], 2)

    def test_goal_completed_before_action_result_is_not_lost_or_reviewed_twice(self):
        request = self.submit_action()
        goal = GoalStore().submit(
            "trees", source="player", request_id="1:trees", count=2
        )
        GoalStore().update(goal, state="completed", completed=2)
        value = action_output(after_goal="review")
        self.agent.results.put(("action", request, value, None))
        self.agent.handle_results()
        self.agent.poll_goals()
        saved = self.agent.requests["1"]
        self.assertEqual(saved["state"], "deciding")
        self.assertEqual(saved["observations"][0]["completed"], 2)
        self.agent.results.put(
            ("action", saved.copy(), action_output(decision="completed"), None)
        )
        self.agent.handle_results()
        self.assertEqual(saved["state"], "finished")

    def test_failed_tree_cannot_be_restarted_under_changed_count(self):
        request = self.submit_action()
        goal = GoalStore().submit(
            "trees", source="player", request_id="1:trees", count=2
        )
        GoalStore().update(goal, state="failed")
        result = self.agent.execute(request, "grow_trees", {"count": 3}, "retry")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(len(GoalStore().list()), 1)

    def test_mutated_persona_text_does_not_enter_factual_memory(self):
        self.agent.submit(self.event("привет"))
        request = self.agent.requests["1"]
        self.agent.publish(request, persona_output(voice="Я посадил сто деревьев."))
        self.assertEqual(GoalStore().list(), [])
        self.agent.ctx_handler.add_message.assert_not_called()
        self.assertNotIn("сто деревьев", str(self.agent.world()))

    def test_own_game_chat_is_not_treated_as_a_player_goal(self):
        event = self.event("вырасти дерево")
        event.additional_fields["self"] = True
        self.agent.submit(event)
        self.assertEqual(self.agent.requests, {})

    def test_speech_context_omits_tool_definitions_and_private_history(self):
        request = self.submit_action()
        self.agent.speak(
            request,
            "accepted",
            {
                "tool_calls": [{"name": "agent_action", "arguments": "SECRET_CMD"}],
                "results": ["private"],
            },
        )
        job = self.agent.persona_queue.get_nowait()
        self.assertNotIn("SECRET_CMD", str(job))
        self.assertNotIn("results", job["facts"])

    def test_persona_receives_topical_memory_cues_without_a_second_model_call(self):
        self.agent.submit(self.event("расскажи о себе"))
        job = self.agent.persona_queue.get_nowait()
        self.clients["persona"].complete.return_value = persona_output()
        self.agent.persona.generate(job)
        payload = self.clients["persona"].complete.call_args.args[1]
        self.assertTrue(payload["personal_memory_cues"]["suggestions"])
        self.clients["persona"].complete.assert_called_once()
        self.clients["action"].complete.assert_not_called()

    def test_quiet_world_can_trigger_only_one_speech_job_without_game_goal(self):
        self.ctx["game"]["mod_runtime"] = {
            "status": "running",
            "in_world": True,
            "world": "world1",
            "defense_busy": False,
        }
        self.now += 181
        self.agent.maybe_remark()
        job = self.agent.persona_queue.get_nowait()
        self.assertEqual(job["phase"], "personal_remark")
        self.assertEqual(self.agent.requests[job["request_id"]]["source"], "ambient")
        self.agent.maybe_remark()
        self.assertTrue(self.agent.persona_queue.empty())
        self.assertTrue(self.agent.action_queue.empty())
        self.assertEqual(GoalStore().list(), [])

    def test_ambient_reply_cannot_escalate_into_actions(self):
        self.ctx["game"]["mod_runtime"] = {
            "status": "running",
            "in_world": True,
            "world": "world1",
        }
        self.now += 181
        self.agent.maybe_remark()
        job = self.agent.persona_queue.get_nowait()
        self.agent.results.put(("persona", job, persona_output(needs=True), None))
        self.agent.handle_results()
        self.assertTrue(self.agent.action_queue.empty())
        self.assertEqual(self.ctx["tts_queue"], [])
        self.assertEqual(GoalStore().list(), [])

    def test_ambient_initiative_waits_for_safety_and_player_requests(self):
        self.ctx["game"]["mod_runtime"] = {
            "status": "running",
            "in_world": True,
            "defense_busy": True,
        }
        self.now += 181
        self.agent.maybe_remark()
        self.assertTrue(self.agent.persona_queue.empty())
        self.ctx["game"]["mod_runtime"]["defense_busy"] = False
        self.submit_action()
        self.agent.maybe_remark()
        self.assertTrue(self.agent.persona_queue.empty())

    def test_new_player_message_discards_ambient_reply_already_being_generated(self):
        self.ctx["game"]["mod_runtime"] = {
            "status": "running",
            "in_world": True,
            "world": "world1",
        }
        self.now += 181
        self.agent.maybe_remark()
        job = self.agent.persona_queue.get_nowait()
        self.now += 1
        self.agent.submit(self.event("привет"))
        self.agent.results.put(("persona", job, persona_output(), None))
        self.agent.handle_results()
        self.assertEqual(self.ctx["tts_queue"], [])

    def test_operator_can_disable_ambient_generation_without_disabling_replies(self):
        self.ctx["game"]["mod_runtime"] = {"status": "running", "in_world": True}
        self.now += 181
        with patch.dict("os.environ", SARUMAN_PERSONA_INITIATIVE_ENABLED="false"):
            self.agent.maybe_remark()
        self.assertTrue(self.agent.persona_queue.empty())
        self.agent.submit(self.event("привет"))
        self.assertEqual(self.agent.persona_queue.get_nowait()["phase"], "conversation")

    def test_mod_supervisor_has_only_two_roles_and_no_legacy_model_loop(self):
        from agent.multiagent import MultiAgent

        with (
            patch.dict("os.environ", SARUMAN_AUTOMATION_MODE="mods"),
            patch(
                "agent.multiagent.tools_config.init_tools_module",
                return_value=self.agent.tools,
            ),
            patch(
                "agent.multiagent.PlannerAgent",
                side_effect=AssertionError("legacy planner"),
            ),
            patch(
                "agent.multiagent.CoreAgent", side_effect=AssertionError("legacy core")
            ),
            patch(
                "agent.multiagent.get_llm_chain",
                side_effect=AssertionError("legacy model"),
            ),
        ):
            supervisor = MultiAgent(
                self.ctx,
                ctx_handler=self.agent.ctx_handler,
                ctx_host=Mock(),
                use_chat_agent=True,
            )
        self.assertIsInstance(supervisor.hybrid_agent, HybridAgent)
        self.assertFalse(hasattr(supervisor, "chat_agent"))
        self.assertFalse(hasattr(supervisor, "planner_agent"))
        supervisor.cleanup()

    def test_world_change_discards_delayed_persona_reply(self):
        self.ctx["game"]["mod_runtime"] = {"world": "old"}
        self.agent.submit(self.event("привет"))
        job = self.agent.persona_queue.get_nowait()
        self.ctx["game"]["mod_runtime"] = {"world": "new"}
        self.agent.results.put(("persona", job, persona_output(), None))
        self.agent.handle_results()
        self.assertEqual(self.ctx["tts_queue"], [])

    def test_long_native_goal_still_gets_a_final_reply(self):
        self.submit_action()
        goal = GoalStore().submit(
            "trees", source="player", request_id="1:trees", count=2
        )
        saved = self.agent.requests["1"]
        saved.update(state="waiting", goal_ids=[goal["id"]], after_goal="finish")
        self.now += 3600
        GoalStore().update(goal, state="completed", completed=2)
        self.agent.poll_goals()
        self.assertTrue(self.agent.active(saved))
        self.assertEqual(self.agent.persona_queue.get_nowait()["phase"], "completed")

    def test_real_gradio_save_callback_needs_no_event_payload(self):
        import asyncio

        from gradio.state_holder import SessionState

        with gr.Blocks() as app:
            add_role_panel(lambda: ({}, False), self.store)
        callbacks = [
            (i, f) for i, f in app.fns.items() if f.fn and f.fn.__name__ == "save_role"
        ]
        self.assertEqual(len(callbacks), 2)
        values = [
            "litellm",
            next(iter(catalogue()["litellm"])),
            "default",
            1024,
            60,
            100,
            100000,
        ]
        provider_callback = next(
            i
            for i, f in app.fns.items()
            if f.fn and f.fn.__name__ == "provider_changed"
        )

        async def apply():
            state = SessionState(app)
            await app.process_api(provider_callback, ["litellm"], state=state)
            return await app.process_api(callbacks[0][0], values, state=state)

        with patch.dict(
            "os.environ",
            LITELLM_API_KEY="test-only",
            LITELLM_BASE_URL="https://example.test/v1",
        ):
            result = asyncio.run(apply())
        self.assertIn("Сохранено", result["data"][0])
        self.assertEqual(self.store.profiles()["persona"]["provider"], "litellm")
        self.assertEqual(self.store.profiles()["action"]["provider"], "codex")


if __name__ == "__main__":
    unittest.main()
