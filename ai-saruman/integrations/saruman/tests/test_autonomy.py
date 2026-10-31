"""Background work must progress without chat triggers, but cannot replace native work."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from saruman.autonomy import Autonomy
from saruman.autonomy_settings import save_weights


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.settings_path = Path(temporary.name) / "autonomy.json"
        save_weights(
            {"farm": 3, "hunt": 2, "explore": 2, "trees": 0}, self.settings_path
        )
        self.controller = Autonomy(
            clock=lambda: self.now, rng=Mock(), settings_path=self.settings_path
        )
        self.controller.enabled = True
        self.controller.rng.choices.side_effect = lambda choices, **kw: [choices[0]]
        self.game = {
            "ingame": True,
            "hasActiveTask": False,
            "tree_task": {"status": "blocked", "progress": {"completed": 2}},
        }
        self.answers = {
            "get_inventory": {"food": 20, "health": 20, "items": []},
            "farm_crops": {"status": "no_work"},
            "hunt_hostiles": {"status": "no_target"},
        }
        self.execute = Mock(side_effect=lambda name, args: self.answers.get(name, True))

    def tick(self, seconds=9, **kwargs):
        self.now += seconds
        return self.controller.tick(self.game, self.execute, **kwargs)

    def test_blocked_operator_goal_is_preserved_and_another_activity_is_tried(self):
        self.tick()
        self.tick(3)
        names = [call.args[0] for call in self.execute.call_args_list]
        self.assertIn("farm_crops", names)
        self.assertIn("hunt_hostiles", names)
        self.assertEqual(self.game["tree_task"]["progress"]["completed"], 2)

    def test_no_background_action_over_busy_disconnected_or_pending_work(self):
        for state in ({"hasActiveTask": True}, {"ingame": False}):
            with self.subTest(state=state), patch.dict(self.game, state):
                self.assertIsNone(self.tick())
        self.assertIsNone(self.tick(procedure_pending=True))
        self.execute.assert_not_called()

    def test_stop_and_explicit_wait_pause_until_a_new_directive(self):
        self.controller.directive("Стой, ничего не делай")
        self.assertIsNone(self.tick())
        self.controller.directive("Продолжай жить своей жизнью")
        self.assertIsNotNone(self.tick())
        self.controller.pause()
        self.assertIsNone(self.tick())

    def test_confirmed_planting_continues_without_another_llm_turn(self):
        self.answers["farm_crops"] = {"status": "planted"}
        self.tick()
        self.tick(2)
        self.assertEqual(
            sum(c.args[0] == "farm_crops" for c in self.execute.call_args_list), 2
        )

    def test_one_observed_move_then_grace_and_busy_wait(self):
        action = {"tool": "agent_action", "arguments": {"action": "@goto 1 63 2"}}
        self.answers["farm_crops"] = {"status": "needs_move", "next_action": action}
        self.tick()
        self.assertEqual(
            self.execute.call_args.args, ("agent_action", action["arguments"])
        )
        self.assertIsNone(self.tick(0.2))
        self.game["hasActiveTask"] = True
        self.assertIsNone(self.tick(3))
        self.assertEqual(
            sum(c.args[0] == "agent_action" for c in self.execute.call_args_list), 1
        )

    def test_native_stop_failure_prevents_random_actions(self):
        self.game["navigation"] = {"status": "stop_failed"}
        self.assertIsNone(self.tick())
        self.execute.assert_not_called()

    def test_unknown_interaction_ends_only_this_activity_and_does_not_replay_its_action(
        self,
    ):
        self.answers["farm_crops"] = {
            "status": "interaction_unconfirmed",
            "position": [1, 63, 2],
            "next_action": {
                "tool": "agent_action",
                "arguments": {"action": "@goto 1 63 2"},
            },
        }
        self.tick()
        self.assertFalse(self.controller.paused)
        self.assertIsNone(self.controller.job)
        self.assertIsNone(self.tick(1))
        self.tick(2)
        self.assertEqual(self.controller.last_selection["id"], "hunt")
        self.assertEqual(
            sum(c.args[0] == "farm_crops" for c in self.execute.call_args_list), 1
        )
        self.assertNotIn(
            "agent_action", [c.args[0] for c in self.execute.call_args_list]
        )
        issue = self.controller.snapshot(self.game)["recent_issues"][0]
        self.assertEqual(issue["result"]["position"], [1, 63, 2])

    def test_tool_error_keeps_life_running_and_diagnostic_survives_death_cleanup(self):
        self.answers["farm_crops"] = {
            "status": "error",
            "detail": "Native result unknown",
        }
        self.tick()
        self.controller.end_activity("Player died; inspect state")
        self.tick(3)
        self.assertFalse(self.controller.paused)
        self.assertEqual(self.controller.last_selection["id"], "hunt")
        self.assertEqual(
            self.controller.snapshot(self.game)["recent_issues"][0]["result"]["detail"],
            "Native result unknown",
        )

    def test_saved_goal_error_blocks_only_that_goal_and_keeps_its_checkpoint(self):
        self.game["tree_task"] = {
            "id": "goal",
            "status": "active",
            "config": {"count": 2, "mode": "grow", "max_bone_meal": 7},
            "targets": [{"position": [1, 63, 2], "stage": "planted"}],
            "bone_meal_charged": 3,
        }
        self.answers["manage_trees"] = {
            "status": "error",
            "detail": "Native state unavailable",
        }
        self.tick()
        saved = self.game["tree_task"]
        self.assertEqual(saved["status"], "blocked")
        self.assertEqual(saved["bone_meal_charged"], 3)
        self.assertEqual(saved["config"]["max_bone_meal"], 7)
        self.assertEqual(saved["targets"][0]["position"], [1, 63, 2])
        self.tick(3)
        self.assertFalse(self.controller.paused)
        self.assertIn("farm_crops", [c.args[0] for c in self.execute.call_args_list])
        self.assertEqual(
            sum(c.args[0] == "manage_trees" for c in self.execute.call_args_list), 1
        )

    def test_saved_goal_continuation_rejection_does_not_pause_all_work(self):
        self.game["tree_task"] = {
            "id": "goal",
            "status": "active",
            "config": {"count": 2, "mode": "plant"},
            "targets": [],
        }
        self.answers["manage_trees"] = {
            "status": "needs_move",
            "next_action": {
                "tool": "agent_action",
                "arguments": {"action": "@goto 1 63 2"},
            },
        }
        self.answers["agent_action"] = False
        self.tick()
        self.assertEqual(self.game["tree_task"]["status"], "blocked")
        self.tick(3)
        self.assertFalse(self.controller.paused)
        self.assertIn("farm_crops", [c.args[0] for c in self.execute.call_args_list])

    def test_unconfirmed_tree_planting_does_not_starve_the_exploration_queue(self):
        save_weights(
            {"farm": 0, "hunt": 0, "explore": 1, "trees": 1}, self.settings_path
        )
        self.controller.rng.choices.side_effect = lambda choices, **kw: [
            next((a for a in choices if a["id"] == "trees"), choices[0])
        ]
        self.answers["tend_trees"] = {
            "status": "interaction_unconfirmed",
            "position": [1, 63, 2],
        }
        self.answers["explore_nearby"] = {"status": "no_work"}
        self.tick()
        self.assertEqual(self.controller.history[-1]["id"], "trees")
        self.tick(3)
        self.assertEqual(self.controller.last_selection["id"], "explore")
        self.assertFalse(self.controller.paused)
        self.assertEqual(
            sum(c.args[0] == "tend_trees" for c in self.execute.call_args_list), 1
        )

    def test_disabled_saved_goal_tool_does_not_starve_enabled_hobbies(self):
        self.game["tree_task"] = {
            "id": "goal",
            "status": "active",
            "config": {"count": 2, "mode": "plant"},
            "targets": [],
        }
        available = {"get_inventory", "farm_crops"}
        self.tick(available_tools=available)
        self.assertEqual(self.game["tree_task"]["status"], "blocked")
        self.tick(3, available_tools=available)
        self.assertIn("farm_crops", [c.args[0] for c in self.execute.call_args_list])
        self.assertFalse(self.controller.paused)

    def test_error_never_revokes_later_operator_wait(self):
        self.answers["farm_crops"] = {"status": "error"}
        self.tick()
        self.controller.directive("Остановись и жди")
        self.execute.reset_mock()
        self.assertIsNone(self.tick(100))
        self.execute.assert_not_called()
        self.assertTrue(self.controller.paused)

    def test_food_has_priority_without_eating_the_last_planting_carrot(self):
        self.answers["get_inventory"] = {
            "food": 12,
            "items": [
                {"item": "minecraft:bread", "count": 2},
                {"item": "minecraft:carrot", "count": 1},
            ],
        }
        self.answers["eat_food"] = {"consumed": True}
        self.tick()
        self.assertEqual(self.execute.call_args.args, ("eat_food", {"item": "bread"}))
        self.assertIsNone(self.controller.job)

    def test_actionable_saved_tree_goal_has_priority_and_is_not_reset(self):
        self.game["tree_task"] = {
            "status": "active",
            "config": {"count": 3, "mode": "plant"},
        }
        self.answers["manage_trees"] = {"status": "in_progress"}
        self.tick()
        self.execute.assert_called_once_with(
            "manage_trees", {"count": 3, "mode": "plant", "new_task": False}
        )

    def test_background_activity_has_a_step_budget(self):
        self.answers["farm_crops"] = {"status": "planted"}
        for _ in range(9):
            self.tick(2 if self.now else 9)
        self.assertEqual(
            sum(c.args[0] == "farm_crops" for c in self.execute.call_args_list), 8
        )
        self.assertIn("farm", self.controller.cooldowns)

    def test_missing_farm_hoe_queues_one_native_task_and_waits(self):
        self.answers["farm_crops"] = {
            "status": "needs_materials",
            "next_action": {
                "tool": "obtain_item",
                "arguments": {"item": "wooden_hoe", "count": 1},
            },
        }
        self.answers["obtain_item"] = "Queued obtaining/crafting wooden_hoe"
        self.tick()
        self.assertFalse(self.controller.paused)
        self.assertEqual(
            self.execute.call_args.args,
            ("obtain_item", {"item": "wooden_hoe", "count": 1}),
        )
        self.assertIsNone(self.tick(0.1))

    def test_wheat_is_crafted_into_edible_bread_before_hunting(self):
        self.answers["get_inventory"] = {
            "food": 10,
            "items": [{"item": "minecraft:wheat", "count": 3}],
        }
        self.answers["obtain_item"] = "Queued obtaining/crafting bread"
        self.tick()
        self.assertEqual(
            self.execute.call_args.args, ("obtain_item", {"item": "bread", "count": 1})
        )

    def test_saved_tree_next_action_really_queues_instead_of_only_announcing(self):
        self.game["tree_task"] = {
            "status": "active",
            "config": {"count": 3, "mode": "plant"},
        }
        self.answers["manage_trees"] = {
            "status": "needs_move",
            "next_action": {
                "tool": "agent_action",
                "arguments": {"action": "@goto 1 63 0"},
            },
        }
        self.tick()
        self.assertEqual(
            self.execute.call_args.args, ("agent_action", {"action": "@goto 1 63 0"})
        )

    def test_live_weights_apply_without_restarting_current_job(self):
        self.answers["farm_crops"] = {"status": "planted"}
        self.tick()
        selected = self.controller.last_selection["selected_at"]
        save_weights(
            {"farm": 0, "hunt": 100, "explore": 0, "trees": 0}, self.settings_path
        )
        self.tick(2)
        self.assertEqual(self.execute.call_args.args[0], "farm_crops")
        self.assertEqual(self.controller.last_selection["selected_at"], selected)
        self.assertEqual(self.controller.weights["hunt"], 100)
        self.answers["farm_crops"] = {"status": "no_work"}
        self.tick(2)
        self.tick(3)
        self.assertEqual(self.execute.call_args.args[0], "hunt_hostiles")
        self.assertEqual(self.controller.last_selection["probability"], 100)

    def test_zero_weights_disable_random_choices_but_keep_food_priority(self):
        save_weights(
            {"farm": 0, "hunt": 0, "explore": 0, "trees": 0}, self.settings_path
        )
        self.assertEqual(self.tick()["result"]["status"], "zero_weights")
        self.controller.rng.choices.assert_not_called()
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "zero_weights")
        self.answers["get_inventory"] = {
            "food": 10,
            "items": [{"item": "minecraft:bread", "count": 1}],
        }
        self.tick(9)
        self.assertEqual(self.execute.call_args.args, ("eat_food", {"item": "bread"}))

    def test_terminal_selection_and_cooldown_are_visible_after_job_is_cleared(self):
        self.tick()
        snapshot = self.controller.snapshot(self.game)
        self.assertIsNone(snapshot["activity"])
        self.assertEqual(snapshot["last_selection"]["id"], "farm")
        self.assertEqual(snapshot["history"][0]["status"], "no_work")
        self.assertAlmostEqual(snapshot["last_selection"]["probability"], 300 / 7)
        self.assertEqual(snapshot["activities"][0]["next_probability"], 0)
        self.assertEqual(snapshot["activities"][1]["next_probability"], 50)
        self.game["hasActiveTask"] = True
        self.tick(2)
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "native_task")

    def test_invalid_settings_block_new_choices_until_repaired(self):
        self.settings_path.write_text('{"weights": {"farm": -1}}', encoding="utf-8")
        self.tick()
        self.controller.rng.choices.assert_not_called()
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "settings_error")
        save_weights(
            {"farm": 0, "hunt": 0, "explore": 1, "trees": 0}, self.settings_path
        )
        self.tick(2)
        self.assertIsNone(self.controller.settings_error)
        self.assertEqual(self.controller.last_selection["id"], "explore")

    def test_disabled_hunt_is_excluded_and_remaining_chances_are_normalized(self):
        available = {
            "get_inventory",
            "farm_crops",
            "explore_nearby",
            "agent_action",
            "attack_hostile",
        }
        self.tick(available_tools=available)
        snapshot = self.controller.snapshot(self.game)
        self.assertFalse(self.controller.paused)
        self.assertEqual(snapshot["activities"][1]["next_probability"], 0)
        self.assertIn("hunt_hostiles", snapshot["activities"][1]["unavailable_reason"])
        self.assertEqual(snapshot["activities"][2]["next_probability"], 100)
        self.tick(3, available_tools=available)
        self.assertEqual(self.controller.last_selection["id"], "explore")
        self.assertNotIn(
            "hunt_hostiles", [c.args[0] for c in self.execute.call_args_list]
        )

    def test_missing_attack_tool_disables_hunt_even_when_scan_tool_is_enabled(self):
        self.tick(available_tools={"get_inventory", "hunt_hostiles"})
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "unavailable")
        self.assertFalse(self.controller.paused)
        self.controller.rng.choices.assert_not_called()

    def test_disabled_all_tools_wait_without_any_execution_or_global_error_pause(self):
        self.tick(available_tools=set())
        self.execute.assert_not_called()
        self.assertFalse(self.controller.paused)
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "unavailable")
        available = {"get_inventory", "hunt_hostiles", "attack_hostile"}
        self.assertIsNone(self.tick(2, available_tools=available))
        self.tick(2, available_tools=available)
        self.assertEqual(self.controller.last_selection["id"], "hunt")
        self.assertFalse(self.controller.paused)

    def test_disabling_running_activity_waits_for_native_work_and_then_selects_another(
        self,
    ):
        self.answers["farm_crops"] = {"status": "planted"}
        self.tick()
        available = {"get_inventory", "hunt_hostiles", "attack_hostile"}
        self.game["hasActiveTask"] = True
        self.assertIsNone(self.tick(2, available_tools=available))
        self.assertEqual(self.controller.job["id"], "farm")
        self.game["hasActiveTask"] = False
        self.tick(2, available_tools=available)
        self.assertEqual(self.controller.history[0]["status"], "tool_unavailable")
        self.assertEqual(self.controller.last_selection["id"], "hunt")
        self.assertFalse(self.controller.paused)

    def test_disabled_material_tool_rejection_is_not_an_unknown_physical_interaction(
        self,
    ):
        self.answers["farm_crops"] = {
            "status": "needs_materials",
            "next_action": {
                "tool": "obtain_item",
                "arguments": {"item": "wooden_hoe", "count": 1},
            },
        }
        outcome = self.tick(available_tools={"get_inventory", "farm_crops"})
        self.assertEqual(outcome["result"]["status"], "tool_unavailable")
        self.assertFalse(self.controller.paused)
        self.assertNotIn(
            "obtain_item", [c.args[0] for c in self.execute.call_args_list]
        )

    def test_young_crops_are_left_to_grow_while_other_activities_continue(self):
        self.answers["farm_crops"] = {"status": "waiting_for_growth"}
        self.tick()
        self.assertIsNone(self.controller.job)
        self.assertFalse(self.controller.paused)
        self.assertIn("farm", self.controller.cooldowns)
        self.tick(3)
        self.assertNotEqual(self.controller.last_selection["id"], "farm")

    def test_death_clears_current_activity_without_issuing_more_actions(self):
        self.answers["farm_crops"] = {"status": "planted"}
        self.tick()
        self.execute.reset_mock()
        self.game["alive"] = False
        self.tick(2)
        self.assertIsNone(self.controller.job)
        self.execute.assert_not_called()
        self.assertEqual(self.controller.snapshot(self.game)["phase"], "respawning")
        self.game["alive"] = True
        self.tick(3)
        self.assertTrue(self.execute.called)

    def test_stale_seed_continuation_never_starts_native_collection(self):
        self.answers["farm_crops"] = {
            "status": "needs_materials",
            "next_action": {
                "tool": "obtain_item",
                "arguments": {"item": "wheat_seeds", "count": 4},
            },
        }
        outcome = self.tick()
        self.assertEqual(outcome["result"]["status"], "blocked")
        self.assertNotIn(
            "obtain_item", [c.args[0] for c in self.execute.call_args_list]
        )

    def test_random_work_repeats_for_multiple_cycles_without_chat_events(self):
        self.answers["explore_nearby"] = {"status": "no_work"}
        for _ in range(125):
            self.tick(2)
        history = self.controller.history
        self.assertGreaterEqual(len(history), 9)
        for activity in ("farm", "hunt", "explore"):
            self.assertGreaterEqual(sum(row["id"] == activity for row in history), 3)
        self.assertFalse(self.controller.paused)
        self.assertEqual(self.game["tree_task"]["progress"]["completed"], 2)

    def test_current_activity_stop_waits_for_bridge_then_selects_a_different_activity(
        self,
    ):
        from saruman.navigation import NavigationGuard

        self.answers["farm_crops"] = {"status": "planted"}
        self.tick()
        self.controller.end_activity("Route was stopped")
        self.assertFalse(self.controller.paused)
        self.game["navigation"] = {"status": "stop_requested", "target": None}
        self.assertIsNone(self.tick(2))
        self.assertEqual(self.controller.history[0]["status"], "stopped")
        guard = NavigationGuard()
        guard.tick(Mock(), self.game, "0,63,0", False, "No tasks")
        self.tick(2)
        self.assertEqual(self.controller.last_selection["id"], "hunt")
        self.assertFalse(self.controller.paused)

    def test_current_activity_end_cannot_revoke_an_explicit_operator_pause(self):
        self.controller.directive("Остановись и жди")
        self.controller.end_activity("Native stop accepted")
        self.assertTrue(self.controller.paused)
        self.assertIsNone(self.tick(100))


if __name__ == "__main__":
    unittest.main()
