"""Native staff actions are faked; no real creature or inventory is modified."""

import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from saruman import scepter
from saruman.combat_policy import permitted, record_native_damage, spell_clear


def target(kind="minecraft:zombie", uuid="zombie", x=3):
    return {"type": kind, "uuid": uuid, "position": [x, 63, 0], "health": 20}


class PolicyTests(unittest.TestCase):
    def test_native_hostile_classification_never_overrides_protected_types_or_animals(
        self,
    ):
        self.assertTrue(permitted({**target("mod:monster"), "native_hostile": True}))
        self.assertFalse(
            permitted(
                {**target("mod:pet"), "native_hostile": True, "protected_native": True}
            )
        )
        self.assertFalse(
            permitted({**target("minecraft:iron_golem"), "native_hostile": True})
        )
        self.assertFalse(
            permitted({**target("minecraft:enderman"), "native_hostile": True})
        )

    def test_protected_types_stay_protected_even_with_damage_evidence(self):
        proof = {"victim": {"source": "native_damage", "until": 200}}
        for kind in (
            "villager",
            "iron_golem",
            "snow_golem",
            "cow",
            "pig",
            "wolf",
            "bee",
            "armor_stand",
            "wandering_trader",
        ):
            self.assertFalse(
                permitted(target("minecraft:" + kind, "victim"), proof, now=100)
            )
        self.assertFalse(permitted(target("othermod:zombie", "victim"), proof, now=100))

    def test_players_need_recent_native_proof_and_hostiles_do_not(self):
        player = target("minecraft:player", "alex")
        self.assertFalse(permitted(player, now=100))
        self.assertFalse(
            permitted(player, {"alex": {"source": "chat", "until": 200}}, now=100)
        )
        self.assertTrue(
            permitted(
                player, {"alex": {"source": "native_damage", "until": 200}}, now=100
            )
        )
        self.assertFalse(
            permitted(
                player, {"alex": {"source": "native_damage", "until": 99}}, now=100
            )
        )
        self.assertTrue(permitted(target()))
        self.assertTrue(permitted(target("magicalscepter:sorcerer")))

    def test_spells_protect_bystanders_and_area_or_projectile_requires_no_bystanders(
        self,
    ):
        origin, aim = [0, 64, 0], [5, 64, 0]
        villager = target("minecraft:villager", "v", 2)
        ray = scepter.SPELLS["magicalscepter:magical_attack"]
        self.assertFalse(spell_clear(ray, origin, aim, [(None, villager)]))
        villager["position"][2] = 10
        self.assertTrue(spell_clear(ray, origin, aim, [(None, villager)]))
        self.assertFalse(
            spell_clear(
                scepter.SPELLS["magicalscepter:ghast_fireball"],
                origin,
                aim,
                [(None, villager)],
            )
        )
        self.assertTrue(
            spell_clear(
                scepter.SPELLS["magicalscepter:magical_resistance"],
                origin,
                aim,
                [(None, villager)],
            )
        )

    def test_native_hit_only_records_self_damage_and_rejects_stale_attacker(self):
        gateway = Mock()
        gateway.entry_point.getUsername.return_value = "saruman-mag"
        player = gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getPlayer.return_value
        attacker = player.method_6065.return_value
        attacker.method_5477.return_value.getString.return_value = "Alex"
        attacker.method_5845.return_value = "alex"
        event = {"victim": "saruman-mag", "attacker": "Alex", "amount": 2}
        game = {}
        self.assertTrue(record_native_damage(gateway, game, event, now=100))
        self.assertTrue(
            permitted(
                target("minecraft:player", "alex"), game["combat_threats"], now=101
            )
        )
        for change in (
            {"victim": "Other"},
            {"amount": 0},
            {"amount": float("nan")},
            {"attacker": "unknown"},
            {"attacker": "Bob"},
        ):
            self.assertFalse(
                record_native_damage(gateway, game, {**event, **change}, now=101)
            )


class DefenseTests(unittest.TestCase):
    def test_defense_stops_old_work_once_preserves_goal_and_protects_before_attack(
        self,
    ):
        from saruman import hunting

        game = {
            "alive": True,
            "combat_threats": {"alex": {"source": "native_damage", "until": 200}},
            "tree_task": {
                "id": "tree",
                "status": "active",
                "config": {"count": 2},
                "targets": [{"position": [1, 63, 2]}],
            },
        }
        gateway = Mock()
        gateway.entry_point.hasActiveTask.return_value = True
        attacker = {**target("minecraft:player", "alex"), "distance": 2.5}
        with (
            patch.object(hunting, "_gateway", return_value=gateway),
            patch.object(hunting.time, "time", return_value=100),
            patch.object(
                hunting,
                "scan_hostiles",
                return_value={"status": "observed", "targets": [attacker]},
            ),
            patch.object(
                hunting, "attack_hostile_at", return_value={"status": "cast_requested"}
            ) as attack,
            patch.object(
                scepter, "cast", return_value={"status": "cast_requested"}
            ) as protect,
        ):
            self.assertEqual(
                hunting.defend_step(game)["status"], "stopping_for_defense"
            )
            hunting.defend_step(game)
            gateway.entry_point.stopPathing.assert_called_once()
            self.assertEqual(game["tree_task"]["status"], "paused")
            self.assertEqual(
                game["deferred_goals"][0]["task"]["targets"], [{"position": [1, 63, 2]}]
            )
            attack.assert_not_called()
            gateway.entry_point.hasActiveTask.return_value = False
            hunting.defend_step(game)
            protect.assert_called_once_with(game, "protect")
            hunting.defend_step(game)
            attack.assert_called_once_with("alex", game, emergency=True)
            game["combat_threats"] = {}
            self.assertEqual(hunting.defend_step(game)["status"], "no_threat")
            self.assertFalse(game["self_defense"]["active"])


class StaffTests(unittest.TestCase):
    def test_cast_confirmation_requires_actual_observed_zero_health(self):
        self.entity.method_6032.side_effect = [20, 0]
        result = scepter.cast(self.game, "attack", "zombie")
        self.assertEqual(result["status"], "killed")
        self.assertTrue(result["damage_confirmed"])

    def test_bystander_appearing_during_aim_cancels_the_cast(self):
        self.started[3].side_effect = [
            list(self.entities),
            self.entities + [(Mock(), target("minecraft:cow", "cow", 2))],
        ]
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"],
            "protected_entity_near_spell",
        )
        self.invoke.assert_not_called()

    def test_empty_experience_is_prepared_by_verified_command_permissions(self):
        self.staff["enough_experience"] = False
        self.xp.return_value = 0
        result = scepter.ensure(self.game)
        self.assertNotIn("next_action", result)
        self.entry.ChatMessage.assert_called_once_with(
            "/experience add saruman-mag 100 points"
        )
        self.assertEqual(result["status"], "equipment_preparing")
        self.assertFalse(result["completion_confirmed"])

    def test_xp_is_not_assumed_or_spammed_and_native_confirmation_clears_pending(self):
        self.staff["enough_experience"] = False
        self.xp.return_value = 0
        scepter.ensure(self.game, now=100)
        self.assertEqual(
            scepter.ensure(self.game, now=102)["status"], "equipment_preparing"
        )
        self.assertEqual(
            scepter.ensure(self.game, now=105)["status"], "equipment_unconfirmed"
        )
        self.entry.ChatMessage.assert_called_once()
        self.staff["enough_experience"] = True
        self.xp.return_value = 100
        self.assertEqual(scepter.ensure(self.game, now=110)["status"], "ready")
        self.assertEqual(self.game["scepter_experience"]["status"], "ready")

    def test_combat_never_spawns_experience_and_preflight_grants_back_off(self):
        self.staff["enough_experience"] = False
        self.xp.return_value = 0
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"],
            "insufficient_experience",
        )
        self.entry.ChatMessage.assert_not_called()
        scepter.ensure(self.game, now=100)
        scepter.ensure(self.game, now=159)
        self.entry.ChatMessage.assert_called_once()
        self.assertEqual(
            scepter.ensure(self.game, now=160)["status"], "equipment_preparing"
        )
        self.assertEqual(self.entry.ChatMessage.call_count, 2)
        self.invoke.assert_not_called()

    def test_uncertain_xp_delivery_is_reserved_before_dispatch(self):
        self.staff["enough_experience"] = False
        self.xp.return_value = 0
        self.entry.ChatMessage.side_effect = OSError("delivery unknown")
        scepter.ensure(self.game, now=100)
        self.entry.ChatMessage.side_effect = None
        self.assertEqual(
            scepter.ensure(self.game, now=105)["status"], "equipment_unconfirmed"
        )
        self.entry.ChatMessage.assert_called_once()

    def test_no_xp_command_for_healthy_staff_death_or_missing_server_mod(self):
        scepter.ensure(self.game)
        self.staff["enough_experience"] = False
        self.game["alive"] = False
        scepter.ensure(self.game)
        self.game["alive"] = True
        self.started[1].return_value = False
        scepter.ensure(self.game)
        self.entry.ChatMessage.assert_not_called()

    def test_no_commands_needs_only_a_sword_not_staff_materials(self):
        self.started[2].side_effect = None
        self.started[2].return_value = []
        self.inventory["slots"] = [{"item": scepter.BLANK, "index": 0, "count": 1}]
        with patch(
            "saruman.equipment_supply.command_capabilities",
            return_value={"give": None, "experience": None, "verified": True},
        ):
            result = scepter.ensure(self.game, now=100)
        self.assertEqual(
            result["next_action"]["arguments"], {"item": "wooden_sword", "count": 1}
        )
        self.entry.ChatMessage.assert_not_called()

    def test_native_death_or_active_defense_prevents_preflight_grants(self):
        self.xp.return_value = 0
        self.player.method_6032.return_value = 0
        self.assertEqual(scepter.ensure(self.game)["status"], "waiting_for_survival")
        self.player.method_6032.return_value = 20
        self.game["self_defense"] = {"active": True}
        self.assertEqual(
            scepter.ensure(self.game)["status"], "waiting_for_self_defense"
        )
        self.entry.ChatMessage.assert_not_called()

    def test_unconfigured_staff_requires_a_real_spell_component_to_confirm_a_grant(
        self,
    ):
        self.started[2].side_effect = None
        self.started[2].return_value = []
        result = scepter.ensure(self.game, now=100)
        self.assertTrue(result["preflight_required"])
        self.assertFalse(result["observed"]["staff"])
        self.assertIn("scepter_contents", self.entry.ChatMessage.call_args.args[0])

    def test_no_command_policy_never_auto_crafts_a_staff_even_with_recipe_materials(
        self,
    ):
        self.started[2].side_effect = None
        self.started[2].return_value = []
        self.inventory["slots"] += [
            {"item": item, "index": index + 3, "count": 1}
            for index, item in enumerate(scepter.MATERIALS)
        ]
        with (
            patch(
                "saruman.equipment_supply.command_capabilities",
                return_value={"give": None, "experience": None, "verified": True},
            ),
            patch.object(scepter, "_craft") as craft,
        ):
            self.assertEqual(
                scepter.ensure(self.game, now=100, allow_crafting=False)["status"],
                "sword_ready",
            )
            self.assertEqual(
                scepter.ensure(self.game, now=100, allow_crafting=True)["status"],
                "sword_ready",
            )
            craft.assert_not_called()

    def setUp(self):
        self.gateway = Mock()
        self.entry = self.gateway.entry_point
        self.entry.inGame.return_value = True
        self.entry.hasActiveTask.return_value = False
        self.entry.getUsername.return_value = "saruman-mag"
        self.entry.closeOpenScreen.return_value = True
        self.entry.lookAt.return_value = True
        self.entry.getYaw.return_value = -90
        self.entry.getPitch.return_value = 0
        self.entry.useHeldItem.return_value = {"ok": True, "result": "SUCCESS"}
        self.inventory = {
            "ok": True,
            "selectedHotbar": 0,
            "slots": [
                {"item": scepter.ITEM, "count": 1, "index": 0},
                {"item": "minecraft:iron_sword", "count": 1, "index": 1},
                {"item": "minecraft:shield", "count": 1, "index": 2},
            ],
        }
        self.entry.getInventoryFull.side_effect = lambda: deepcopy(self.inventory)
        self.player = self.gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getPlayer.return_value
        self.player.method_23317.return_value = 0
        self.player.method_23320.return_value = 64
        self.player.method_23321.return_value = 0
        self.player.method_6032.return_value = 20
        self.player.method_6057.return_value = True
        self.player.method_56992.return_value = False
        self.gateway.jvm.java.lang.System.identityHashCode.return_value = 7
        self.xp = self.gateway.jvm.io.github.pistonpoek.magicalscepter.util.PlayerExperience.getTotalExperience
        self.xp.return_value = 100
        self.entity = Mock()
        self.entity.method_23320.return_value = 64
        self.entity.method_6032.return_value = 20
        self.entities = [(self.entity, target())]
        self.staff = {
            "index": 0,
            "item": scepter.ITEM,
            "attack": "magicalscepter:magical_attack",
            "protect": "magicalscepter:magical_resistance",
            "enough_experience": True,
            "experience_cost": 4,
            "cooldown": False,
            "damage": 0,
            "max_damage": 250,
        }
        self.game = {"alive": True}
        self.patches = [
            patch.object(scepter, "_gateway", return_value=self.gateway),
            patch.object(scepter, "available", return_value=True),
            patch.object(
                scepter, "describe", side_effect=lambda *args: [dict(self.staff)]
            ),
            patch.object(
                scepter, "living_entities", side_effect=lambda _: list(self.entities)
            ),
            patch.object(scepter, "select_inventory_item"),
            patch.object(scepter, "on_client", return_value=True),
            patch.object(scepter.time, "time", return_value=100),
            patch.object(scepter.time, "sleep"),
            patch(
                "saruman.equipment_supply.command_capabilities",
                return_value={
                    "give": "give",
                    "experience": "experience",
                    "verified": True,
                },
            ),
            patch.object(scepter, "sync_selected_slot"),
        ]
        self.started = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.invoke = self.started[5]
        self.sync = self.started[-1]

    def test_staff_attack_uses_real_mod_callback_and_not_a_melee_or_raw_mouse_click(
        self,
    ):
        result = scepter.cast(self.game, "attack", "zombie")
        self.assertEqual(result["status"], "cast_requested")
        self.assertFalse(result["damage_confirmed"])
        self.assertFalse(result["killed"])
        self.assertEqual(self.invoke.call_args.args[3], "onClientPlayerPreAttack")
        self.assertEqual(self.invoke.call_args.args[4][-1], "int")
        self.sync.assert_called_once_with(self.gateway, 0)
        self.entry.mouseClick.assert_not_called()
        self.entry.RunInnerCommand.assert_not_called()
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"],
            "waiting_for_cooldown",
        )
        self.invoke.assert_called_once()

    def test_right_click_casts_actual_second_spell(self):
        result = scepter.cast(self.game, "protect")
        self.assertEqual(result["spell"], "magicalscepter:magical_resistance")
        self.entry.useHeldItem.assert_called_once()
        self.invoke.assert_not_called()
        self.sync.assert_not_called()

    def test_ranged_cast_synchronizes_equipment_before_native_mod_callback(self):
        ordered = Mock()
        ordered.attach_mock(self.sync, "sync")
        ordered.attach_mock(self.invoke, "attack")
        self.entities[0][1]["position"][0] = 7
        result = scepter.cast(self.game, "attack", "zombie")
        self.assertEqual(result["status"], "cast_requested")
        self.assertEqual([call[0] for call in ordered.mock_calls], ["sync", "attack"])
        self.assertEqual(self.game["scepter_attempt"]["target_uuid"], "zombie")

    def test_rejected_aim_is_visible_in_telemetry_and_sends_no_slot_or_cast_packet(
        self,
    ):
        self.entry.lookAt.return_value = False
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"], "aim_unconfirmed"
        )
        self.assertEqual(self.game["scepter_attempt"]["status"], "aim_unconfirmed")
        self.sync.assert_not_called()
        self.invoke.assert_not_called()

    def test_no_xp_cooldown_unknown_spell_or_aim_cannot_fire(self):
        for change, expected in (
            ({"enough_experience": False}, "insufficient_experience"),
            ({"cooldown": True}, "waiting_for_cooldown"),
            ({"attack": "mod:unknown"}, "unknown_spell"),
        ):
            original = dict(self.staff)
            self.staff.update(change)
            self.assertEqual(
                scepter.cast(self.game, "attack", "zombie")["status"], expected
            )
            self.staff = original
        self.entry.getYaw.return_value = 45
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"], "aim_unconfirmed"
        )
        self.invoke.assert_not_called()

    def test_protected_target_and_protected_bystander_cannot_be_hit(self):
        self.entities[0][1]["type"] = "minecraft:iron_golem"
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"],
            "target_forbidden_or_lost",
        )
        self.entities[0][1]["type"] = "minecraft:zombie"
        self.entities.append((Mock(), target("minecraft:cow", "cow", 2)))
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"],
            "protected_entity_near_spell",
        )
        self.invoke.assert_not_called()

    def test_player_can_only_be_targeted_after_a_verified_hit(self):
        self.entities[0][1]["type"] = "minecraft:player"
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"],
            "target_forbidden_or_lost",
        )
        self.game["combat_threats"] = {
            "zombie": {"source": "native_damage", "until": 110}
        }
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"], "cast_requested"
        )

    def test_all_twenty_spells_are_recognized_with_their_actual_native_actions(self):
        self.assertEqual(len(scepter.SPELLS), 20)
        for spell_id in scepter.SPELLS:
            with self.subTest(spell=spell_id):
                self.game.clear()
                self.staff["attack"] = self.staff["protect"] = spell_id
                action = "attack" if scepter.SPELLS[spell_id]["range"] else "protect"
                result = scepter.cast(
                    self.game, action, "zombie" if action == "attack" else None
                )
                self.assertEqual(result["status"], "cast_requested")

    def test_unknown_result_is_not_replayed(self):
        self.invoke.side_effect = TimeoutError("unknown cast")
        with self.assertRaises(TimeoutError):
            scepter.cast(self.game, "attack", "zombie")
        self.assertEqual(
            scepter.cast(self.game, "attack", "zombie")["status"],
            "interaction_unconfirmed",
        )
        self.invoke.assert_called_once()

    def test_give_is_only_attempted_once_and_has_an_explicit_spell_component(self):
        self.started[2].side_effect = None
        self.started[2].return_value = []
        self.inventory["slots"] = []
        self.assertEqual(
            scepter.ensure(self.game, now=100)["status"], "equipment_preparing"
        )
        commands = [call.args[0] for call in self.entry.ChatMessage.call_args_list]
        command = next(command for command in commands if scepter.ITEM in command)
        self.assertIn('scepter_contents="magicalscepter:magical"', command)
        self.assertIn("/give saruman-mag ", command)
        self.assertEqual(
            scepter.ensure(self.game, now=101)["status"], "equipment_preparing"
        )
        self.assertEqual(
            scepter.ensure(self.game, now=105)["status"], "equipment_unconfirmed"
        )
        self.assertEqual(self.entry.ChatMessage.call_count, 3)

    def test_unconfirmed_give_never_starts_staff_crafting_even_with_materials(
        self,
    ):
        self.started[2].side_effect = None
        self.started[2].return_value = []
        self.inventory["slots"] = [
            {"item": item, "count": 1, "index": i}
            for i, item in enumerate(scepter.MATERIALS)
        ]
        scepter.ensure(self.game, now=100)
        with patch.object(scepter, "_craft") as craft:
            self.assertEqual(
                scepter.ensure(self.game, now=105)["status"], "equipment_unconfirmed"
            )
            self.assertEqual(
                scepter.ensure(self.game, now=106)["status"], "equipment_unconfirmed"
            )
            self.assertEqual(
                scepter.ensure(self.game, now=140)["status"], "equipment_unconfirmed"
            )
            craft.assert_not_called()
        self.started[2].return_value = [dict(self.staff)]
        self.inventory["slots"] += [
            {"item": "minecraft:iron_sword", "count": 1, "index": 3},
            {"item": "minecraft:shield", "count": 1, "index": 4},
        ]
        self.assertEqual(scepter.ensure(self.game, now=141)["status"], "ready")

    def test_existing_usable_staff_does_not_get_duplicated_and_water_blocks_actions(
        self,
    ):
        self.assertEqual(scepter.ensure(self.game)["status"], "ready")
        self.entry.ChatMessage.assert_not_called()
        self.game["survival"] = {"active": True}
        self.assertEqual(
            scepter.cast(self.game, "protect")["status"], "waiting_for_survival"
        )
        self.invoke.assert_not_called()
