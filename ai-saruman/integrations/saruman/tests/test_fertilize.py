"""Bone meal interactions use fake gateways; no live item is consumed."""

import unittest
from unittest.mock import call, patch

from saruman.fertilize import apply_bone_meal_at
from saruman.minecraft import validate_action_command


def inventory(count, slot=11):
    return {
        "ok": True,
        "selectedHotbar": 0,
        "slots": [
            {"index": slot, "item": "minecraft:bone_meal", "count": count},
            {"index": 5, "item": "minecraft:bread", "count": 4},
        ],
    }


class FertilizeTests(unittest.TestCase):
    def setUp(self):
        factory = patch("saruman.fertilize.JavaGateway")
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        sleep = patch("saruman.fertilize.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.gateway = self.factory.return_value
        self.entry = self.gateway.entry_point
        self.entry.inGame.return_value = True
        self.entry.hasActiveTask.return_value = False
        self.entry.getBlockAt.return_value = {"id": "minecraft:oak_sapling"}
        self.entry.getGameState.return_value = {
            "self": {"lookingAt": "oak_sapling@1,63,2"}
        }
        self.entry.getInventoryFull.side_effect = [
            inventory(3),
            inventory(3, 0),
            inventory(2, 0),
        ]
        self.entry.placeBlockLooking.return_value = {
            "ok": True,
            "target": "1, 63, 2",
            "result": "SUCCESS",
        }

    def assert_restored(self):
        self.assertEqual(
            self.entry.clickUiSlot.call_args_list,
            [call(11, 0, "SWAP"), call(11, 0, "SWAP")],
        )
        self.assertEqual(self.entry.selectHotbar.call_args_list, [call(0), call(0)])
        self.gateway.close.assert_called_once()

    def test_main_inventory_dose_is_applied_once_without_claiming_growth(self):
        result = apply_bone_meal_at(1, 63, 2)
        self.assertTrue(result["applied"])
        self.assertEqual(result["consumed"], 1)
        self.assertFalse(result["tree_grown"])
        self.entry.lookAt.assert_called_once_with(1.5, 63.35, 2.5)
        self.entry.placeBlockLooking.assert_called_once()
        self.entry.useHeldItem.assert_not_called()
        self.entry.executeAgentActions.assert_not_called()
        self.assert_restored()

    def test_tree_growth_is_reported_only_when_trunk_appears(self):
        self.entry.getBlockAt.side_effect = [
            {"id": "minecraft:oak_sapling"},
            {"id": "minecraft:oak_log"},
        ]
        result = apply_bone_meal_at(1, 63, 2)
        self.assertTrue(result["tree_grown"])
        self.assertEqual(result["count_after"], 2)
        self.assert_restored()

    def test_bone_meal_in_hotbar_is_selected_without_swapping_inventory(self):
        equipped, after = inventory(3, 7), inventory(2, 7)
        equipped["selectedHotbar"] = after["selectedHotbar"] = 7
        self.entry.getInventoryFull.side_effect = [inventory(3, 7), equipped, after]
        self.assertTrue(apply_bone_meal_at(1, 63, 2)["applied"])
        self.entry.clickUiSlot.assert_not_called()
        self.assertEqual(self.entry.selectHotbar.call_args_list, [call(7), call(0)])
        self.gateway.close.assert_called_once()

    def test_unconfirmed_slot_selection_does_not_click(self):
        equipped = inventory(3, 0)
        equipped["selectedHotbar"] = 2
        self.entry.getInventoryFull.side_effect = [inventory(3), equipped]
        with self.assertRaisesRegex(RuntimeError, "slot was not confirmed"):
            apply_bone_meal_at(1, 63, 2)
        self.entry.placeBlockLooking.assert_not_called()
        self.assert_restored()

    def test_disappearance_is_not_proof_of_tree_growth(self):
        self.entry.getBlockAt.side_effect = [
            {"id": "minecraft:oak_sapling"},
            {"id": "minecraft:air"},
        ]
        self.assertIsNone(apply_bone_meal_at(1, 63, 2)["tree_grown"])

    def test_missing_bone_meal_or_wrong_target_does_not_interact(self):
        self.entry.getInventoryFull.side_effect = None
        self.entry.getInventoryFull.return_value = inventory(0)
        self.assertEqual(apply_bone_meal_at(1, 63, 2)["reason"], "no_bone_meal")
        self.entry.getBlockAt.return_value = {"id": "minecraft:grass_block"}
        self.assertEqual(apply_bone_meal_at(1, 63, 2)["reason"], "target_not_sapling")
        self.entry.selectHotbar.assert_not_called()
        self.entry.placeBlockLooking.assert_not_called()

    def test_active_task_or_missed_target_does_not_interact(self):
        self.entry.hasActiveTask.return_value = True
        self.assertEqual(apply_bone_meal_at(1, 63, 2)["reason"], "active_game_task")
        self.entry.hasActiveTask.return_value = False
        self.entry.getGameState.return_value = {
            "self": {"lookingAt": "grass_block@1,62,2"}
        }
        with patch("saruman.fertilize.time.monotonic", side_effect=[0, 2]):
            self.assertEqual(
                apply_bone_meal_at(1, 63, 2)["reason"], "target_not_in_crosshair"
            )
        self.entry.selectHotbar.assert_not_called()
        self.entry.placeBlockLooking.assert_not_called()

    def test_processed_unchanged_target_allows_bounded_retry_without_claiming_success(self):
        self.entry.getInventoryFull.side_effect = None
        self.entry.getInventoryFull.return_value = inventory(3, 0)
        with patch("saruman.fertilize.time.monotonic", side_effect=[0, 0, 3]):
            result = apply_bone_meal_at(1, 63, 2)
        self.assertFalse(result["applied"])
        self.assertTrue(result["retry_safe"])
        self.assertEqual(result["reason"], "application_not_confirmed")
        self.assertEqual(result["consumed"], 0)
        self.entry.placeBlockLooking.assert_called_once()

    def test_wrong_crosshair_target_in_result_does_not_claim_target_was_fertilized(
        self,
    ):
        self.entry.placeBlockLooking.return_value = {"ok": True, "target": "1,62,2"}
        result = apply_bone_meal_at(1, 63, 2)
        self.assertFalse(result["applied"])
        self.assertFalse(result["retry_safe"])
        self.assertEqual(result["consumed"], 1)
        self.assertEqual(result["reason"], "target_changed")

    def test_unconfirmed_interaction_receipt_never_allows_retry(self):
        self.entry.placeBlockLooking.return_value = {"ok": False, "error": "timeout"}
        result = apply_bone_meal_at(1, 63, 2)
        self.assertFalse(result["applied"])
        self.assertFalse(result.get("retry_safe", False))
        self.entry.placeBlockLooking.assert_called_once()
        self.assert_restored()

    def test_failure_after_equip_restores_inventory_even_if_swap_back_fails(self):
        self.entry.placeBlockLooking.side_effect = OSError("disconnected")
        self.entry.clickUiSlot.side_effect = [True, OSError("restore failed")]
        with self.assertRaises(OSError):
            apply_bone_meal_at(1, 63, 2)
        self.assert_restored()

    def test_bone_meal_place_command_is_rejected_with_supported_tool_hint(self):
        for command in ("@place bone_meal", "@place minecraft:bone_meal"):
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(ValueError, "apply_bone_meal"),
            ):
                validate_action_command(command)


if __name__ == "__main__":
    unittest.main()
