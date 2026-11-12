"""Offline integration: capture -> persona draft -> durable queue -> real HTTP gateway."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

folder = Path(__file__).resolve().parent
if "_saruman_telegram" not in sys.modules:
    spec = importlib.util.spec_from_file_location("_saruman_telegram", folder / "__init__.py",
                                                submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from _saruman_telegram.automation import Control, Director, Store, Tunnel, record_goal
from _saruman_telegram.gateway import Publisher, Server, Settings, TelegramError
from _saruman_telegram.client import call

KEY = "test-key-" + "a" * 40


class Clock:
    def __init__(self):
        self.now = 1000

    def __call__(self):
        return self.now


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.store = Store(Path(self.temp.name) / "local.sqlite3", self.clock)

    def event(self, id="event-1"):
        self.store.event(id, "tree_grown", {"fact": "One tree grew", "world_scope": "private-world"})

    def active(self):
        self.store.set_meta("runtime", {"active": True, "world_ready": True, "updated": self.clock()})

    def director(self, draft=None):
        return Director(draft or (lambda p: {"text": "Я вырастил дерево.",
                                             "event_ids": [p["events"][0]["id"]]}), self.store)


class AdminPublicationTests(Fixture):
    def test_repeated_and_concurrent_commands_queue_a_draft_only_once(self):
        identifier=self.store.manual('Existing draft')
        barrier=threading.Barrier(2);results=[];errors=[]
        def run(request_id):
            try:
                barrier.wait(timeout=3)
                results.append(self.store.queue_for_admin(request_id))
            except Exception as error:errors.append(error)
        workers=[threading.Thread(target=run,args=(str(i),)) for i in range(2)]
        for thread in workers:thread.start()
        for thread in workers:thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertEqual(errors,[])
        self.assertEqual([r['post_id'] for r in results],[identifier,identifier])
        self.assertEqual([r['status'] for r in results],['queued','queued'])
        attempts=self.store.snapshot()['attempts']
        self.assertEqual(len(attempts),1)
        self.assertEqual(attempts[0]['stage'],'admin_queue')

    def test_unknown_and_failed_delivery_need_explicit_resolution(self):
        identifier=self.store.manual('Not confirmed')
        for status in ('failed','unknown'):
            with self.store.db() as db:db.execute('UPDATE posts SET status=? WHERE id=?',(status,identifier))
            self.assertEqual(self.store.queue_for_admin('request')['status'],status)
        self.assertEqual(self.store.queue_for_admin('explicit',identifier)['status'],'unknown')
        with self.store.db() as db:db.execute("UPDATE posts SET status='failed' WHERE id=?",(identifier,))
        self.assertEqual(self.store.queue_for_admin('retry',identifier)['status'],'queued')

    def test_only_existing_draft_is_used_and_no_post_is_generated(self):
        self.assertEqual(self.store.queue_for_admin('empty')['status'],'no_draft')
        self.assertEqual(self.store.snapshot()['posts'],[])
        self.store.manual('A');self.store.manual('B')
        self.assertEqual(self.store.queue_for_admin('ambiguous')['status'],'needs_input')
        self.assertEqual(self.store.snapshot()['counts'],{'draft':2})

class ManagedDraftTests(Fixture):
    def managed(self, draft=None):
        self.store.configure({"enabled":True,"auto_post":True,"interval_seconds":120})
        self.owner()
        return Director(draft or (lambda p:{"text":"Размышление Сарумана.","event_ids":[p["events"][0]["id"]]}),self.store,managed=True)

    def owner(self,active=True,epoch=1):
        self.store.set_meta("character_runtime",{"active":active,"updated":self.clock(),"owner":"lab","epoch":epoch})

    def test_without_minecraft_first_start_waits_for_schedule(self):
        director=self.managed()
        director.step()
        self.assertEqual(self.store.snapshot()["posts"],[])
        due=self.store.meta("next_draft")
        self.assertEqual(due,self.clock()+120)
        self.clock.now=due;self.owner()
        director.step()
        post=self.store.snapshot()["posts"][0]
        self.assertEqual(post["status"],"queued")
        event=self.store.snapshot()["events"][0]
        self.assertEqual(event["kind"],"scheduled_reflection")
        self.assertNotIn("tree_grown",str(event))

    def test_saving_unchanged_settings_preserves_schedule(self):
        director=self.managed();director.step()
        due=self.store.meta("next_draft")
        self.clock.now+=10
        self.store.configure(self.store.settings())
        self.assertEqual(self.store.meta("next_draft"),due)

    def test_stop_and_start_do_not_reset_schedule_or_duplicate_draft(self):
        director=self.managed()
        director.step();due=self.store.meta("next_draft")
        self.owner(False,2);director.step()
        self.owner(True,3);director.step()
        self.assertEqual(self.store.meta("next_draft"),due)
        self.clock.now=due;self.owner(True,3)
        director.step();director.step()
        self.assertEqual(len(self.store.snapshot()["posts"]),1)

    def test_explicit_interval_edit_changes_due_date_without_a_restart(self):
        director=self.managed();director.step()
        self.clock.now+=10
        self.store.configure({"interval_seconds":30})
        self.assertEqual(self.store.meta("next_draft"),self.clock()+30)
        self.owner();director.step()
        self.assertEqual(self.store.snapshot()["posts"],[])

    def test_pending_review_keeps_full_events_without_more_model_calls(self):
        draft=Mock(return_value={"text":"Мысль.","event_ids":["event-1"]})
        director=self.managed(draft)
        self.store.configure({"auto_post":False})
        self.event();self.store.set_meta("next_draft",0)
        director.step()
        self.clock.now+=121;self.owner()
        director.step()
        self.assertEqual(draft.call_count,1)
        self.assertEqual(self.store.snapshot()["posts"][0]["status"],"draft")

    def test_stop_start_during_generation_cannot_autopublish_stale_result(self):
        def draft(payload):
            self.owner(False,2);self.owner(True,3)
            return {"text":"Мысль.","event_ids":[payload["events"][0]["id"]]}
        director=self.managed(draft)
        self.event();self.store.set_meta("next_draft",0)
        director.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"],"draft")

    def test_expired_owner_blocks_model_and_delivery(self):
        director=self.managed(Mock(side_effect=AssertionError("stale owner")))
        self.event();self.store.set_meta("next_draft",0)
        self.clock.now+=16
        director.step()
        control=Control(self.store,managed=True)
        with patch.object(control,"config",side_effect=AssertionError("delivery after shutdown")):
            control.step()
        self.assertEqual(self.store.snapshot()["posts"],[])

    def test_legacy_game_cannot_compete_with_lab_owner(self):
        self.managed();self.active();self.event()
        director=self.director(Mock(side_effect=AssertionError("duplicate generator")))
        director.step()
        self.assertEqual(self.store.snapshot()["posts"],[])


class DraftTests(Fixture):
    def test_disabled_does_not_call_model(self):
        self.active()
        self.event()
        with patch.object(self.director(), "draft", side_effect=AssertionError()) as draft:
            director = self.director(draft)
            director.step()
            draft.assert_not_called()

    def test_draft_retains_sources_and_excludes_private_context(self):
        self.active()
        self.event()
        self.store.configure({"enabled": True})
        def generate(payload):
            self.assertNotIn("private-world", str(payload))
            self.assertEqual(payload["events"][0]["facts"], {"fact": "One tree grew"})
            return {"text": "Я вырастил дерево.", "event_ids": ["event-1"]}
        self.director(generate).step()
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["posts"][0]["status"], "draft")
        self.assertEqual(snapshot["posts"][0]["event_ids"], ["event-1"])
        self.assertEqual(snapshot["pending_events"], 0)
        self.assertEqual(snapshot["attempts"][0]["result"]["text"], "Я вырастил дерево.")

    def test_fabricated_reference_preserves_events_for_retry(self):
        self.active()
        self.event()
        self.store.configure({"enabled": True, "auto_post": True})
        with self.assertRaises(ValueError):
            self.director(lambda p: {"text": "Story", "event_ids": ["invented"]}).step()
        self.assertEqual(self.store.snapshot()["pending_events"], 1)
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "cancelled")

    def test_pause_during_generation_keeps_draft_without_sending(self):
        self.active()
        self.event()
        self.store.configure({"enabled": True, "auto_post": True})
        def generate(payload):
            self.store.configure({"enabled": False})
            return {"text": "Story", "event_ids": ["event-1"]}
        self.director(generate).step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "draft")

    def test_interval_and_partial_coverage_do_not_lose_memory(self):
        self.active()
        self.event("one")
        self.event("two")
        self.store.configure({"enabled": True, "auto_post": True, "interval_seconds": 120})
        director = self.director()
        director.step()
        self.assertEqual(self.store.snapshot()["pending_events"], 1)
        self.assertEqual(self.store.snapshot()["event_count"], 2)
        with self.store.db() as db:
            db.execute("UPDATE posts SET status='sent'")
        director.step()
        self.assertEqual(len(self.store.snapshot()["posts"]), 1)
        self.clock.now += 121
        self.active()
        director.step()
        self.assertEqual(self.store.snapshot()["pending_events"], 0)

    def test_old_runtime_and_pending_review_prevent_extra_model_calls(self):
        self.event()
        self.store.configure({"enabled": True})
        self.store.set_meta("runtime", {"active": True, "world_ready": True, "updated": 1})
        director = self.director()
        with patch.object(director, "draft") as draft:
            director.step()
            draft.assert_not_called()
        self.active()
        director.step()
        self.clock.now += 4000
        self.active()
        with patch.object(director, "draft") as draft:
            director.step()
            draft.assert_not_called()

    def test_full_history_is_paginated_not_deleted(self):
        for i in range(42):
            self.event(str(i))
        first, second = self.store.snapshot(), self.store.snapshot(1)
        self.assertEqual(first["event_count"], 42)
        self.assertEqual(len(first["events"]) + len(second["events"]), 42)


class ObservationTests(Fixture):
    def test_per_tree_completion_is_captured_before_run_id_is_reset(self):
        old = {"kind": "trees", "world": "world", "run_id": "run", "completed": 0}
        result = {**old, "completed": 1, "run_id": None}
        record_goal(old, result, self.store)
        director = self.director()
        director.observe(self.game(), active=False)
        self.assertEqual(self.store.snapshot()["event_count"], 1)
        self.assertFalse(self.store.meta("runtime")["active"])

    def game(self, success=True, finished=True, world="world", busy=False):
        return {"alive": True, "position": [1, 2, 3], "nearby_players": ["PrivatePlayer"],
                "mod_runtime": {"status": "running", "in_world": True, "updated": self.clock(), "world": world,
                                "defense_busy": busy, "tree": {"run_id": "run", "isFinished": finished, "success": success}}}

    def test_only_confirmed_growth_and_dedup_across_restart(self):
        director = self.director()
        director.observe(self.game(finished=False))
        self.assertEqual(self.store.snapshot()["event_count"], 0)
        self.clock.now += 3
        director.observe(self.game())
        self.clock.now += 3
        self.director().observe(self.game())
        events = self.store.snapshot()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "tree_grown")
        self.assertNotIn("PrivatePlayer", str(events))

    def test_failure_is_not_success(self):
        self.director().observe(self.game(success=False))
        self.assertEqual(self.store.snapshot()["events"][0]["kind"], "tree_failed")

    def test_stale_world_and_world_change_do_not_invent_battle_outcome(self):
        director = self.director()
        game = self.game(finished=False)
        game["mod_runtime"]["updated"] = 1
        director.observe(game)
        self.assertEqual(self.store.snapshot()["event_count"], 0)
        self.clock.now += 3
        director.observe(self.game(finished=False, busy=True))
        self.clock.now += 3
        director.observe(self.game(finished=False, world="new-world"))
        self.assertEqual(self.store.snapshot()["event_count"], 0)


class FakeTelegram:
    def __init__(self):
        self.sends = 0
        self.failure = None

    def call(self, method, payload):
        if method == "sendMessage":
            self.sends += 1
            if self.failure:
                raise self.failure
            return {"message_id": 25}
        if method == "getUpdates":
            return [{"update_id": 10, "channel_post": {"chat": {"id": -100123456}, "text": "Channel post"}},
                    {"update_id": 11, "message": {"chat": {"id": 99}, "text": "private"}}]
        return {"getMe": {"id": 123, "username": "bot"},
                "getChat": {"id": -100123456, "type": "channel", "title": "Test"},
                "getChatMember": {"status": "administrator", "can_post_messages": True}}[method]


class EndToEndTests(Fixture):
    def setUp(self):
        super().setUp()
        self.telegram = FakeTelegram()
        self.publisher = Publisher(Settings("123:" + "b" * 30, "-100123456", KEY,
                                            Path(self.temp.name) / "gateway.sqlite3"), self.telegram)
        self.server = Server(("127.0.0.1", 0), self.publisher)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.env = Path(self.temp.name) / ".env.client"
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.env.write_text(f"GATEWAY_URL={self.url}\nGATEWAY_API_KEY={KEY}\n", encoding="utf-8")
        self.control = Control(self.store, self.env)
        self.store.configure({"enabled": True})

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def queue(self):
        identifier = self.store.manual("Проверка очереди")
        self.store.action(identifier, "queue")
        return identifier

    def test_full_pipeline_and_idempotency_after_restart(self):
        self.active()
        self.event()
        self.store.configure({"auto_post": True})
        self.director().step()
        self.control.step()
        post = self.store.snapshot()["posts"][0]
        self.assertEqual((post["status"], post["message_id"]), ("sent", 25))
        # Simulate response lost before the local commit, then a new process.
        with self.store.db() as db:
            db.execute("UPDATE posts SET status='sending',next_try=0")
        Control(self.store, self.env).step()
        self.assertEqual(self.telegram.sends, 1)
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "sent")

    def test_admin_queue_repeated_before_and_after_delivery_sends_once(self):
        identifier=self.store.manual('One explicit publication')
        self.assertEqual(self.store.queue_for_admin('first')['post_id'],identifier)
        self.assertEqual(self.store.queue_for_admin('repeat')['post_id'],identifier)
        self.control.step()
        self.assertEqual(self.store.snapshot()['posts'][0]['status'],'sent')
        self.assertEqual(self.store.queue_for_admin('explicit-again',identifier)['status'],'already_sent')
        self.assertEqual(self.store.queue_for_admin('general-again')['status'],'no_draft')
        self.control.step()
        self.assertEqual(self.telegram.sends,1)

    def test_unreachable_gateway_retries_same_id(self):
        identifier = self.queue()
        self.control.transport = lambda *args: (0, {"error": "gateway_unreachable"})
        self.control.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "queued")
        self.clock.now += 10
        self.control.transport = call
        self.control.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["id"], identifier)
        self.assertEqual(self.telegram.sends, 1)

    def test_unknown_delivery_is_never_replayed(self):
        self.telegram.failure = TelegramError("connection_error", uncertain=True)
        self.queue()
        self.control.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "unknown")
        self.clock.now += 100
        self.control.step()
        self.assertEqual(self.telegram.sends, 1)

    def test_explicit_failure_can_be_retried_without_changing_id(self):
        self.telegram.failure = TelegramError("telegram_http_403")
        identifier = self.queue()
        self.control.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "failed")
        self.telegram.failure = None
        self.store.action(identifier, "queue")
        self.control.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "sent")
        self.assertEqual(self.telegram.sends, 2)

    def test_rate_limit_waits_then_retries_same_id(self):
        self.telegram.failure = TelegramError("telegram_http_429", retry_after=30)
        identifier = self.queue()
        self.control.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "queued")
        self.clock.now += 10
        self.control.step()
        self.assertEqual(self.telegram.sends, 1)
        with self.publisher.connect() as db:
            db.execute("UPDATE publications SET updated_at='2020-01-01T00:00:00+00:00' WHERE publication_id=?", (identifier,))
        self.telegram.failure = None
        self.clock.now += 30
        self.control.step()
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "sent")
        self.assertEqual(self.store.snapshot()["posts"][0]["id"], identifier)

    def test_pause_during_status_lookup_does_not_start_send(self):
        self.queue()
        def pause(*args):
            self.store.configure({"enabled": False})
            return 404, {"error": "not_found"}
        self.control.transport = pause
        self.control.step()
        self.assertEqual(self.telegram.sends, 0)
        self.assertEqual(self.store.snapshot()["posts"][0]["status"], "queued")

    def test_large_channel_history_response_is_not_truncated(self):
        old = self.telegram.call
        def updates(method, payload):
            if method == "getUpdates":
                return [{"update_id": i, "channel_post": {"chat": {"id": -100123456}, "text": "я" * 4096}}
                        for i in range(100)]
            return old(method, payload)
        self.telegram.call = updates
        self.control.step()
        self.assertEqual(self.store.meta("update_offset"), 100)
        self.assertEqual(len(self.store.snapshot()["inbox"]), 30)

    def test_updates_filter_private_messages_and_persist_offset(self):
        self.control.step()
        self.control.step()
        inbox = self.store.snapshot()["inbox"]
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["update_id"], 10)
        self.assertNotIn("private", str(inbox))
        self.assertEqual(self.store.meta("update_offset"), 12)

    def test_disabled_delivery_has_no_network_side_effect(self):
        self.queue()
        self.store.configure({"enabled": False})
        with patch.object(self.control, "transport", side_effect=AssertionError()) as transport:
            self.control.step()
            transport.assert_not_called()

    def test_lease_prevents_another_sender(self):
        self.queue()
        with self.store.lease("delivery"):
            self.control.step()
        self.assertEqual(self.telegram.sends, 0)
        self.control.step()
        self.assertEqual(self.telegram.sends, 1)

    def test_retry_api_cannot_resend_a_success_or_unknown(self):
        code, first = call(self.url, KEY, "POST", "/v1/posts", {"publication_id": "fixed", "text": "Hi"})
        self.assertEqual(code, 200)
        self.assertEqual(call(self.url, KEY, "POST", "/v1/posts/fixed/retry", {})[1]["message_id"], first["message_id"])
        self.assertEqual(self.telegram.sends, 1)


class TunnelTests(unittest.TestCase):
    def test_setup_preserves_api_key_and_quotes_remote_input(self):
        from setup_tunnel import remote_script, update_config
        script = remote_script("ssh-ed25519 AAAA comment'quoted")
        self.assertIn('permitopen=', script)
        self.assertIn('command=', script)
        with self.assertRaises(ValueError):
            remote_script("ssh-ed25519 AAAA\nmalicious command")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.client"
            path.write_text("GATEWAY_API_KEY=keep-this\nGATEWAY_SSH_USER=root\n", encoding="utf-8")
            update_config(path, {"GATEWAY_SSH_USER": "telegram-tunnel"})
            self.assertEqual(path.read_text().count("GATEWAY_API_KEY=keep-this"), 1)
            self.assertNotIn("USER=root", path.read_text())

    def test_close_prevents_a_late_spawn(self):
        tunnel = Tunnel()
        tunnel.close(permanent=True)
        with patch("_saruman_telegram.automation.subprocess.Popen") as process:
            self.assertEqual(tunnel.ensure({})["status"], "stopped")
            process.assert_not_called()

    def test_does_not_kill_an_existing_external_listener(self):
        tunnel = Tunnel()
        with patch("_saruman_telegram.automation.socket.create_connection") as connected, \
                patch("_saruman_telegram.automation.subprocess.Popen") as process:
            self.assertEqual(tunnel.ensure({"GATEWAY_URL": "http://127.0.0.1:8787"})["status"], "external")
            tunnel.close()
            process.assert_not_called()
            connected.assert_called_once()


if __name__ == "__main__":
    unittest.main()
