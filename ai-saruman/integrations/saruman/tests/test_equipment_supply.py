"""Permissions, advance reserves and observed self grants without live commands."""

import unittest
from unittest.mock import Mock

from saruman.equipment_supply import STAFF, command_capabilities, prepare


class EquipmentSupplyTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock()
        self.gateway.entry_point.getUsername.return_value = "saruman_mag"
        self.gateway.jvm.java.lang.System.identityHashCode.return_value = 7
        self.player = self.gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getPlayer.return_value
        self.player.method_56992.return_value = False
        self.xp = self.gateway.jvm.io.github.pistonpoek.magicalscepter.util.PlayerExperience.getTotalExperience
        self.xp.return_value = 0
        self.root = self.gateway.jvm.net.minecraft.class_310.method_1551.return_value.method_1562.return_value.method_2886.return_value.getRoot.return_value
        self.commands = {"give", "experience"}
        self.root.getChild.side_effect = lambda name: (
            object() if name in self.commands else None
        )
        self.game, self.inventory, self.staff = {}, {"ok": True, "slots": []}, []

    def check(self, now=100, magic=True):
        return prepare(self.gateway, self.game, self.inventory, self.staff, magic, now)

    def equipped(self):
        self.inventory["slots"] = [
            {"item": "minecraft:diamond_sword", "count": 1, "index": 0},
            {"item": "minecraft:shield", "count": 1, "index": 40},
            {"item": STAFF, "count": 1, "index": 1},
        ]
        self.staff = [{"experience_cost": 4, "enough_experience": True}]
        self.xp.return_value = 100

    def test_permissions_are_read_without_command_probes_including_namespaced_aliases(
        self,
    ):
        self.assertEqual(
            command_capabilities(self.gateway),
            {"give": "give", "experience": "experience", "verified": True},
        )
        self.commands = {"minecraft:give", "minecraft:xp"}
        result = command_capabilities(self.gateway)
        self.assertEqual(result["give"], "minecraft:give")
        self.assertEqual(result["experience"], "minecraft:xp")
        self.gateway.entry_point.ChatMessage.assert_not_called()

    def test_full_kit_requests_only_self_and_requires_inventory_xp_confirmation(self):
        result = self.check()
        self.assertTrue(result["preflight_required"])
        issued = [
            call.args[0] for call in self.gateway.entry_point.ChatMessage.call_args_list
        ]
        self.assertEqual(
            issued,
            [
                "/give saruman_mag minecraft:iron_sword 1",
                "/give saruman_mag minecraft:shield 1",
                '/give saruman_mag magicalscepter:magical_scepter[magicalscepter:scepter_contents="magicalscepter:magical"] 1',
                "/experience add saruman_mag 100 points",
            ],
        )
        self.assertFalse(result["completion_confirmed"])
        self.check(now=102)
        self.assertEqual(self.gateway.entry_point.ChatMessage.call_count, 4)
        self.equipped()
        result = self.check(now=103)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["completion_confirmed"])
        self.assertFalse(result["preflight_required"])
        self.assertEqual(self.gateway.entry_point.ChatMessage.call_count, 4)

    def test_no_commands_means_sword_only_even_when_the_mod_and_materials_exist(self):
        self.commands = set()
        result = self.check()
        self.assertEqual(result["mode"], "sword_only")
        self.assertEqual(
            result["next_action"]["arguments"], {"item": "wooden_sword", "count": 1}
        )
        self.assertEqual(set(result["observed"]), {"sword"})
        self.gateway.entry_point.ChatMessage.assert_not_called()
        self.xp.assert_not_called()
        self.inventory["slots"] = [
            {"item": "minecraft:wooden_sword", "count": 1, "index": 0}
        ]
        self.assertEqual(self.check()["status"], "sword_ready")

    def test_partial_permissions_do_not_request_a_staff_shield_or_xp(self):
        self.commands = {"give"}
        self.check()
        self.gateway.entry_point.ChatMessage.assert_called_once_with(
            "/give saruman_mag minecraft:iron_sword 1"
        )
        self.xp.assert_not_called()

    def test_server_without_staff_mod_never_requests_mod_item_or_experience(self):
        result = self.check(magic=False)
        issued = [
            call.args[0] for call in self.gateway.entry_point.ChatMessage.call_args_list
        ]
        self.assertEqual(len(issued), 2)
        self.assertFalse(
            any("magicalscepter" in text or "experience" in text for text in issued)
        )
        self.xp.assert_not_called()
        self.assertNotIn("staff", result["observed"])

    def test_reserve_is_replenished_before_zero_but_not_after_every_cast(self):
        self.equipped()
        self.xp.return_value = 24
        self.assertEqual(self.check()["status"], "ready")
        self.gateway.entry_point.ChatMessage.assert_not_called()
        self.xp.return_value = 16
        result = self.check(now=101)
        self.assertTrue(result["preflight_required"])
        self.gateway.entry_point.ChatMessage.assert_called_once_with(
            "/experience add saruman_mag 84 points"
        )

    def test_unconfirmed_grants_back_off_and_do_not_hold_all_work_forever(self):
        self.check()
        result = self.check(now=105)
        self.assertEqual(result["status"], "equipment_unconfirmed")
        self.assertFalse(result["preflight_required"])
        self.assertFalse(result["completion_confirmed"])
        self.check(now=159)
        self.assertEqual(self.gateway.entry_point.ChatMessage.call_count, 4)
        self.check(now=160)
        self.assertEqual(self.gateway.entry_point.ChatMessage.call_count, 8)

    def test_world_change_or_observed_item_loss_renews_only_needed_requests(self):
        self.check()
        self.equipped()
        self.check(now=101)
        self.inventory["slots"] = [
            row for row in self.inventory["slots"] if row["item"] != "minecraft:shield"
        ]
        self.check(now=102)
        self.assertEqual(self.gateway.entry_point.ChatMessage.call_count, 5)
        self.assertEqual(
            self.gateway.entry_point.ChatMessage.call_args.args[0],
            "/give saruman_mag minecraft:shield 1",
        )
        self.gateway.jvm.java.lang.System.identityHashCode.return_value = 8
        self.check(now=103)
        self.assertEqual(self.gateway.entry_point.ChatMessage.call_count, 6)

    def test_unknown_permissions_invalid_inventory_or_username_never_dispatches(self):
        self.root.getChild.side_effect = OSError("command tree unavailable")
        self.check()
        self.gateway.entry_point.ChatMessage.assert_not_called()
        self.root.getChild.side_effect = lambda name: object()
        self.inventory["ok"] = False
        self.check()
        self.gateway.entry_point.ChatMessage.assert_not_called()
        self.inventory["ok"] = True
        self.gateway.entry_point.getUsername.return_value = "bad name; give Alex"
        self.check()
        self.gateway.entry_point.ChatMessage.assert_not_called()

    def test_permission_loss_removes_full_kit_wait_immediately(self):
        self.check()
        self.commands = set()
        result = self.check(now=101)
        self.assertEqual(result["mode"], "sword_only")
        self.assertFalse(result["preflight_required"])
        self.assertEqual(set(result["observed"]), {"sword"})
        self.assertEqual(self.gateway.entry_point.ChatMessage.call_count, 4)

    def test_transport_failure_reserves_request_and_never_replays_on_next_check(self):
        self.equipped()
        self.xp.return_value = 0
        self.gateway.entry_point.ChatMessage.side_effect = OSError("unknown delivery")
        self.assertFalse(self.check()["preflight_required"])
        self.assertFalse(self.check(now=101)["preflight_required"])
        self.gateway.entry_point.ChatMessage.assert_called_once()
