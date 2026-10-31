"""Fast tree procedures: fake world mutations, real local instruction routing."""

import unittest
from unittest.mock import ANY, patch

from saruman import trees
from saruman.procedures import (
    ProcedureContinuation,
    procedure_prompt,
    read_procedure,
    select_procedure,
)
from tests.fake_world_rules import FakeWorldRules


class GuideTests(unittest.TestCase):
    def test_local_counted_step_is_ready_without_artificial_delay_and_partial_is_terminal(
        self,
    ):
        continuation = ProcedureContinuation("Grow two trees")
        with patch("saruman.procedures.time.monotonic", return_value=0):
            continuation.observe(
                "manage_trees", {}, {"task_id": "job", "status": "in_progress"}
            )
            self.assertIsNotNone(continuation.poll(False))
            continuation.observe(
                "manage_trees", {}, {"task_id": "job", "status": "partial"}
            )
            self.assertTrue(continuation.done)

    def test_guides_match_requested_outcome_and_are_available_by_id(self):
        for request, name in (
            ("Посади пшеницу", "farm_crops"),
            ("Собери зрелый урожай", "farm_crops"),
            ("Найди враждебных мобов", "hunt_hostiles"),
            ("Вырастить дерево", "grow_tree"),
            ("посади саженец дуба", "plant_sapling"),
            ("Примени костную муку", "apply_bone_meal"),
        ):
            self.assertEqual(select_procedure(request), name)
            result = read_procedure(request)
            self.assertIn("instructions", result)
            self.assertEqual(
                result["instructions"], read_procedure(name)["instructions"]
            )
            self.assertIn(result["instructions"], procedure_prompt(request))
        self.assertIsNone(select_procedure("сруби дерево"))
        self.assertNotIn("instructions", read_procedure("../../auth.json"))
        self.assertEqual(procedure_prompt("привет"), "")

    def test_collection_completion_resumes_without_idle_timer(self):
        continuation = ProcedureContinuation("вырасти дерево")
        with patch("saruman.procedures.time.monotonic", return_value=0):
            continuation.observe(
                "obtain_item",
                {},
                "Queued obtaining/crafting oak_sapling, target count 1",
            )
            self.assertIsNone(
                continuation.poll(False)
            )  # queue has not reached the mod yet
        with patch("saruman.procedures.time.monotonic", return_value=3):
            self.assertIsNone(continuation.poll(True))
            message = continuation.poll(False)
        self.assertIn("вырасти дерево", message)
        self.assertFalse(continuation.pending)
        self.assertIsNone(continuation.poll(False))

    def test_pending_collection_cannot_be_overwritten_by_another_resource_task(self):
        continuation = ProcedureContinuation("вырасти дерево")
        continuation.observe(
            "obtain_item", {}, "Queued obtaining/crafting oak_sapling, target count 1"
        )
        self.assertTrue(
            continuation.blocks_task_start("obtain_item", {"item": "bone_meal"})
        )
        self.assertTrue(
            continuation.blocks_task_start(
                "agent_action", {"action": "@get bone_meal 8"}
            )
        )
        self.assertFalse(continuation.blocks_task_start("get_inventory", {}))
        self.assertFalse(
            continuation.blocks_task_start("agent_action", {"action": "@stop"})
        )

    def test_stop_success_timeout_and_retry_budget_end_continuation(self):
        for terminal in ("stop", "grown", "timeout"):
            continuation = ProcedureContinuation("вырасти дерево")
            with patch("saruman.procedures.time.monotonic", return_value=0):
                continuation.observe(
                    "grow_tree", {}, {"status": "waiting_for_game_task"}
                )
            if terminal == "stop":
                continuation.observe("agent_action", {"action": "@stop"}, True)
            elif terminal == "grown":
                continuation.observe("grow_tree", {}, {"status": "grown"})
            else:
                with patch("saruman.procedures.time.monotonic", return_value=181):
                    self.assertIn("timed out", continuation.poll(True))
            self.assertTrue(continuation.done)
            self.assertIsNone(continuation.poll(False))
        continuation = ProcedureContinuation("вырасти дерево")
        for _ in range(4):
            with patch("saruman.procedures.time.monotonic", return_value=0):
                continuation.observe(
                    "grow_tree", {}, {"status": "waiting_for_game_task"}
                )
            with patch("saruman.procedures.time.monotonic", return_value=3):
                self.assertIsNotNone(continuation.poll(False))
        continuation.observe("grow_tree", {}, {"status": "waiting_for_game_task"})
        self.assertFalse(continuation.pending)


class TreeScanTests(unittest.TestCase):
    def setUp(self):
        rules = patch("saruman.trees.NativeWorldRules", FakeWorldRules)
        rules.start()
        self.addCleanup(rules.stop)
        factory = patch("saruman.trees._gateway")
        self.gateway = factory.start().return_value
        self.addCleanup(factory.stop)
        self.entry = self.gateway.entry_point
        self.entry.getGameState.return_value = {"self": {"pos": "0.0,63.0,0.0"}}
        self.entry.getBlockAt.return_value = {"id": "minecraft:air"}
        self.entry.getBlocksAround.return_value = [
            {"position": "1,62,0", "block": "smooth sandstone"},
            {"position": "2,62,0", "block": "grass block"},
        ]
        self.entry.canPlaceBlock.return_value = {"canPlace": True}
        native = patch(
            "saruman.trees._can_plant",
            side_effect=lambda g, s, p, rules=None: rules.can_plant(s, p),
        )
        self.native = native.start()
        self.addCleanup(native.stop)

    def test_scan_returns_exact_valid_reachable_spot_and_never_moves(self):
        result = trees.find_tree_spots()
        self.assertEqual(result["spots"][0]["position"], [2, 63, 0])
        self.assertTrue(result["spots"][0]["within_reach"])
        self.native.assert_any_call(self.gateway, "oak_sapling", (2, 63, 0), ANY)
        self.entry.RunInnerCommand.assert_not_called()
        self.entry.placeBlockLooking.assert_not_called()
        self.entry.getBlocksAround.assert_called_once_with(5)
        self.gateway.close.assert_called_once()

    def test_native_soil_rejection_policy_and_roof_do_not_return_a_spot(self):
        self.native.side_effect = None
        self.native.return_value = False
        self.assertEqual(trees.find_tree_spots()["spots"], [])
        self.native.return_value = True
        self.entry.canPlaceBlock.return_value = {"canPlace": False}
        self.assertEqual(trees.find_tree_spots()["spots"], [])
        self.entry.canPlaceBlock.return_value = {"canPlace": True}
        self.entry.getBlocksAround.return_value += [
            {"position": "2,65,0", "block": "oak planks"}
        ]
        self.native.side_effect = lambda g, s, p, rules=None: rules.can_plant(s, p)
        self.assertEqual(trees.find_tree_spots()["spots"], [])

    def test_empty_scan_and_unsupported_two_by_two_tree_do_not_invent_a_place(self):
        self.entry.getBlocksAround.return_value = []
        self.assertEqual(trees.find_tree_spots()["spots"], [])
        with self.assertRaises(ValueError):
            trees.find_tree_spots("dark_oak_sapling")


class PlantTests(unittest.TestCase):
    def setUp(self):
        factory = patch("saruman.trees._gateway")
        self.gateway = factory.start().return_value
        self.addCleanup(factory.stop)
        self.entry = self.gateway.entry_point
        self.entry.hasActiveTask.return_value = False
        self.entry.getBlockAt.side_effect = [
            {"id": "minecraft:air"},
            {"id": "minecraft:oak_sapling"},
        ]
        self.entry.canPlaceBlock.return_value = {"canPlace": True}
        self.entry.getGameState.return_value = {
            "self": {"lookingAt": "grass_block@2,62,0"}
        }
        self.entry.getInventoryFull.side_effect = [
            {
                "ok": True,
                "selectedHotbar": 0,
                "slots": [{"index": 11, "item": "minecraft:oak_sapling", "count": 1}],
            },
            {
                "ok": True,
                "selectedHotbar": 0,
                "slots": [{"index": 0, "item": "minecraft:oak_sapling", "count": 1}],
            },
        ]
        for name in ("_can_plant", "_clearance"):
            mock = patch("saruman.trees." + name, return_value=True)
            mock.start()
            self.addCleanup(mock.stop)
        sleep = patch("saruman.trees.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_single_native_click_verifies_sapling_and_restores_inventory(self):
        result = trees.plant_sapling_at(2, 63, 0)
        self.assertTrue(result["planted"])
        self.entry.lookAt.assert_called_once_with(2.5, 62.99, 0.5)
        self.entry.placeBlockLooking.assert_called_once()
        self.assertEqual(self.entry.clickUiSlot.call_count, 2)
        self.gateway.close.assert_called_once()

    def test_wrong_soil_and_active_task_do_not_click(self):
        with patch("saruman.trees._can_plant", return_value=False):
            self.assertFalse(trees.plant_sapling_at(2, 63, 0)["planted"])
        self.entry.hasActiveTask.return_value = True
        self.assertEqual(
            trees.plant_sapling_at(2, 63, 0)["status"], "waiting_for_game_task"
        )
        self.entry.placeBlockLooking.assert_not_called()


class GrowTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {"item": "minecraft:oak_sapling", "count": 1},
            {"item": "minecraft:bone_meal", "count": 8},
        ]
        for name, value in (
            ("_game_busy", False),
            ("read_player_inventory", {"position": "0,63,0", "items": self.items}),
            (
                "find_tree_spots",
                {
                    "existing_saplings": [],
                    "spots": [{"position": [2, 63, 0], "within_reach": True}],
                },
            ),
            ("plant_sapling_at", {"planted": True}),
            (
                "apply_bone_meal_at",
                {"applied": True, "tree_grown": False, "consumed": 1},
            ),
        ):
            mock = patch("saruman.trees." + name, return_value=value)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)

    def test_grows_with_two_doses_without_model_round_per_dose(self):
        self.apply_bone_meal_at.side_effect = [
            {"applied": True, "tree_grown": False, "consumed": 1},
            {
                "applied": True,
                "tree_grown": True,
                "consumed": 1,
                "target_after": {"id": "minecraft:oak_log"},
            },
        ]
        result = trees.grow_tree_fast()
        self.assertEqual(result["status"], "grown")
        self.assertEqual(result["consumed"], 2)
        self.find_tree_spots.assert_called_once()
        self.plant_sapling_at.assert_called_once_with(2, 63, 0, "oak_sapling")
        self.assertEqual(self.apply_bone_meal_at.call_count, 2)

    def test_busy_native_task_is_not_replaced_or_scanned_again(self):
        self._game_busy.return_value = True
        self.assertEqual(trees.grow_tree_fast()["status"], "waiting_for_game_task")
        self.find_tree_spots.assert_not_called()
        self.plant_sapling_at.assert_not_called()
        self.apply_bone_meal_at.assert_not_called()

    def test_existing_sapling_is_reused_and_budget_is_bounded(self):
        self.find_tree_spots.return_value["existing_saplings"] = [[2, 63, 0]]
        self.items.pop(0)
        result = trees.grow_tree_fast(max_bone_meal=2)
        self.assertEqual(result["status"], "bone_meal_budget_exhausted")
        self.assertEqual(self.apply_bone_meal_at.call_count, 2)
        self.plant_sapling_at.assert_not_called()

    def test_missing_materials_or_missing_site_never_clicks(self):
        self.items.clear()
        self.assertEqual(trees.grow_tree_fast()["status"], "needs_materials")
        self.apply_bone_meal_at.assert_not_called()
        self.plant_sapling_at.assert_not_called()
        self.items.extend(
            [
                {"item": "minecraft:oak_sapling", "count": 1},
                {"item": "minecraft:bone_meal", "count": 8},
            ]
        )
        self.find_tree_spots.return_value["spots"] = []
        self.assertEqual(trees.grow_tree_fast()["status"], "no_site")
        self.apply_bone_meal_at.assert_not_called()

    def test_unknown_click_result_stops_without_spending_another_dose(self):
        self.apply_bone_meal_at.return_value = {
            "applied": False,
            "reason": "application_not_confirmed",
        }
        self.assertEqual(trees.grow_tree_fast()["status"], "interaction_not_confirmed")
        self.apply_bone_meal_at.assert_called_once()


if __name__ == "__main__":
    unittest.main()
