"""Command preflight precedes work; ordinary sword acquisition stays bounded."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from saruman.autonomy import Autonomy
from saruman.autonomy_settings import save_weights


class EquipmentSchedulingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "settings.json"
        save_weights({"farm": 1, "hunt": 0, "explore": 0, "trees": 0}, path)
        self.now = 100
        self.controller = Autonomy(clock=lambda: self.now, settings_path=path)
        self.controller.enabled, self.controller.due = True, 0
        self.game = {
            "ingame": True,
            "alive": True,
            "position": "0,63,0",
            "hasActiveTask": False,
        }
        self.available = {
            "ensure_scepter",
            "obtain_item",
            "agent_action",
            "get_inventory",
            "farm_crops",
        }
        self.supply = {"status": "ready"}
        self.execute = Mock(side_effect=self.answer)
        patcher = patch("saruman.autonomy.time.time", side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, tool, arguments):
        if tool == "ensure_scepter":
            return dict(self.supply)
        if tool == "get_inventory":
            return {"health": 20, "food": 20, "items": []}
        if tool == "farm_crops":
            return {"status": "planted"}
        if tool == "obtain_item":
            return {"status": "queued"}
        return True

    def tick(self, seconds=0):
        self.now += seconds
        return self.controller.tick(
            self.game, self.execute, available_tools=self.available
        )

    def start_materials(self):
        self.supply = {
            "status": "needs_sword",
            "next_action": {
                "tool": "obtain_item",
                "arguments": {"item": "wooden_sword", "count": 1},
            },
        }
        self.tick()
        self.game.update(
            hasActiveTask=True,
            native_user_task={"id": 7, "text": "Collect wooden_sword"},
            native_user_task_seen_at=self.now + 3,
            collection={"status": "running", "goal": {"item": "wooden_sword"}},
        )
        self.tick(3)

    def test_pending_give_xp_and_missing_blank_do_not_block_actual_farming(self):
        for status in (
            "give_pending",
            "xp_pending",
            "xp_unconfirmed",
            "needs_materials",
            "blocked",
        ):
            with self.subTest(status=status):
                self.controller.job = None
                self.controller.equipment_due = self.controller.due = 0
                self.controller.cooldowns = {}
                self.supply = {"status": status}
                self.assertEqual(self.tick(3)["result"]["status"], "planted")
        self.assertIsNone(self.controller.equipment_job)

    def test_material_attempt_stops_once_and_returns_to_hobbies_after_native_idle(self):
        self.start_materials()
        self.assertEqual(self.controller.equipment_job["root_id"], 7)
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "equipment")
        self.game["native_user_task_seen_at"] = self.now + 18
        self.assertEqual(self.tick(18)["result"]["status"], "stopping_equipment")
        self.tick(3)
        self.assertEqual(
            sum(c.args[0] == "agent_action" for c in self.execute.call_args_list), 1
        )
        self.game.update(hasActiveTask=False, navigation={"status": "stopped"})
        self.assertEqual(self.tick(3)["result"]["status"], "planted")
        self.assertIsNone(self.controller.equipment_job)
        self.assertEqual(
            sum(c.args[0] == "ensure_scepter" for c in self.execute.call_args_list), 1
        )
        self.assertGreaterEqual(self.controller.equipment_due - self.now, 100)

    def test_replaced_or_unobserved_native_root_is_never_cancelled(self):
        self.start_materials()
        self.game.update(
            native_user_task={"id": 8}, native_user_task_seen_at=self.now + 18
        )
        self.tick(18)
        self.assertIsNone(self.controller.equipment_job)
        self.assertEqual(
            self.controller.equipment_result["status"], "equipment_replaced"
        )
        self.assertFalse(
            any(c.args[0] == "agent_action" for c in self.execute.call_args_list)
        )

    def test_disabled_stop_tool_prevents_optional_acquisition_or_crafting(self):
        self.available.remove("agent_action")
        self.supply = {"status": "ready_to_craft"}
        self.assertEqual(self.tick()["result"]["status"], "planted")
        self.assertEqual(
            self.execute.call_args_list[0].args,
            ("ensure_scepter", {"allow_crafting": False}),
        )
        self.assertFalse(
            any(c.args[0] == "obtain_item" for c in self.execute.call_args_list)
        )

    def test_player_priority_clears_optional_attempt_and_keeps_retry_backoff(self):
        self.start_materials()
        self.assertTrue(self.controller.yield_to_player("Alex", "Come here"))
        self.assertIsNone(self.controller.equipment_job)
        self.execute.reset_mock()
        self.tick(10)
        self.execute.assert_not_called()

    def test_required_command_preflight_precedes_hobbies_until_observed_ready(self):
        self.supply = {"status": "equipment_preparing", "preflight_required": True}
        self.assertEqual(self.tick()["result"]["status"], "equipment_preparing")
        self.assertIsNone(self.controller.job)
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "equipment")
        self.tick(1)
        self.assertFalse(
            any(c.args[0] == "farm_crops" for c in self.execute.call_args_list)
        )
        self.supply = {"status": "ready", "preflight_required": False}
        self.assertEqual(self.tick(1)["result"]["status"], "planted")

    def test_saved_goal_does_not_skip_the_advance_command_preflight(self):
        self.available.add("manage_trees")
        self.game["tree_task"] = {
            "status": "active",
            "config": {"count": 2},
            "progress": {"completed": 1},
        }
        self.supply = {"status": "equipment_preparing", "preflight_required": True}
        self.tick()
        self.assertFalse(
            any(c.args[0] == "manage_trees" for c in self.execute.call_args_list)
        )
        self.supply = {"status": "ready", "preflight_required": False}
        self.tick(1)
        self.assertTrue(
            any(c.args[0] == "manage_trees" for c in self.execute.call_args_list)
        )
        self.assertEqual(self.game["tree_task"]["progress"], {"completed": 1})

    def test_disabled_preflight_does_not_leave_a_false_equipment_wait(self):
        self.supply = {"status": "equipment_preparing", "preflight_required": True}
        self.tick()
        self.available.remove("ensure_scepter")
        self.assertEqual(self.tick(1)["result"]["status"], "planted")
        self.assertNotEqual(self.controller.snapshot(self.game)["phase"], "equipment")

    def test_rescue_and_emergency_defense_preempt_command_preflight(self):
        for key in ("survival", "self_defense"):
            with self.subTest(key=key):
                self.game[key] = {"active": True}
                self.assertIsNone(self.tick(2))
                self.execute.assert_not_called()
                del self.game[key]
