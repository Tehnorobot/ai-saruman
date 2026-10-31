"""Emergency behavior uses fake worlds, clocks and native calls; no live attacks."""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from saruman import hunting, scepter
from saruman.combat_reflex import CombatReflex
from saruman.survival import nearest_dry_shore, swim_paths
from tests.fake_world_rules import FakeWorldRules


class SwimmingRules:
    def __init__(self, shore=True):
        self.shore = shore

    def free(self, p, allow_water=False):
        x, y, z = p
        if abs(x) > 4 or abs(z) > 4:
            return False
        if self.shore and x >= 2 and y <= 62:
            return False
        return y >= 64 or allow_water and y in (62, 63)

    def support(self, p):
        return self.shore and p[0] >= 2 and p[1] == 62


class SwimmingRouteTests(unittest.TestCase):
    def test_fractional_submerged_start_finds_land_without_standing_at_start(self):
        rules = SwimmingRules()
        paths = swim_paths(rules, (0, 62, 0))
        self.assertIn((2, 63, 0), paths)
        self.assertNotIn((2, 62, 0), paths)  # Solid bank, not swimming through terrain.

    def test_no_shore_moves_towards_observed_frontier_and_avoids_failed_first_step(
        self,
    ):
        gateway = Mock()
        gateway.entry_point.getGameState.return_value = {
            "self": {"pos": "0.4,63.6,0.4"}
        }
        gateway.entry_point.getBlocksAround.return_value = []
        with patch(
            "saruman.world_rules.NativeWorldRules", return_value=SwimmingRules(False)
        ):
            first = nearest_dry_shore(gateway, set())
            self.assertIsNotNone(first)
            other = nearest_dry_shore(gateway, {first})
            self.assertIsNotNone(other)
            self.assertNotEqual(first, other)
        gateway.entry_point.RunInnerCommand.assert_not_called()


class ServerCapabilityTests(unittest.TestCase):
    def test_client_mod_is_not_server_capability(self):
        gateway = Mock()
        loader = (
            gateway.jvm.net.fabricmc.loader.api.FabricLoader.getInstance.return_value
        )
        network = gateway.jvm.net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking
        loader.isModLoaded.return_value = True
        network.canSend.return_value = False
        self.assertFalse(scepter.available(gateway))
        network.canSend.return_value = True
        self.assertTrue(scepter.available(gateway))
        loader.isModLoaded.return_value = False
        network.canSend.reset_mock()
        self.assertFalse(scepter.available(gateway))
        network.canSend.assert_not_called()

    def test_missing_server_mod_gets_sword_without_give_or_mod_recipe(self):
        gateway, game = Mock(), {"alive": True}
        gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getPlayer.return_value.method_6032.return_value = 20
        gateway.jvm.java.lang.System.identityHashCode.return_value = 7
        gateway.entry_point.getInventoryFull.return_value = {"ok": True, "slots": []}
        with (
            patch.object(scepter, "_gateway", return_value=gateway),
            patch.object(scepter, "available", return_value=False),
            patch.object(scepter, "_craft") as craft,
            patch(
                "saruman.equipment_supply.command_capabilities",
                return_value={"give": None, "experience": None, "verified": True},
            ),
        ):
            result = scepter.ensure(game)
            self.assertEqual(result["next_action"]["arguments"]["item"], "wooden_sword")
            gateway.entry_point.getInventoryFull.return_value["slots"] = [
                {"item": "minecraft:iron_sword", "index": 0, "count": 1}
            ]
            self.assertEqual(scepter.ensure(game)["status"], "sword_ready")
            self.assertEqual(
                scepter.cast(game, "attack", "zombie")["status"], "mod_unavailable"
            )
            gateway.entry_point.ChatMessage.assert_not_called()
            craft.assert_not_called()


class ReflexTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.gateway, self.nav = Mock(), Mock()
        self.nav.lock = threading.RLock()
        self.game = {
            "alive": True,
            "combat_controls": {"defend_self": True, "cast_scepter": False},
        }
        self.controller = CombatReflex(clock=lambda: self.now)
        self.setter = patch("saruman.combat_reflex.set_bool_on_client").start()
        self.defend = patch(
            "saruman.combat_reflex.defend_step", return_value={"status": "attacked"}
        ).start()
        self.addCleanup(patch.stopall)

    def tick(self, enabled=True):
        self.controller.tick(self.gateway, self.game, self.nav, enabled=enabled)

    def test_bridge_fights_without_chat_events_or_model_calls_and_has_one_owner(self):
        self.tick()
        self.defend.assert_called_once_with(
            self.game, self.nav, before_stop=self.controller._capture
        )
        self.assertEqual(self.game["self_defense"]["owner"], "bridge")
        self.assertFalse(self.game["scepter_enabled"])
        self.assertFalse(self.setter.call_args.args[-1])  # Native fleeing disabled.
        self.now = 0.1
        self.tick()
        self.defend.assert_called_once()

    def test_tool_controls_stop_death_and_water_are_respected(self):
        self.game["combat_controls"]["defend_self"] = False
        self.tick()
        self.defend.assert_not_called()
        self.now = 1
        self.game["combat_controls"]["defend_self"] = True
        self.game["survival"] = {"active": True}
        self.tick()
        self.defend.assert_not_called()
        self.now = 2
        self.game["survival"] = {}
        self.tick(False)
        self.defend.assert_not_called()
        self.assertIsNone(self.controller.original_defense)
        self.now = 3
        self.game["alive"] = False
        self.tick()
        self.defend.assert_not_called()

    def test_nearby_hostile_preempts_without_waiting_to_be_hit_then_approaches(self):
        game = {"alive": True}
        zombie = {
            "type": "minecraft:zombie",
            "uuid": "z",
            "position": [5, 63, 0],
            "distance": 5,
        }
        self.gateway.entry_point.hasActiveTask.return_value = True
        with (
            patch.object(hunting, "_gateway", return_value=self.gateway),
            patch.object(
                hunting,
                "scan_hostiles",
                return_value={"status": "observed", "targets": [zombie]},
            ),
            patch.object(
                hunting.scepter, "cast", return_value={"status": "mod_unavailable"}
            ),
            patch.object(
                hunting, "attack_hostile_at", return_value={"status": "out_of_reach"}
            ) as attack,
            patch.object(
                hunting, "tree_approach", return_value={"command": "@goto 3 63 0"}
            ),
        ):
            hunting.defend_step(game, self.nav)
            hunting.defend_step(game, self.nav)
            self.nav.interrupt_for_combat.assert_called_once()
            self.gateway.entry_point.hasActiveTask.return_value = False
            self.assertEqual(
                hunting.defend_step(game, self.nav)["status"], "approaching_threat"
            )
            attack.assert_called_once_with("z", game, emergency=True)
            self.nav.send.assert_called_once_with(
                self.gateway.entry_point, "@goto 3 63 0", game, purpose="combat"
            )
            self.gateway.entry_point.hasActiveTask.return_value = True
            self.assertEqual(
                hunting.defend_step(game, self.nav)["status"], "approaching_threat"
            )
            self.nav.interrupt_for_combat.assert_called_once()

    def test_player_without_damage_proof_and_protected_mobs_do_not_trigger_fight(self):
        with patch.object(hunting, "scan_hostiles") as scan:
            for kind in ("player", "iron_golem", "villager", "cow"):
                scan.return_value = {
                    "status": "observed",
                    "targets": [
                        {
                            "type": "minecraft:" + kind,
                            "uuid": "x",
                            "position": [1, 63, 0],
                            "distance": 1,
                        }
                    ],
                }
                self.assertEqual(
                    hunting.defend_step({"alive": True}, self.nav)["status"],
                    "no_threat",
                )
        self.nav.send.assert_not_called()

    def test_failed_route_records_reach_real_approach_planner_without_shape_errors(
        self,
    ):
        zombie = {
            "type": "minecraft:zombie",
            "uuid": "z",
            "position": [5, 63, 0],
            "distance": 5,
        }
        self.gateway.entry_point.hasActiveTask.return_value = False
        self.gateway.entry_point.getGameState.return_value = {
            "self": {"pos": "0.5,63,0.5"}
        }
        self.gateway.entry_point.getBlocksAround.return_value = [
            {"position": f"{x},62,{z}", "block": "grass block"}
            for x in range(-5, 6)
            for z in range(-5, 6)
        ]
        game = {
            "alive": True,
            "navigation": {
                "recent_failures": [
                    {
                        "target": [0, 63, 3],
                        "until": time.time() + 100,
                        "reason": "stalled",
                    }
                ]
            },
        }
        with (
            patch.object(hunting, "_gateway", return_value=self.gateway),
            patch("saruman.trees._gateway", return_value=self.gateway),
            patch("saruman.trees.NativeWorldRules", FakeWorldRules),
            patch.object(
                hunting,
                "scan_hostiles",
                return_value={"status": "observed", "targets": [zombie]},
            ),
            patch.object(
                hunting.scepter, "cast", return_value={"status": "mod_unavailable"}
            ),
            patch.object(
                hunting, "attack_hostile_at", return_value={"status": "out_of_reach"}
            ),
        ):
            self.assertEqual(
                hunting.defend_step(game, self.nav)["status"], "approaching_threat"
            )
            self.nav.send.assert_called_once()

    def test_confirmed_player_outside_twelve_blocks_is_not_chased(self):
        player = {
            "type": "minecraft:player",
            "uuid": "alex",
            "distance": 14,
            "position": [14, 63, 0],
        }
        game = {
            "alive": True,
            "combat_threats": {
                "alex": {"source": "native_damage", "until": time.time() + 30}
            },
            "self_defense": {"active": True, "target": player},
        }
        with patch.object(
            hunting,
            "scan_hostiles",
            return_value={"status": "observed", "targets": [player]},
        ):
            self.assertEqual(hunting.defend_step(game, self.nav)["status"], "no_threat")
        self.nav.send.assert_not_called()
