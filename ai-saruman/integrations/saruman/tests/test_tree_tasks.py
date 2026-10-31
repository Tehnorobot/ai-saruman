"""Counted tasks verify world outcomes and checkpoint before each interaction."""

import copy
import time
import unittest
from unittest.mock import patch

from saruman import tree_tasks
from saruman.procedures import ProcedureContinuation


class TreeTaskTests(unittest.TestCase):
    def setUp(self):
        self.world = {}
        self.items = [
            {"item": "minecraft:oak_sapling", "count": 2},
            {"item": "minecraft:bone_meal", "count": 16},
        ]
        self.saved = None
        self.search = {}
        for name, value in (
            ("_game_busy", False),
            ("read_player_inventory", {"position": "0,63,0", "items": self.items}),
            (
                "find_tree_spots",
                {
                    "spots": [
                        {"position": [2, 63, 0], "within_reach": True},
                        {"position": [-2, 63, 0], "within_reach": True},
                    ],
                    "existing_saplings": [],
                    "exploration": None,
                },
            ),
            ("tree_approach", None),
            ("plant_sapling_at", None),
            ("apply_bone_meal_at", None),
        ):
            patcher = patch("saruman.tree_tasks.trees." + name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        block = patch(
            "saruman.tree_tasks._block",
            side_effect=lambda p: self.world.get(tuple(p), "minecraft:air"),
        )
        block.start()
        self.addCleanup(block.stop)
        self.plant_sapling_at.side_effect = self.plant
        self.apply_bone_meal_at.side_effect = self.grow

    def save(self, task):
        self.saved = copy.deepcopy(task)

    def plant(self, x, y, z, sapling):
        self.assertTrue(
            any(
                t["stage"] == "planting" and t["position"] == [x, y, z]
                for t in self.saved["targets"]
            )
        )
        self.world[x, y, z] = "minecraft:" + sapling
        self.items[0]["count"] -= 1
        return {"planted": True, "position": [x, y, z]}

    def grow(self, x, y, z):
        self.assertTrue(any(t["stage"] == "fertilizing" for t in self.saved["targets"]))
        self.assertGreater(self.saved["bone_meal_charged"], 0)
        self.world[x, y, z] = "minecraft:oak_log"
        self.items[1]["count"] -= 1
        return {"applied": True, "tree_grown": True, "consumed": 1}

    def start(self, **kwargs):
        task = tree_tasks.new_task(tree_tasks.task_config(count=2, **kwargs))
        self.save(task)
        return task

    def advance(self, task):
        return tree_tasks.advance(task, self.save, self.search)

    def test_two_new_plantings_progress_and_completed_replay_is_noop(self):
        task = self.start()
        first = self.advance(task)
        self.assertEqual(first["progress"]["completed"], 1)
        self.assertEqual(first["status"], "in_progress")
        second = self.advance(task)
        self.assertEqual(second["progress"]["completed"], 2)
        self.assertEqual(second["status"], "completed")
        self.assertNotIn("next_action", second)
        self.assertEqual(self.advance(task)["status"], "completed")
        self.assertEqual(self.plant_sapling_at.call_count, 2)
        self.apply_bone_meal_at.assert_not_called()

    def test_requests_total_missing_saplings_then_resumes_existing_targets(self):
        task = self.start()
        self.items[0]["count"] = 1
        missing = self.advance(task)
        self.assertEqual(
            missing["next_action"],
            {"tool": "obtain_item", "arguments": {"item": "oak_sapling", "count": 2}},
        )
        self.assertEqual(missing["progress"]["completed"], 0)
        self.plant_sapling_at.assert_not_called()
        self.items[0]["count"] = 2
        self.assertEqual(self.advance(task)["progress"]["completed"], 1)
        self.assertEqual(self.advance(task)["progress"]["completed"], 2)

    def test_busy_and_move_do_not_increment_completion(self):
        task = self.start()
        self._game_busy.return_value = True
        self.assertEqual(self.advance(task)["status"], "waiting_for_game_task")
        self.find_tree_spots.assert_not_called()
        self._game_busy.return_value = False
        self.read_player_inventory.return_value["position"] = "10,63,0"
        self.tree_approach.return_value = {"command": "@goto 4 63 0"}
        moving = self.advance(task)
        self.assertEqual(moving["status"], "needs_move")
        self.assertEqual(moving["progress"]["completed"], 0)
        self.read_player_inventory.return_value["position"] = "0,63,0"
        self.assertEqual(self.advance(task)["progress"]["completed"], 1)

    def test_checks_other_reserved_site_before_declaring_route_blocked(self):
        task = self.start()
        task["targets"] = [
            {"position": [x, 63, 0], "stage": "planned", "owned": False}
            for x in (10, 14)
        ]
        self.tree_approach.side_effect = lambda p, *a, **kw: (
            {"command": "@goto 12 63 0", "standing_position": [12, 63, 0]}
            if p[0] == 14
            else None
        )
        result = self.advance(task)
        self.assertEqual(result["status"], "needs_move")
        self.assertEqual(result["current_target"], [14, 63, 0])
        self.assertEqual(task["targets"][0]["stage"], "planned")
        self.find_tree_spots.assert_not_called()
        self.plant_sapling_at.assert_not_called()

    def test_in_reach_reserved_site_can_be_planted_before_the_far_site(self):
        task = self.start()
        task["targets"] = [
            {"position": [x, 63, 0], "stage": "planned", "owned": False}
            for x in (10, -2)
        ]
        result = self.advance(task)
        self.assertEqual(result["progress"]["completed"], 1)
        self.assertEqual(task["targets"][0]["stage"], "planned")
        self.assertEqual(task["targets"][1]["stage"], "planted")
        self.tree_approach.assert_not_called()

    def test_replans_only_unplanted_site_preserving_grown_count_and_total_doses(self):
        task = self.start(mode="grow")
        task["targets"] = [
            {"position": [30, 63, 0], "stage": "grown", "owned": True},
            {"position": [20, 63, 0], "stage": "planned", "owned": False},
        ]
        task["bone_meal_charged"] = 2
        self.find_tree_spots.return_value = {
            "spots": [{"position": [2, 63, 0]}],
            "existing_saplings": [],
        }
        result = self.advance(task)
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["progress"]["completed"], 1)
        self.assertEqual(task["targets"][1]["stage"], "abandoned")
        self.assertEqual(task["targets"][1]["position"], [20, 63, 0])
        self.assertEqual(task["targets"][2]["position"], [2, 63, 0])
        self.assertEqual(task["bone_meal_charged"], 2)
        self.plant_sapling_at.assert_not_called()
        final = self.advance(task)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["progress"]["completed"], 2)
        self.assertEqual(final["bone_meal_charged"], 3)
        self.plant_sapling_at.assert_called_once()

    def test_planted_sapling_is_never_replaced_just_because_route_is_unknown(self):
        task = self.start(mode="grow")
        task["targets"] = [
            {"position": [30, 63, 0], "stage": "grown", "owned": True},
            {"position": [20, 63, 0], "stage": "planted", "owned": True},
        ]
        self.world[20, 63, 0] = "minecraft:oak_sapling"
        result = self.advance(task)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(task["targets"][1]["stage"], "planted")
        self.assertEqual(result["progress"]["completed"], 1)
        self.find_tree_spots.assert_not_called()
        self.plant_sapling_at.assert_not_called()

    def test_route_recovery_can_use_bounded_exploration_without_dropping_plan(self):
        task = self.start()
        task["targets"] = [
            {"position": [10, 63, 0], "stage": "planned", "owned": False}
        ]
        action = {"tool": "agent_action", "arguments": {"action": "@goto -4 63 0"}}
        self.find_tree_spots.return_value = {
            "spots": [],
            "existing_saplings": [],
            "exploration": {"next_action": action},
        }
        result = self.advance(task)
        self.assertEqual(result["status"], "needs_move")
        self.assertEqual(result["next_action"], action)
        self.assertEqual(task["targets"][0]["stage"], "planned")
        self.assertEqual(task["steps"], 1)
        task["steps"] = 1000
        self.assertEqual(self.advance(task)["status"], "blocked")
        self.assertEqual(self.find_tree_spots.call_count, 1)

    def test_already_existing_sapling_cannot_count_as_new_planting(self):
        task = self.start()
        self.world[2, 63, 0] = "minecraft:oak_sapling"
        result = self.advance(task)
        self.assertEqual(result["progress"]["completed"], 0)
        self.plant_sapling_at.assert_not_called()
        self.assertEqual(task["targets"][0]["stage"], "abandoned")

    def test_far_return_preserves_completed_tree_and_reserved_site_on_route_failure(
        self,
    ):
        task = self.start(mode="grow")
        task["targets"] = [
            {"position": [1170, 63, -481], "stage": "grown", "owned": False},
            {"position": [1176, 63, -481], "stage": "planned", "owned": False},
        ]
        task["bone_meal_charged"] = 3
        self.read_player_inventory.return_value["position"] = "1121.9,74,-389.7"
        self.tree_approach.return_value = {
            "standing_position": [1126, 74, -393],
            "command": "@goto 1126 74 -393",
        }
        result = self.advance(task)
        self.assertEqual(result["status"], "needs_move")
        self.assertEqual(result["progress"]["completed"], 1)
        self.assertEqual(result["current_target"], [1176, 63, -481])
        self.assertEqual(task["targets"][1]["return_waypoints"], [[1126, 74, -393]])
        self.tree_approach.return_value = None
        result = self.advance(task)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(task["targets"][1]["stage"], "planned")
        self.assertEqual(task["bone_meal_charged"], 3)
        self.assertEqual(result["progress"]["completed"], 1)
        self.find_tree_spots.assert_called_once()
        self.plant_sapling_at.assert_not_called()
        self.apply_bone_meal_at.assert_not_called()

    def test_exception_after_click_recovers_exact_position_without_replanting(self):
        task = self.start()

        def click_then_timeout(*args):
            self.plant(*args)
            raise TimeoutError("reply lost")

        self.plant_sapling_at.side_effect = click_then_timeout
        with self.assertRaises(TimeoutError):
            self.advance(task)
        self.assertEqual(self.saved["targets"][0]["stage"], "planting")
        restored = copy.deepcopy(self.saved)
        self.plant_sapling_at.side_effect = self.plant
        result = self.advance(restored)
        self.assertEqual(result["progress"]["completed"], 2)
        self.assertEqual(self.plant_sapling_at.call_count, 2)  # one click per position

    def test_uncertain_placement_stops_without_claiming_success_or_replay(self):
        task = self.start()
        self.plant_sapling_at.side_effect = None
        self.plant_sapling_at.return_value = {
            "planted": False,
            "status": "placement_not_confirmed",
        }
        result = self.advance(task)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["progress"]["completed"], 0)
        self.advance(task)
        self.plant_sapling_at.assert_called_once()

    def test_two_grown_trees_are_distinct_and_share_one_total_budget(self):
        task = self.start(mode="grow", max_bone_meal=2)
        self.assertEqual(self.advance(task)["progress"]["completed"], 1)
        final = self.advance(task)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["progress"]["grown"], 2)
        self.assertEqual(final["bone_meal_charged"], 2)
        self.assertEqual(self.apply_bone_meal_at.call_count, 2)

    def test_refill_preserves_full_attempt_allowance_for_the_remaining_tree(self):
        task = self.start(mode="grow")
        self.assertEqual(self.advance(task)["progress"]["completed"], 1)
        self.assertEqual(task["targets"][0]["attempts"], 1)
        self.items[1]["count"] = 0
        result = self.advance(task)
        self.assertEqual(result["status"], "needs_materials")
        self.assertEqual(result["missing"], {"item": "bone_meal", "count": 5})
        self.apply_bone_meal_at.assert_called_once()

    def test_five_failed_attempts_leave_sapling_and_continue_the_other_tree(self):
        task = self.start(mode="grow")
        self.apply_bone_meal_at.side_effect = lambda *p: {
            "applied": True,
            "tree_grown": False,
            "consumed": 1,
        }
        first = self.advance(task)
        self.assertEqual(self.apply_bone_meal_at.call_count, 5)
        self.assertEqual(first["status"], "in_progress")
        self.assertEqual(first["progress"]["skipped"], 1)
        self.assertTrue(task["targets"][0]["exhausted"])
        self.assertEqual(task["targets"][0]["stage"], "planted")
        self.apply_bone_meal_at.side_effect = self.grow
        final = self.advance(task)
        self.assertEqual(final["status"], "partial")
        self.assertEqual(final["progress"]["completed"], 1)
        self.assertEqual(final["progress"]["skipped"], 1)
        self.assertEqual(self.apply_bone_meal_at.call_count, 6)
        self.assertEqual(self.advance(task)["status"], "partial")
        self.assertEqual(self.apply_bone_meal_at.call_count, 6)

    def test_processed_clicks_without_effect_are_bounded_and_not_called_success(self):
        task = self.start(mode="grow")
        self.apply_bone_meal_at.side_effect = lambda *p: {
            "applied": False,
            "retry_safe": True,
            "tree_grown": False,
            "consumed": 0,
        }
        self.advance(task)
        final = self.advance(task)
        self.assertEqual(final["status"], "partial")
        self.assertEqual(final["progress"]["completed"], 0)
        self.assertEqual(final["progress"]["skipped"], 2)
        self.assertEqual(self.apply_bone_meal_at.call_count, 10)
        self.assertEqual([t["attempts"] for t in task["targets"]], [5, 5])

    def test_fifth_attempt_can_grow_tree_and_is_not_marked_skipped(self):
        task = self.start(mode="grow")
        attempts = 0

        def apply(*p):
            nonlocal attempts
            attempts += 1
            return (
                self.grow(*p)
                if attempts == 5
                else {"applied": True, "tree_grown": False}
            )

        self.apply_bone_meal_at.side_effect = apply
        result = self.advance(task)
        self.assertEqual(result["progress"]["completed"], 1)
        self.assertEqual(result["progress"]["skipped"], 0)
        self.assertEqual(task["targets"][0]["attempts"], 5)

    def test_attempt_limit_survives_material_refill_and_checkpoint_restore(self):
        task = self.start(mode="grow")
        self.items[1]["count"] = 2

        def apply(*p):
            self.items[1]["count"] -= 1
            return {"applied": True, "tree_grown": False, "consumed": 1}

        self.apply_bone_meal_at.side_effect = apply
        self.advance(task)
        restored = copy.deepcopy(self.saved)
        self.assertEqual(restored["targets"][0]["attempts"], 2)
        self.assertEqual(self.advance(restored)["status"], "needs_materials")
        self.items[1]["count"] = 3
        self.advance(restored)
        self.assertTrue(restored["targets"][0]["exhausted"])
        self.assertEqual(self.apply_bone_meal_at.call_count, 5)

    def test_safe_no_click_does_not_spend_attempt_but_unknown_click_still_blocks(self):
        task = self.start(mode="grow")
        self.apply_bone_meal_at.side_effect = None
        self.apply_bone_meal_at.return_value = {
            "applied": False,
            "reason": "no_bone_meal",
        }
        self.advance(task)
        self.assertEqual(task["targets"][0]["attempts"], 0)
        self.assertEqual(task["bone_meal_charged"], 0)
        self.apply_bone_meal_at.return_value = {
            "applied": False,
            "reason": "application_not_confirmed",
            "interaction": {"ok": False},
        }
        self.assertEqual(self.advance(task)["status"], "blocked")
        self.assertEqual(task["targets"][0]["attempts"], 1)

    def test_budget_exhaustion_preserves_first_tree_and_does_not_refill_itself(self):
        task = self.start(mode="grow", max_bone_meal=1)
        self.assertEqual(self.advance(task)["progress"]["completed"], 1)
        final = self.advance(task)
        self.assertEqual(final["status"], "blocked")
        self.assertEqual(final["progress"]["completed"], 1)
        self.advance(task)
        self.apply_bone_meal_at.assert_called_once()

    def test_consumed_dose_without_trunk_is_not_completed(self):
        task = self.start(mode="grow", max_bone_meal=1)
        self.apply_bone_meal_at.side_effect = None
        self.apply_bone_meal_at.return_value = {
            "applied": True,
            "tree_grown": False,
            "consumed": 1,
        }
        result = self.advance(task)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["progress"]["completed"], 0)
        self.assertEqual(result["progress"]["planted"], 1)

    def test_interrupted_dose_reserves_budget_and_never_blindly_repeats(self):
        task = self.start(mode="grow", max_bone_meal=2)
        self.apply_bone_meal_at.side_effect = TimeoutError("unknown")
        with self.assertRaises(TimeoutError):
            self.advance(task)
        restored = copy.deepcopy(self.saved)
        self.assertEqual(restored["bone_meal_charged"], 1)
        result = self.advance(restored)
        self.assertEqual(result["status"], "blocked")
        self.apply_bone_meal_at.assert_called_once()

    def test_growth_reuses_existing_saplings_but_still_requires_observed_trunks(self):
        task = self.start(mode="grow")
        self.find_tree_spots.return_value["spots"] = []
        self.find_tree_spots.return_value["existing_saplings"] = [
            [2, 63, 0],
            [-2, 63, 0],
        ]
        self.items[0]["count"] = 0
        self.world = {
            (2, 63, 0): "minecraft:oak_sapling",
            (-2, 63, 0): "minecraft:oak_sapling",
        }
        self.assertEqual(self.advance(task)["progress"]["completed"], 1)
        self.assertEqual(self.advance(task)["status"], "completed")
        self.plant_sapling_at.assert_not_called()

    def test_interrupted_successful_growth_recovers_without_fertilizing_same_tree_again(
        self,
    ):
        task = self.start(mode="grow", max_bone_meal=2)

        def grow_then_timeout(*args):
            self.grow(*args)
            raise TimeoutError("reply lost")

        self.apply_bone_meal_at.side_effect = grow_then_timeout
        with self.assertRaises(TimeoutError):
            self.advance(task)
        restored = copy.deepcopy(self.saved)
        self.apply_bone_meal_at.side_effect = self.grow
        result = self.advance(restored)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["bone_meal_charged"], 2)
        self.assertEqual(self.apply_bone_meal_at.call_count, 2)

    def test_too_close_sites_are_not_reserved_as_two_separate_growth_sites(self):
        task = self.start()
        self.find_tree_spots.return_value["spots"][1]["position"] = [3, 63, 0]
        self.assertEqual(self.advance(task)["progress"]["completed"], 1)
        blocked = self.advance(task)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["progress"]["completed"], 1)
        self.plant_sapling_at.assert_called_once()

    def test_snapshot_failure_still_persists_attempt_budget(self):
        task = self.start()
        self.plant_sapling_at.side_effect = TimeoutError("request failed")
        with self.assertRaises(TimeoutError):
            self.advance(task)
        self.assertEqual(self.saved["steps"], 1)
        restored = copy.deepcopy(self.saved)
        with (
            patch("saruman.tree_tasks._block", side_effect=RuntimeError("read failed")),
            self.assertRaises(RuntimeError),
        ):
            self.advance(restored)
        self.assertEqual(self.saved["steps"], 2)

    def test_deadline_spacing_and_input_bounds(self):
        task = self.start()
        task["created_at"] = time.time() - 1300
        self.assertEqual(self.advance(task)["status"], "blocked")
        self.plant_sapling_at.assert_not_called()
        for kwargs in (
            {"count": 0},
            {"count": True},
            {"count": 33},
            {"mode": "grow", "max_bone_meal": 0},
        ):
            with self.assertRaises(ValueError):
                tree_tasks.task_config(**kwargs)
        self.assertEqual(tree_tasks.task_config(3, "grow")["max_bone_meal"], 15)


class BatchContinuationTests(unittest.TestCase):
    def test_batch_allows_only_expected_native_step_then_waits_and_resumes(self):
        continuation = ProcedureContinuation("Plant 2 saplings")
        result = {
            "task_id": "job",
            "status": "needs_materials",
            "next_action": {
                "tool": "obtain_item",
                "arguments": {"item": "oak_sapling", "count": 2},
            },
        }
        with patch("saruman.procedures.time.monotonic", return_value=0):
            continuation.observe("manage_trees", {}, result)
            self.assertFalse(
                continuation.blocks_task_start(
                    "obtain_item", {"item": "oak_sapling", "count": 2}
                )
            )
            self.assertTrue(
                continuation.blocks_task_start(
                    "obtain_item", {"item": "bone_meal", "count": 1}
                )
            )
            continuation.observe(
                "obtain_item", {}, "Queued obtaining/crafting oak_sapling"
            )
            self.assertTrue(continuation.blocks_task_start("manage_trees", {}))
        with patch("saruman.procedures.time.monotonic", return_value=3):
            self.assertIsNone(continuation.poll(True))
            self.assertIn("Plant 2 saplings", continuation.poll(False))
        self.assertEqual(continuation.remaining, 4)

    def test_confirmed_partial_progress_schedules_next_step_without_idle_wait(self):
        continuation = ProcedureContinuation("Plant 2 saplings")
        with patch("saruman.procedures.time.monotonic", return_value=0):
            continuation.observe(
                "manage_trees", {}, {"task_id": "job", "status": "in_progress"}
            )
        with patch("saruman.procedures.time.monotonic", return_value=3):
            self.assertIsNotNone(continuation.poll(False))
        continuation.observe(
            "manage_trees", {}, {"task_id": "job", "status": "completed"}
        )
        self.assertTrue(continuation.done)


if __name__ == "__main__":
    unittest.main()
