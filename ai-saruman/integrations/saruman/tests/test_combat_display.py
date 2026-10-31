"""Target HUD and equipment synchronization without a live game or chat."""

import unittest
from unittest.mock import Mock, patch

from saruman.combat_display import CombatDisplay, describe_combat
from saruman.world_actions import sync_selected_slot


class CombatDisplayTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.gateway = Mock()
        self.game = {
            "self_defense": {
                "active": True,
                "target": {"type": "minecraft:skeleton", "uuid": "sk1", "distance": 7},
                "result": {"status": "cast_requested", "damage_confirmed": False},
            }
        }

    def test_target_and_unconfirmed_hit_are_local_overlay_not_server_chat(self):
        hud = CombatDisplay(clock=lambda: self.now)
        with patch("saruman.combat_display.on_client") as invoke:
            hud.tick(self.gateway, self.game)
            self.assertIn("skeleton", self.game["combat_display"]["text"])
            self.assertIn("7.0", self.game["combat_display"]["text"])
            self.assertIn("не подтверждено", self.game["combat_display"]["text"])
            self.assertEqual(invoke.call_args.args[3], "method_7353")
            self.assertTrue(invoke.call_args.args[-1][-1])
            hud.tick(self.gateway, self.game)
            invoke.assert_called_once()
            self.game["self_defense"] = {"active": False, "status": "no_threat"}
            self.now += 1
            hud.tick(self.gateway, self.game)
            self.assertEqual(self.game["combat_display"]["status"], "cleared")
        self.gateway.entry_point.ChatMessage.assert_not_called()

    def test_overlay_failure_cannot_break_defense_and_damage_requires_observation(self):
        hud = CombatDisplay(clock=lambda: self.now)
        with patch(
            "saruman.combat_display.on_client", side_effect=OSError("HUD failure")
        ):
            hud.tick(self.gateway, self.game)
        self.assertEqual(self.game["combat_display"]["status"], "unavailable")
        self.assertTrue(self.game["self_defense"]["active"])
        self.game["self_defense"]["result"].update(
            damage_confirmed=True, health_before=20, health_after=16
        )
        self.assertIn("урон 4.0", describe_combat(self.game))

    def test_selected_slot_packet_uses_actual_connection_and_confirmed_hotbar(self):
        with patch("saruman.world_actions.on_client") as invoke:
            sync_selected_slot(self.gateway, 4)
            self.gateway.jvm.net.minecraft.class_2868.assert_called_once_with(4)
            self.assertEqual(invoke.call_args.args[3], "method_52787")
            self.assertEqual(
                invoke.call_args.args[1],
                self.gateway.jvm.net.minecraft.class_310.method_1551().method_1562(),
            )
            with self.assertRaises(ValueError):
                sync_selected_slot(self.gateway, 9)
            invoke.assert_called_once()
