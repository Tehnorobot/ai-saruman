"""Crop fixtures never touch the real Minecraft world or inventory."""

import unittest
from unittest.mock import Mock, patch

from saruman import farming
from saruman.peaceful import guard_command, guard_crop_inputs


class FarmingTests(unittest.TestCase):
    def setUp(self):
        self.world = {(0, 62, 0): "farmland", (1, 62, 0): "farmland"}
        self.ripe = set()
        self.gateway = Mock()
        self.entry = self.gateway.entry_point
        self.entry.inGame.return_value = True
        self.entry.hasActiveTask.return_value = False
        self.entry.getGameState.return_value = {
            "self": {"pos": "0,63,0", "lookingAt": "wheat@1,63,0"}
        }
        self.entry.getInventoryFull.return_value = {
            "ok": True,
            "slots": [{"item": "minecraft:wheat_seeds", "index": 0, "count": 3}],
        }
        self.entry.getBlocksAround.side_effect = lambda r: [
            {"position": ",".join(map(str, p)), "block": block}
            for p, block in self.world.items()
            if block != "air"
        ]
        self.entry.canPlaceBlock.return_value = {"canPlace": True}
        self.entry.canBreakBlock.return_value = True
        self.rules = Mock()
        self.rules.can_plant.return_value = True
        self.paths = {(0, 63, 0): 0, (1, 63, 0): 1, (3, 63, 0): 3}
        for name, value in (
            ("_gateway", self.gateway),
            ("NativeWorldRules", self.rules),
            ("_walk_paths", self.paths),
            ("interaction_manager", Mock()),
            ("on_client", True),
            ("_click_soil", {"ok": True}),
        ):
            p = patch("saruman.farming." + name, return_value=value)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        p = patch("saruman.farming._state", side_effect=self.state)
        p.start()
        self.addCleanup(p.stop)
        p = patch("saruman.farming.time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def state(self, rules, position):
        block = Mock()
        block.method_9825.return_value = position in self.ripe
        return self.world.get(position, "air"), Mock(), block

    def test_planting_is_confirmed_from_the_actual_crop_block(self):
        def sow(entry, cleanup, inventory, item, p):
            self.world[p] = "wheat"
            return {"ok": True}

        self._click_soil.side_effect = sow
        result = farming.farm_step("plant")
        self.assertEqual(result["status"], "planted")
        self.assertEqual(result["crop"], "wheat")
        self.on_client.assert_not_called()

    def test_no_visible_plant_is_not_reported_success_and_click_is_not_repeated(self):
        result = farming.farm_step("plant")
        self.assertEqual(result["status"], "interaction_unconfirmed")
        self._click_soil.assert_called_once()

    def test_only_native_mature_crop_is_harvested_and_drops_are_separate(self):
        self.world[1, 63, 0] = "wheat"
        self.ripe.add((1, 63, 0))

        def harvest(*args):
            self.world[1, 63, 0] = "air"
            return True

        self.on_client.side_effect = harvest
        result = farming.farm_step("harvest")
        self.assertEqual(result["status"], "harvested")
        self.assertEqual(result["collected"], {})
        self._click_soil.assert_not_called()

    def test_young_crops_and_unrelated_blocks_are_never_broken(self):
        for block in (
            "wheat",
            "carrots",
            "beetroots",
            "potatoes",
            "oak_sapling",
            "stone",
        ):
            self.world[1, 63, 0] = block
            self.assertEqual(farming.farm_step("harvest")["status"], "no_work")
        self.on_client.assert_not_called()

    def test_protected_mature_crop_is_left_intact(self):
        self.world[1, 63, 0] = "wheat"
        self.ripe.add((1, 63, 0))
        self.entry.canBreakBlock.return_value = False
        self.assertEqual(farming.farm_step("harvest")["status"], "no_work")
        self.on_client.assert_not_called()

    def test_no_seed_or_native_planting_rule_refusal_does_not_click(self):
        self.entry.getInventoryFull.return_value["slots"] = []
        self.assertEqual(farming.farm_step("plant")["status"], "no_work")
        self.entry.getInventoryFull.return_value["slots"] = [
            {"item": "minecraft:carrot", "count": 1}
        ]
        self.rules.can_plant.return_value = False
        self.assertEqual(farming.farm_step("plant")["status"], "no_work")
        self._click_soil.assert_not_called()

    def test_native_work_is_never_replaced_by_harvesting(self):
        self.entry.hasActiveTask.return_value = True
        self.assertEqual(farming.farm_step()["status"], "waiting_for_game_task")
        self.entry.getBlocksAround.assert_not_called()
        self.on_client.assert_not_called()

    def test_tilling_requires_hoe_water_and_empty_plain_ground(self):
        self.world = {(1, 62, 0): "grass_block", (2, 62, 0): "water"}
        self.assertEqual(farming.farm_step("plant")["status"], "needs_materials")
        self.entry.getInventoryFull.return_value["slots"].append(
            {"item": "minecraft:wooden_hoe", "count": 1}
        )

        def till(entry, cleanup, inventory, item, p):
            self.assertEqual(item, "wooden_hoe")
            self.world[p[0], p[1] - 1, p[2]] = "farmland"
            return {"ok": True}

        self._click_soil.side_effect = till
        self.assertEqual(farming.farm_step("plant")["status"], "tilled")

    def test_seed_exhaustion_never_recycles_recent_plantings(self):
        def sow(entry, cleanup, inventory, item, p):
            self.world[p] = "wheat"
            self.entry.getInventoryFull.return_value["slots"][0]["count"] -= 1
            return {"ok": True}

        self.entry.getInventoryFull.return_value["slots"][0]["count"] = 2
        self._click_soil.side_effect = sow
        self.assertEqual(farming.farm_step()["status"], "planted")
        self.assertEqual(farming.farm_step()["status"], "planted")
        planted = dict(self.world)
        for mode in ("auto", "plant", "harvest", "auto"):
            result = farming.farm_step(mode)
            self.assertEqual(result["status"], "waiting_for_growth")
            self.assertNotIn("next_action", result)
        self.assertEqual(self.world, planted)
        self.on_client.assert_not_called()
        self.assertEqual(self._click_soil.call_count, 2)

    def test_empty_inventory_plant_request_gets_seeds_from_mature_crop(self):
        self.entry.getInventoryFull.return_value["slots"] = []
        self.world[1, 63, 0] = "wheat"
        self.ripe.add((1, 63, 0))

        def harvest(*args):
            self.world[1, 63, 0] = "air"
            self.entry.getInventoryFull.return_value["slots"] = [
                {"item": "minecraft:wheat_seeds", "count": 2, "index": 0},
                {"item": "minecraft:wheat", "count": 1, "index": 1},
            ]
            return True

        self.on_client.side_effect = harvest
        result = farming.farm_step("plant")
        self.assertEqual(result["status"], "harvested")
        self.assertEqual(result["collected"]["minecraft:wheat_seeds"], 2)
        self._click_soil.assert_not_called()

    def test_maturity_is_rechecked_after_aim_before_breaking(self):
        self.world[1, 63, 0] = "wheat"
        self.ripe.add((1, 63, 0))
        self.entry.lookAt.side_effect = lambda *args: self.ripe.clear() or True
        farming.farm_step("harvest")
        self.on_client.assert_not_called()

    def test_harvest_supplies_replanting_then_leaves_new_crops_to_grow(self):
        self.entry.getInventoryFull.return_value["slots"] = []
        self.world[0, 63, 0] = "wheat"  # Our existing young plant stays intact.
        self.world[1, 63, 0] = "wheat"
        self.ripe.add((1, 63, 0))

        def harvest(*args):
            self.world[1, 63, 0] = "air"
            self.ripe.clear()
            self.entry.getInventoryFull.return_value["slots"] = [
                {"item": "minecraft:wheat_seeds", "count": 2, "index": 0}
            ]
            return True

        def sow(entry, cleanup, inventory, item, p):
            self.world[p] = "wheat"
            self.entry.getInventoryFull.return_value["slots"][0]["count"] -= 1
            return {"ok": True}

        self.on_client.side_effect = harvest
        self._click_soil.side_effect = sow
        self.assertEqual(farming.farm_step()["status"], "harvested")
        self.assertEqual(farming.farm_step()["status"], "planted")
        for _ in range(3):
            self.assertEqual(farming.farm_step()["status"], "waiting_for_growth")
        self.on_client.assert_called_once()
        self._click_soil.assert_called_once()
        self.assertEqual(self.world[0, 63, 0], "wheat")
        self.assertEqual(self.world[1, 63, 0], "wheat")


class CropPolicyTests(unittest.TestCase):
    def test_raw_native_crop_commands_cannot_bypass_maturity_checks(self):
        for command in (
            "@get wheat_seeds 4",
            "$get minecraft:beetroot_seeds 2",
            "@get wheat 3",
            "@get carrot 1",
            "@get potato 1",
            "@get beetroot 1",
            "@mineblock wheat",
            "@mineblock carrots",
            "@mineblock farmland",
            "@place wheat_seeds",
            "@place minecraft:potato",
        ):
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(ValueError, "farm_crops"),
            ):
                guard_command(command)
        for command in ("@get wooden_hoe 1", "@get oak_sapling 2", "@goto 1 63 2"):
            guard_command(command)

    def test_bread_uses_already_harvested_wheat_for_only_missing_loaves(self):
        inventory = {
            "items": [
                {"item": "minecraft:wheat", "count": 3},
                {"item": "minecraft:bread", "count": 1},
            ]
        }
        guard_crop_inputs("@get bread 2", inventory)
        with self.assertRaisesRegex(ValueError, "already harvested wheat"):
            guard_crop_inputs("@get bread 3", inventory)
        with self.assertRaises(ValueError):
            guard_crop_inputs("@get bread 1", {"slots": []})


if __name__ == "__main__":
    unittest.main()
