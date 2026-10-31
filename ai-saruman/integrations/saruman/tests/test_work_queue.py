"""Background intent queue, expanding actual walks and player priority without a world."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from data_schema.chat_structures import CtxEventBase
from saruman.autonomy import Autonomy
from saruman.autonomy_settings import load_weights, save_weights
from saruman.player_priority import PlayerInbox, is_player_message


def event(timestamp, user="Alex", kind="chat", **extra):
    return CtxEventBase(
        msg="Help me",
        user=user,
        date="",
        processing_timestamp=timestamp,
        type=kind,
        env="minecraft",
        additional_fields=extra,
    )


class PlayerInboxTests(unittest.TestCase):
    def test_fifo_player_requests_are_not_lost_or_replayed(self):
        inbox = PlayerInbox(since=10)
        events = [event(30, "Bob"), event(20), event(5)]
        inbox.collect(events)
        inbox.collect(events)
        self.assertEqual(inbox.next().user, "Alex")
        self.assertEqual(inbox.next().user, "Bob")
        self.assertIsNone(inbox.next())
        inbox.collect(events)
        self.assertIsNone(inbox.next())

    def test_server_own_filtered_and_non_game_events_are_not_player_priority(self):
        for value in [
            event(1, ""),
            event(2, self=True),
            event(3, filter_results={"acceptable": False}),
            event(4, kind="death"),
        ]:
            self.assertFalse(is_player_message(value))
        other = event(5)
        other.env = "system"
        self.assertFalse(is_player_message(other))
        self.assertTrue(is_player_message(event(6, kind="voice", filter_results=None)))

    def test_old_three_activity_weights_keep_values_and_explicit_off_stays_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weights.json"
            for weights in (
                {"farm": 6, "hunt": 5, "explore": 5},
                {"farm": 0, "hunt": 0, "explore": 0},
            ):
                path.write_text(json.dumps({"weights": weights}), encoding="utf-8")
                before = path.read_bytes()
                result = load_weights(path)
                self.assertEqual({k: result[k] for k in weights}, weights)
                self.assertEqual(result["trees"], 2 if any(weights.values()) else 0)
                self.assertEqual(before, path.read_bytes())


class WorkQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "weights.json"
        save_weights({"farm": 1, "hunt": 1, "explore": 0, "trees": 0}, self.path)
        self.now = 9
        self.controller = Autonomy(
            clock=lambda: self.now, rng=Mock(), settings_path=self.path
        )
        self.controller.rng.choices.side_effect = lambda choices, **kwargs: [choices[0]]
        self.controller.due = 0
        self.game = {
            "ingame": True,
            "alive": True,
            "hasActiveTask": False,
            "position": "0,63,0",
        }
        self.available = {
            "get_inventory",
            "farm_crops",
            "hunt_hostiles",
            "attack_hostile",
            "explore_nearby",
            "agent_action",
            "obtain_item",
        }
        self.answers = {
            "get_inventory": {"food": 20, "items": []},
            "farm_crops": {"status": "no_work", "reason_code": "no_water"},
            "hunt_hostiles": {"status": "no_target"},
            "agent_action": True,
            "explore_nearby": {
                "status": "needs_move",
                "next_action": {
                    "tool": "agent_action",
                    "arguments": {"action": "@goto 4 63 0"},
                },
            },
        }
        self.execute = Mock(side_effect=lambda name, args: self.answers.get(name))

    def tick(self, seconds=0.2):
        self.now += seconds
        return self.controller.tick(
            self.game, self.execute, available_tools=self.available
        )

    def test_failed_work_queues_search_while_the_next_ready_job_runs_immediately(self):
        self.tick()
        queued = self.controller.snapshot(self.game)["queue"]
        self.assertEqual(
            [(row["id"], row["kind"]) for row in queued],
            [("hunt", "activity"), ("farm", "search")],
        )
        self.tick()
        self.assertEqual(self.execute.call_args.args[0], "hunt_hostiles")
        self.tick()
        self.assertEqual(
            self.execute.call_args.args, ("agent_action", {"action": "@goto 4 63 0"})
        )
        self.assertEqual(self.controller.job["kind"], "search")

    def test_a_real_move_rescans_work_instead_of_waiting_out_the_old_cooldown(self):
        self.tick()
        self.tick()
        self.tick()
        self.game["hasActiveTask"] = True
        self.assertIsNone(self.tick(3))
        self.game.update(hasActiveTask=False, position="4,63,0")
        self.assertEqual(self.tick(3)["result"]["status"], "new_area")
        self.answers["farm_crops"] = {"status": "planted"}
        outcome = self.tick()
        self.assertEqual(outcome["result"]["status"], "planted")

    def test_search_budgets_expand_by_actual_stages_up_to_sixty_four(self):
        save_weights({"farm": 1, "hunt": 0, "explore": 0, "trees": 0}, self.path)
        self.answers["explore_nearby"] = {
            "status": "no_work",
            "reason_code": "search_budget_exhausted",
        }
        for _ in range(14):
            self.tick()
        limits = [
            call.args[1]["search_steps"]
            for call in self.execute.call_args_list
            if call.args[0] == "explore_nearby"
        ]
        self.assertEqual(limits, [4, 8, 16, 32, 64])
        self.assertLessEqual(len(self.controller.pending), 4)

    def test_unconfirmed_movement_is_not_repeated(self):
        self.tick()
        self.tick()
        self.tick()
        result = self.tick(3)
        self.assertEqual(result["result"]["status"], "movement_unconfirmed")
        self.assertEqual(
            sum(call.args[0] == "agent_action" for call in self.execute.call_args_list),
            1,
        )

    def test_player_priority_keeps_intent_and_blocks_even_saved_background_goals(self):
        self.answers["farm_crops"] = {"status": "planted"}
        self.tick()
        self.assertTrue(self.controller.yield_to_player("Alex", "Come here"))
        self.assertEqual(self.controller.pending[0]["id"], "farm")
        self.game["tree_task"] = {"status": "active", "config": {"count": 2}}
        self.execute.reset_mock()
        self.tick(20)
        self.execute.assert_not_called()
        self.controller.release_player()
        self.tick(4)
        self.execute.assert_not_called()

    def test_search_never_ignores_disabled_movement_tools_or_zero_weights(self):
        self.available.remove("agent_action")
        self.tick()
        self.assertFalse(
            any(row.get("kind") == "search" for row in self.controller.pending)
        )

    def test_slow_native_hobby_is_stopped_once_before_the_next_queued_job(self):
        self.answers["farm_crops"] = {
            "status": "needs_materials",
            "next_action": {
                "tool": "obtain_item",
                "arguments": {"item": "wooden_hoe", "count": 1},
            },
        }
        self.answers["obtain_item"] = "Queued obtaining/crafting wooden_hoe"
        self.tick()
        self.game["hasActiveTask"] = True
        self.tick(46)
        self.assertEqual(
            self.execute.call_args.args, ("agent_action", {"action": "@stop"})
        )
        self.tick(5)
        self.assertEqual(
            sum(c.args[0] == "agent_action" for c in self.execute.call_args_list), 1
        )
        self.game.update(hasActiveTask=False, navigation={"status": "stopped"})
        self.tick(2)
        self.assertEqual(self.execute.call_args.args[0], "hunt_hostiles")
        save_weights({"farm": 0, "hunt": 0, "explore": 0, "trees": 0}, self.path)
        self.execute.reset_mock()
        self.tick()
        self.assertFalse(
            any(
                call.args[0] in ("farm_crops", "explore_nearby")
                for call in self.execute.call_args_list
            )
        )
