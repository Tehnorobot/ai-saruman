import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from saruman.memory import Workplace
from saruman.provider import CodexProvider, require_subscription
from saruman.storage import Store
from saruman.web import Application, make_server

PROJECT = Path(__file__).resolve().parents[1]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="saruman-test-")
        self.root = Path(self.temporary.name)
        target = self.root / "agent-workplace/agent/saruman"
        target.parent.mkdir(parents=True)
        shutil.copytree(PROJECT / "agent-workplace/agent/saruman", target)
        self.workplace = Workplace(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def fact(self, entity_id="alex", related=None):
        return {"id": entity_id, "name": entity_id.title(), "kind": "player",
                "fact": "Introduced themselves in the test chat.", "related_ids": related or []}


class MemoryTests(Fixture):
    def test_context_and_read_only_inspection(self):
        before = self.workplace.read("PERSONA.md")
        instructions, prompt, metrics = self.workplace.context([], "Здравствуйте", False)
        self.assertIn("Саруман", instructions)
        self.assertFalse(json.loads(prompt)["memory_enabled"])
        self.assertGreater(metrics["memory_input_est"], 0)
        self.assertEqual(len(metrics["persona_sha256"]), 64)
        self.assertEqual(self.workplace.read("PERSONA.md"), before)

    def test_facts_update_graph_with_reciprocal_links(self):
        facts = [self.fact("alex", ["boris"]), self.fact("boris")]
        self.assertEqual(self.workplace.remember(facts, "session", "run"), 2)
        snapshot = self.workplace.snapshot()
        entities = {item["id"]: item for item in snapshot["entities"]}
        self.assertIn("boris", entities["alex"]["links"])
        self.assertIn("alex", entities["boris"]["links"])
        self.assertIn("saruman", entities["alex"]["links"])
        self.assertIn("source: character lab", entities["alex"]["body"])
        self.assertIn("run run", entities["alex"]["body"])
        self.assertIn("entities/alex.md", self.workplace.read("memory/MEMORY.md"))

    def test_entire_batch_validated_before_write(self):
        bad = self.fact("../../escape")
        with self.assertRaises(ValueError):
            self.workplace.remember([self.fact(), bad], "s", "r")
        self.assertFalse((self.workplace.home / "memory/entities/alex.md").exists())

    def test_cannot_replace_self_or_read_outside_workplace(self):
        with self.assertRaises(ValueError):
            self.workplace.remember([self.fact("saruman")], "s", "r")
        with self.assertRaises(ValueError):
            self.workplace.read("../../../../etc/passwd")

    def test_symlink_entity_cannot_escape_workplace(self):
        outside = self.root / "outside.md"
        outside.write_text("untouched")
        target = self.workplace.home / "memory/entities/alex.md"
        target.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.workplace.remember([self.fact()], "s", "r")
        self.assertEqual(outside.read_text(), "untouched")

    def test_unknown_game_state_is_not_filled_with_demo_facts(self):
        snapshot = self.workplace.snapshot()
        self.assertEqual(snapshot["entities"], [])
        self.assertEqual(snapshot["edges"], [])
        self.assertIn("Unknown", self.workplace.read("memory/state.md"))


class FakeProvider:
    def status(self):
        return {"connected": True, "plan": "test", "limits": None,
                "models": [{"id": "test-model", "name": "Test model", "is_default": True}]}

    def complete(self, instructions, prompt, model, trace):
        trace.update({"model": model, "latency_ms": 12, "context_window": 1000,
                      "usage": {"inputTokens": 100, "cachedInputTokens": 20,
                                "outputTokens": 40, "reasoningOutputTokens": 10, "totalTokens": 140}})
        return {"reply": "Говори, смертный.", "memories": [
            {"id": "alex", "name": "Алекс", "kind": "player",
             "fact": "Introduced themselves as Алекс.", "related_ids": []}]}


class ApplicationTests(Fixture):
    def make_app(self, provider=None):
        return Application(self.root, provider=provider or FakeProvider())

    def test_dialogue_persists_without_modifying_memory_when_disabled(self):
        app = self.make_app()
        session = app.store.create_session()
        result = app.chat({"session_id": session["id"], "model": "test-model",
                           "text": "Я Алекс", "remember": False})
        self.assertEqual(result["remembered"], 0)
        self.assertEqual([row["role"] for row in app.store.messages(session["id"])], ["user", "assistant"])
        self.assertEqual(app.workplace.snapshot()["entities"], [])
        self.assertEqual(Store(app.store.path).sessions()[0]["title"], "Я Алекс")
        analytics = app.store.analytics()
        self.assertEqual(analytics["totals"]["total_tokens"], 140)
        self.assertEqual(analytics["totals"]["output_tokens"], 40)
        self.assertIsNone(analytics["money"])

    def test_enabled_memory_is_saved(self):
        app = self.make_app()
        session = app.store.create_session()
        result = app.chat({"session_id": session["id"], "model": "test-model",
                           "text": "Я Алекс", "remember": True})
        self.assertEqual(result["remembered"], 1)
        self.assertEqual(app.workplace.snapshot()["entities"][0]["name"], "Алекс")

    def test_failed_call_keeps_error_and_observed_usage(self):
        class Failed(FakeProvider):
            def complete(self, *args):
                args[-1].update({"usage": {"inputTokens": 8, "totalTokens": 8}})
                raise RuntimeError("Quota reached")
        app = self.make_app(Failed())
        session = app.store.create_session()
        with self.assertRaisesRegex(RuntimeError, "Quota"):
            app.chat({"session_id": session["id"], "model": "test-model", "text": "Привет"})
        run = app.store.runs()[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["total_tokens"], 8)
        self.assertEqual(len(app.store.messages(session["id"])), 1)
        self.assertIsNone(app.store.analytics()["totals"]["output_tokens"])
        self.assertEqual(app.store.analytics()["daily"], [])

    def test_memory_failure_does_not_discard_successful_model_reply(self):
        app = self.make_app()
        session = app.store.create_session()
        with patch.object(app.workplace, "remember", side_effect=OSError("read-only")):
            result = app.chat({"session_id": session["id"], "model": "test-model",
                               "text": "Я Алекс", "remember": True})
        self.assertIn("память не сохранена", result["warning"])
        self.assertEqual(app.store.runs()[0]["status"], "completed")
        self.assertEqual(app.store.messages(session["id"])[-1]["role"], "assistant")

    def test_unknown_usage_stays_null(self):
        class NoUsage(FakeProvider):
            def complete(self, *args):
                return {"reply": "Привет.", "memories": []}
        app = self.make_app(NoUsage())
        session = app.store.create_session()
        app.chat({"session_id": session["id"], "model": "test-model", "text": "Привет"})
        self.assertIsNone(app.store.runs()[0]["total_tokens"])
        self.assertIsNone(app.store.analytics()["totals"]["total_tokens"])

    def test_single_active_turn_and_invalid_input(self):
        app = self.make_app()
        session = app.store.create_session()
        app.turn_lock.acquire()
        try:
            with self.assertRaises(BlockingIOError):
                app.chat({"session_id": session["id"], "model": "test-model", "text": "Привет"})
        finally:
            app.turn_lock.release()
        with self.assertRaises(ValueError):
            app.chat({"session_id": session["id"], "model": "test-model", "text": ""})
        self.assertEqual(app.store.runs(), [])

    def test_table_names_are_not_arbitrary_sql(self):
        app = self.make_app()
        with self.assertRaises(ValueError):
            app.store.table("sessions; DROP TABLE runs")
        self.assertEqual(app.store.table("runs")["total"], 0)


class ProviderTests(unittest.TestCase):
    def test_api_key_mode_is_not_used_for_subscription_chat(self):
        for account in (None, {"type": "apiKey"}, {"type": "amazonBedrock"}):
            with self.assertRaises(RuntimeError):
                require_subscription(account)
        require_subscription({"type": "chatgpt"})

    def test_usage_notifications_are_not_added_twice(self):
        class FakeRpc:
            def __enter__(self):
                self.events = iter([
                    {"method": "thread/tokenUsage/updated", "params": {"threadId": "t",
                     "tokenUsage": {"total": {"inputTokens": 10, "totalTokens": 12}}}},
                    {"method": "thread/tokenUsage/updated", "params": {"threadId": "t",
                     "tokenUsage": {"total": {"inputTokens": 10, "totalTokens": 15},
                                    "modelContextWindow": 1000}}},
                    {"method": "item/completed", "params": {"threadId": "t",
                     "item": {"id": "a", "type": "agentMessage",
                              "text": json.dumps({"reply": "Ответ", "memories": []})}}},
                    {"method": "turn/completed", "params": {"threadId": "t", "turn": {"status": "completed"}}}
                ])
                return self
            def __exit__(self, *_):
                pass
            def request(self, method, params):
                if method == "account/read":
                    return {"account": {"type": "chatgpt"}}
                if method == "thread/start":
                    assert params["sandbox"] == "read-only"
                    assert params["environments"] == []
                    return {"thread": {"id": "t"}, "model": "test-model"}
                return {}
            def notification(self, _):
                return next(self.events)
        trace = {"model": "test-model"}
        with patch("saruman.provider.Rpc", FakeRpc):
            result = CodexProvider().complete("persona", "prompt", "test-model", trace)
        self.assertEqual(result["reply"], "Ответ")
        self.assertEqual(trace["usage"]["totalTokens"], 15)
        self.assertEqual(trace["context_window"], 1000)


class HttpTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app = Application(self.root, provider=FakeProvider())
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def test_assets_and_real_overview(self):
        for asset in ("/", "/app.js", "/styles.css"):
            with urlopen(self.base + asset) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("Content-Security-Policy", response.headers)
        with urlopen(self.base + "/api/overview") as response:
            result = json.load(response)
        self.assertEqual(result["sessions"], [])
        self.assertEqual(result["memory"]["entities"], [])
        self.assertIn("csrf_token", result)

    def test_mutations_require_origin_and_session_token(self):
        payload = b"{}"
        for headers in ({}, {"X-Saruman-Token": self.app.token, "Origin": "https://example.com"}):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.base + "/api/sessions", data=payload, headers=headers))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        request = Request(self.base + "/api/sessions", data=payload,
                          headers={"X-Saruman-Token": self.app.token})
        with urlopen(request) as response:
            session = json.load(response)
        self.assertIn("id", session)

    def test_no_arbitrary_file_read(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/../../pyproject.toml")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
