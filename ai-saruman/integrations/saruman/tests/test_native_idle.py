"""Native idle, preemption and failure recovery; no LLM calls or live world writes."""
import tempfile
import sqlite3
import unittest
from pathlib import Path
from time import time_ns
from unittest.mock import Mock, patch

from saruman.mod_goals import GoalStore
from saruman.mod_runtime import AutomationRuntime
from tests.test_mod_runtime import FakeMods


class NativeIdleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GoalStore(Path(self.temp.name) / "goals.sqlite")
        self.mods = FakeMods()
        self.mods.snapshot.update(players=[], player_alive=True, command={})
        self.now = 1000
        self.game = {"automation_mode": "mods", "alive": True}
        self.rng = Mock()
        self.rng.choice.side_effect = lambda values: values[-1]
        self.runtime = AutomationRuntime(self.mods, self.store, clock=lambda: self.now, rng=self.rng)
        env = patch.dict("os.environ", SARUMAN_AUTONOMY_ENABLED="true")
        env.start(); self.addCleanup(env.stop)
        weights = patch("saruman.mod_runtime.load_weights", return_value={"trees": 1})
        weights.start(); self.addCleanup(weights.stop)

    def tick(self, seconds=1, enabled=True, tools=None):
        self.now += seconds
        self.runtime.tick(self.game, enabled=enabled, tools=tools)

    def goal(self, identifier):
        return next(g for g in self.store.list() if g["id"] == identifier)

    def test_idle_waits_twenty_seconds_then_selects_tree_without_player(self):
        self.tick()
        self.tick(19)
        self.assertEqual(self.store.list(), [])
        self.tick()
        self.assertEqual([g["kind"] for g in self.store.list()], ["trees"])
        self.tick()
        self.assertEqual(self.store.list()[0]["state"], "running")
        self.tick(30)
        self.assertEqual(len(self.store.list()), 1)

    def test_random_choice_is_between_activities_then_between_players(self):
        self.mods.snapshot["players"] = ["Alex", "Steve", "bad;name"]
        self.tick(); self.tick(20)
        goal = self.store.list()[0]
        self.assertEqual((goal["kind"], goal["target"], goal["command"]), ("follow", "Steve", "@follow Steve"))
        self.assertEqual(self.rng.choice.call_args_list[0].args[0], ["trees", "follow"])
        self.assertEqual(self.rng.choice.call_args_list[1].args[0], ["Alex", "Steve"])

    def test_follow_leaves_on_player_input_and_stops_only_its_run(self):
        self.mods.snapshot["players"] = ["Alex"]
        self.tick(); self.tick(20); self.tick()
        goal = self.store.list()[0]
        self.game["mod_player_pending_until"] = self.now+30
        self.tick()
        self.assertEqual(self.goal(goal["id"])["state"], "cancelled")
        stopped = [a["run_id"] for op, a in self.mods.calls if op == "stop_command"]
        self.assertEqual(stopped, [goal["run_id"]])

    def test_follow_missing_player_ends_and_does_not_block_tree_request(self):
        self.mods.snapshot["players"] = ["Alex"]
        self.tick(); self.tick(20); self.tick()
        follow = self.store.list()[0]
        self.mods.snapshot["players"] = []
        self.tick()
        self.assertEqual(self.goal(follow["id"])["state"], "failed")
        player = self.store.submit("trees", source="player")
        self.tick()
        self.assertEqual(self.goal(player["id"])["state"], "running")

    def test_follow_resumes_after_defense_and_player_can_cancel_the_resumed_run(self):
        self.mods.snapshot["players"] = ["Alex"]
        self.tick(); self.tick(20); self.tick()
        goal = self.store.list()[0]
        self.mods.snapshot["defense_busy"] = True
        self.tick()
        self.mods.snapshot.update(defense_busy=False, native_busy=False)
        self.tick()
        resumed = self.goal(goal["id"])
        self.assertEqual(resumed["state"], "running")
        self.assertNotEqual(resumed["native_command_run"], resumed["run_id"])
        self.game["mod_player_pending_until"] = self.now+30
        self.tick()
        stopped = [a["run_id"] for op, a in self.mods.calls if op == "stop_command"]
        self.assertEqual(stopped, [resumed["native_command_run"]])

    def test_lost_follow_reply_is_reconciled_using_the_same_receipt(self):
        self.mods.snapshot["players"] = ["Alex"]
        self.tick(); self.tick(20)
        self.mods.lose_next = True
        self.tick()
        goal = self.store.list()[0]
        self.assertEqual(goal["state"], "starting")
        self.tick()
        self.assertEqual(self.goal(goal["id"])["state"], "running")
        ids = [a["request_id"] for op, a in self.mods.calls if op == "command"]
        self.assertEqual(len(set(ids)), 1)

    def test_player_text_and_voice_have_the_same_native_priority(self):
        from agent.multi.chat_agent import ChatAgent
        from data_schema.chat_structures import CtxEventBase
        from saruman.player_priority import PlayerInbox
        for kind in ("chat", "voice"):
            with self.subTest(kind=kind):
                event = CtxEventBase(processing_timestamp=time_ns(), date="2026-11-12T00:00:00",
                                     env="minecraft", user="Alex", type=kind, msg="Пойдём за мной")
                agent = ChatAgent.__new__(ChatAgent)
                agent.ctx_swarm = {"game": self.game}
                agent.ctx_handler = Mock()
                agent.ctx_handler.get_ctx_chat.return_value = [event]
                agent._player_inbox = PlayerInbox()
                agent._background_step()
                self.assertGreater(self.game["mod_player_pending_until"], 0)
                agent._begin_player_turn(event)
                self.assertTrue(self.game["mod_player_context"]["replying"])
                agent._end_player_turn()
                self.assertFalse(self.game["mod_player_context"]["replying"])

    def test_transcribed_minecraft_voice_marks_pending_before_model_response(self):
        from data_collectors.stt.speech_processor import VoiceInputProcessor
        processor = VoiceInputProcessor.__new__(VoiceInputProcessor)
        processor.ctx_swarm = {"game": self.game}
        processor.ctx_handler = Mock()
        processor._on_transcript("Alex", "minecraft", "Посади дерево", 0, 1)
        self.assertGreater(self.game["mod_player_pending_until"], 0)
        processor.ctx_handler.add_message.assert_called_once()

    def test_new_player_request_after_death_reset_starts_after_respawn(self):
        first = self.store.submit("trees", source="player")
        self.tick()
        self.mods.snapshot.update(stop_kind="reset", manual_stop=True, manual_stop_latched=True,
                                  defense_enabled=False, native_busy=False, player_alive=False)
        self.game["alive"] = False
        self.tick()
        self.assertEqual(self.goal(first["id"])["state"], "failed")
        second = self.store.submit("trees", source="player")
        self.mods.snapshot["player_alive"] = True
        self.game["alive"] = True
        self.tick()
        self.assertEqual(self.goal(second["id"])["state"], "running")

    def test_failure_does_not_delay_new_player_request(self):
        self.tick(); self.tick(20); self.tick()
        self.mods.finish(False); self.tick()
        self.assertEqual(self.store.list()[0]["state"], "failed")
        goal = self.store.submit("trees", source="player")
        self.tick()
        self.assertEqual(self.goal(goal["id"])["state"], "running")

    def test_failure_switches_activity_when_alternative_exists_without_extra_pause(self):
        self.tick(); self.tick(20); self.tick()
        self.mods.finish(False); self.tick()
        self.mods.snapshot["players"] = ["Alex"]
        self.tick(20)
        background = self.store.list(active=True)
        self.assertEqual([g["kind"] for g in background], ["follow"])

    def test_success_and_failure_restart_the_idle_clock_not_the_task(self):
        self.tick(); self.tick(20); self.tick()
        self.mods.finish(); self.tick()
        self.tick(19)
        self.assertEqual(len(self.store.list()), 1)
        self.tick()
        self.assertEqual(len(self.store.list()), 2)

    def test_dead_time_does_not_count_as_idle_and_reset_does_not_latch_respawn(self):
        self.mods.snapshot.update(player_alive=False, stop_kind="reset", manual_stop=True, manual_stop_latched=True, defense_enabled=False)
        self.game["alive"] = False
        self.tick(); self.tick(50)
        self.assertEqual(self.store.list(), [])
        self.mods.snapshot["player_alive"] = True
        self.game["alive"] = True
        self.tick()
        self.assertTrue(any(op == "defense" and a["enabled"] for op, a in self.mods.calls))
        self.assertEqual(self.game["mod_runtime"]["status"], "running")
        self.assertEqual(self.store.list(), [])

    def test_explicit_stop_does_not_automatically_resume_or_pick_hobbies(self):
        self.tick()
        self.mods.calls.clear()
        self.mods.snapshot.update(stop_kind="manual", manual_stop=True, manual_stop_latched=True, defense_enabled=False)
        self.tick(60); self.tick(60)
        self.assertEqual(self.game["mod_runtime"]["status"], "paused")
        self.assertFalse(any(op == "defense" and a["enabled"] for op, a in self.mods.calls))
        self.assertEqual(self.store.list(), [])

    def test_conversation_and_combat_restart_idle_wait(self):
        self.tick(); self.tick(19)
        self.game["mod_player_context"] = {"replying": True, "until": self.now+60}
        self.tick(30)
        self.assertEqual(self.store.list(), [])
        self.game["mod_player_context"] = {}
        self.mods.snapshot["defense_busy"] = True
        self.tick(30)
        self.mods.snapshot["defense_busy"] = False
        self.tick(); self.tick(19)
        self.assertEqual(self.store.list(), [])
        self.tick()
        self.assertEqual(len(self.store.list()), 1)

    def test_lost_api_does_not_block_after_recovery(self):
        self.tick()
        original = self.mods.call
        self.mods.call = Mock(side_effect=TimeoutError("read interrupted"))
        self.tick(30)
        self.assertEqual(self.game["mod_runtime"]["status"], "unavailable")
        self.mods.call = original
        player = self.store.submit("trees", source="player")
        self.tick()
        self.assertEqual(self.goal(player["id"])["state"], "running")

    def test_temporary_goal_database_contention_does_not_kill_the_dispatcher(self):
        with self.store.connection() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        goal = self.store.submit("trees", source="player")
        with patch.object(self.store, "lease", side_effect=sqlite3.OperationalError("database is locked")):
            self.tick()
        self.assertEqual(self.game["mod_runtime"]["status"], "unavailable")
        self.tick()
        self.assertEqual(self.goal(goal["id"])["state"], "running")

    def test_failed_goal_history_read_is_reported_and_later_recovers(self):
        with patch.object(self.store, "list", side_effect=sqlite3.OperationalError("database is locked")):
            self.tick()
        self.assertEqual(self.game["mod_runtime"]["status"], "unavailable")
        self.tick()
        self.assertEqual(self.game["mod_runtime"]["status"], "running")

    def test_disabled_tools_and_disabled_schedule_do_not_start_world_actions(self):
        self.tick(tools={}); self.tick(25, tools={})
        self.assertEqual(self.store.list(), [])
        with patch.dict("os.environ", SARUMAN_AUTONOMY_ENABLED="false"):
            self.tick(30)
        self.assertEqual(self.store.list(), [])

    def test_terminal_command_keeps_progress_but_allows_next_request(self):
        first = self.store.submit("command", source="player", command="@goto 1 2 3")
        self.tick()
        run = self.goal(first["id"])["run_id"]
        self.mods.snapshot["command"] = {"run_id": run, "finished": True, "current": True}
        self.tick()
        self.assertEqual(self.goal(first["id"])["state"], "completed")
        self.assertIn("requires observation", self.goal(first["id"])["reason"])

    def test_stopped_command_is_not_success_and_does_not_keep_the_queue(self):
        first = self.store.submit("command", source="player", command="@goto 1 2 3")
        self.tick()
        run = self.goal(first["id"])["run_id"]
        self.mods.snapshot["command"] = {"run_id": run, "finished": False, "stopped": True}
        self.mods.snapshot["native_busy"] = False
        self.tick()
        self.assertEqual(self.goal(first["id"])["state"], "failed")
        second = self.store.submit("trees", source="player")
        self.tick()
        self.assertEqual(self.goal(second["id"])["state"], "running")


if __name__ == "__main__":
    unittest.main()
