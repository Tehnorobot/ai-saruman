"""No real entities are attacked: classification and exact native calls use fakes."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from saruman import hunting
from saruman.peaceful import guard_command, hostile_type, install_native_guard


class HuntingTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock()
        self.entry = self.gateway.entry_point
        self.entry.inGame.return_value = True
        self.entry.hasActiveTask.return_value = False
        self.entry.closeOpenScreen.return_value = True
        self.entry.getGameState.return_value = {
            "self": {"pos": "0,63,0", "health": 20, "food": 20}
        }
        self.entry.getInventoryFull.return_value = {
            "ok": True,
            "selectedHotbar": 0,
            "slots": [{"item": "minecraft:iron_axe", "index": 0, "count": 1}],
        }
        self.mod = self.gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value
        self.player = self.mod.getPlayer.return_value
        self.player.method_5739.return_value = 2.0
        self.player.method_6057.return_value = True
        self.entity = Mock()
        self.entity.method_23320.return_value = 64.62
        self.entity.method_5864.return_value = "minecraft:zombie"
        self.entity.method_5845.return_value = "uuid-zombie"
        self.entity.method_5805.return_value = True
        self.entity.method_6032.return_value = 20
        for method, value in (
            ("method_23317", 2),
            ("method_23318", 63),
            ("method_23321", 0),
        ):
            getattr(self.entity, method).return_value = value
        self.mod.getEntityTracker.return_value.getAllTrackedEntities.return_value = [
            self.entity
        ]
        self.gateway.jvm.net.minecraft.class_7923.field_41177.method_10221.side_effect = (
            lambda t: t
        )
        for name, value in (
            ("_gateway", self.gateway),
            ("on_client", None),
            ("select_inventory_item", None),
            ("interaction_manager", Mock()),
        ):
            patcher = patch("saruman.hunting." + name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        timer = patch("saruman.hunting.time.sleep")
        timer.start()
        self.addCleanup(timer.stop)
        hunting._last_attack = 0

    def test_exact_native_entity_is_attacked_without_crosshair_click_or_pursuit_task(
        self,
    ):
        result = hunting.attack_hostile_at("uuid-zombie")
        self.assertEqual(result["status"], "attacked")
        self.assertFalse(result["killed"])
        self.assertEqual(
            self.on_client.call_args_list[0].args[-1], (self.player, self.entity)
        )
        self.assertEqual(self.on_client.call_args_list[1].args[3], "method_6104")
        self.entry.mouseClick.assert_not_called()
        self.entry.executeAgentActions.assert_not_called()
        self.entry.RunInnerCommand.assert_not_called()

    def test_peaceful_neutral_unknown_and_named_spoofs_cannot_be_attacked(self):
        for kind in (
            "villager",
            "wandering_trader",
            "cow",
            "sheep",
            "pig",
            "chicken",
            "iron_golem",
            "snow_golem",
            "player",
            "wolf",
            "enderman",
            "spider",
            "piglin",
            "zombified_piglin",
            "mod:zombie",
        ):
            with self.subTest(kind=kind):
                self.entity.method_5864.return_value = (
                    "minecraft:" + kind if ":" not in kind else kind
                )
                self.entity.method_5477.return_value = "Zombie"
                self.assertFalse(hunting.attack_hostile_at("uuid-zombie")["killed"])
        self.on_client.assert_not_called()

    def test_low_health_busy_missing_weapon_or_blocked_sight_cannot_attack(self):
        self.entry.hasActiveTask.return_value = True
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie")["status"], "waiting_for_game_task"
        )
        self.entry.hasActiveTask.return_value = False
        self.entry.getGameState.return_value["self"]["health"] = 5
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie")["status"], "unsafe_health"
        )
        self.entry.getGameState.return_value["self"]["health"] = 20
        self.player.method_6057.return_value = False
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie")["status"], "out_of_reach"
        )
        self.player.method_6057.return_value = True
        self.entry.getInventoryFull.return_value["slots"][0]["item"] = (
            "minecraft:flint_and_steel"
        )
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie")["status"], "missing_safe_weapon"
        )
        self.on_client.assert_not_called()

    def test_only_observed_zero_health_confirms_a_kill(self):
        self.on_client.side_effect = lambda *args: setattr(
            self.entity.method_6032, "return_value", 0
        )
        self.assertTrue(hunting.attack_hostile_at("uuid-zombie")["killed"])
        self.assertEqual(hunting.attack_hostile_at("missing")["status"], "target_lost")

    def test_real_gateway_hp_key_and_attack_cooldown(self):
        self.entry.getGameState.return_value["self"].pop("health")
        self.entry.getGameState.return_value["self"]["hp"] = 20
        self.assertEqual(hunting.attack_hostile_at("uuid-zombie")["status"], "attacked")
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie")["status"], "waiting_for_cooldown"
        )
        self.assertEqual(self.on_client.call_count, 2)

    def test_emergency_fights_at_low_health_but_never_after_death(self):
        self.entry.getGameState.return_value["self"].update(health=3, food=0)
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie", emergency=True)["status"],
            "attacked",
        )
        self.entry.getGameState.return_value["self"]["health"] = 0
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie", emergency=True)["status"],
            "waiting_for_respawn",
        )
        self.assertEqual(self.on_client.call_count, 2)

    def test_sword_is_preferred_and_sweeping_cannot_hit_nearby_peaceful_entities(self):
        self.entry.getInventoryFull.return_value["slots"].append(
            {"item": "minecraft:diamond_sword", "index": 1, "count": 1}
        )
        cow = {"type": "minecraft:cow", "uuid": "cow", "position": [2, 63, 1]}
        with patch.object(hunting, "living_entities", return_value=[]) as entities:
            self.assertEqual(
                hunting.attack_hostile_at("uuid-zombie")["status"], "attacked"
            )
            self.assertEqual(
                self.select_inventory_item.call_args.args[-1], "minecraft:diamond_sword"
            )
            entities.return_value = [(Mock(), cow)]
            hunting._last_attack = 0
            self.assertEqual(
                hunting.attack_hostile_at("uuid-zombie")["status"], "attacked"
            )
            self.assertEqual(
                self.select_inventory_item.call_args.args[-1], "minecraft:iron_axe"
            )
            self.entry.getInventoryFull.return_value["slots"] = (
                self.entry.getInventoryFull.return_value["slots"][1:]
            )
            self.on_client.reset_mock()
            self.assertEqual(
                hunting.attack_hostile_at("uuid-zombie")["status"],
                "protected_entity_near_sword",
            )
            self.on_client.assert_not_called()

    def test_emergency_can_defend_unarmed_while_sword_is_missing(self):
        self.entry.getInventoryFull.return_value["slots"] = []
        self.entry.selectHotbar.side_effect = lambda slot: (
            self.entry.getInventoryFull.return_value.update(selectedHotbar=slot) or True
        )
        self.assertEqual(
            hunting.attack_hostile_at("uuid-zombie", emergency=True)["status"],
            "attacked",
        )
        self.assertEqual(self.on_client.call_count, 2)

    def test_emergency_can_punch_the_threat_beside_a_villager_without_sword_sweep(self):
        self.entry.getInventoryFull.return_value["slots"] = [
            {"item": "minecraft:iron_sword", "index": 0, "count": 1}
        ]
        self.entry.selectHotbar.side_effect = lambda slot: (
            self.entry.getInventoryFull.return_value.update(selectedHotbar=slot) or True
        )
        villager = {"uuid": "v", "type": "minecraft:villager", "position": [2, 63, 1]}
        with patch.object(
            hunting, "living_entities", return_value=[(Mock(), villager)]
        ):
            self.assertEqual(
                hunting.attack_hostile_at("uuid-zombie", emergency=True)["status"],
                "attacked",
            )
        self.select_inventory_item.assert_not_called()
        self.assertEqual(
            self.on_client.call_args_list[0].args[-1], (self.player, self.entity)
        )

    def test_native_or_alias_combat_and_animal_collection_are_rejected(self):
        for command in (
            "@pursue Villager",
            "@t combat",
            "/kill @e",
            "@food 2",
            "@get beef 1",
            "@get white_wool 3",
            "@shoot Alex",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                guard_command(command)
        for command in ("@get bread 1", "@goto 1 63 2", "@get oak_sapling 2", "@stop"):
            guard_command(command)
        self.assertFalse(hostile_type("minecraft:iron_golem"))

    def test_unknown_native_result_is_not_replayed(self):
        self.on_client.side_effect = TimeoutError("unknown")
        with self.assertRaises(TimeoutError):
            hunting.attack_hostile_at("uuid-zombie")
        self.on_client.assert_called_once()

    def test_native_guard_covers_automatic_attacks_and_hidden_food_slaughter(self):
        gateway = MagicMock()
        behaviour = gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getBehaviour.return_value
        behaviour.getClass.return_value.getDeclaredField.return_value.get.return_value.toArray.return_value = [
            MagicMock()
        ]
        with (
            patch("saruman.world_actions.on_client") as invoke,
            patch("saruman.world_actions.set_int_on_client") as setter,
        ):
            result = install_native_guard(gateway)
        self.assertFalse(result["native_automatic_attacks"])
        self.assertFalse(result["native_food_collection"])
        self.assertEqual(
            [call.args[3] for call in invoke.call_args_list],
            [
                "add",
                "add",
                "addAttackExclusion",
                "addForceFieldExclusion",
            ],
        )
        settings = gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getModSettings.return_value
        self.assertEqual(
            setter.call_args_list,
            [
                unittest.mock.call(gateway, settings, "minimumFoodAllowed", 0),
                unittest.mock.call(gateway, settings, "foodUnitsToCollect", 0),
            ],
        )
        with self.assertRaises(ValueError):
            guard_command("@reload_settings")


if __name__ == "__main__":
    unittest.main()
