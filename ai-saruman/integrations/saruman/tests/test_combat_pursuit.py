"""Pursuit, rescue and checkpoint regressions, without live Minecraft/model calls."""

import threading
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from saruman import hunting
from saruman.combat_pursuit import pursuit_limit, watch_target, watched
from saruman.combat_reflex import CombatReflex
from saruman.navigation import NavigationGuard
from tests.fake_world_rules import FakeWorldRules


class PursuitPolicyTests(unittest.TestCase):
    def test_watch_expires_but_new_damage_and_visible_contact_react_immediately(self):
        target = {"uuid": "z", "position": [5, 63, 0], "distance": 5}
        game = {}
        watch_target(game, target, "no route", now=100)
        self.assertTrue(watched(game, target, now=101))
        self.assertFalse(watched(game, target, now=131))
        self.assertFalse(watched(game, {**target, "distance": 2}, now=101))
        self.assertTrue(
            watched(game, {**target, "distance": 2, "visible": False}, now=101)
        )
        game["combat_threats"] = {
            "z": {"source": "native_damage", "at": 102, "until": 120}
        }
        self.assertFalse(watched(game, target, now=103))
        watch_target(game, target, "still no route", now=103)
        self.assertTrue(
            watched(game, target, now=104)
        )  # Same hit is not a new trigger.

    def test_target_emerging_elsewhere_is_rechecked_and_watch_memory_is_bounded(self):
        game = {}
        target = {"uuid": "z", "position": [5, 63, 0], "distance": 5, "wet": True}
        watch_target(game, target, "water", now=100)
        moved = {**target, "position": [9, 63, 0]}
        self.assertTrue(watched(game, moved, now=101))
        self.assertFalse(watched(game, {**moved, "wet": False}, now=101))
        for index in range(40):
            watch_target(game, {**target, "uuid": str(index)}, "water", now=102)
        self.assertLessEqual(len(game["combat_watch"]), 32)

    def test_time_stall_and_leash_are_independent_and_progress_resets_only_stall(self):
        target = {"distance": 7}
        defense = {"anchor": [0, 63, 0], "pursuit_started_at": 100}
        self.assertIsNone(pursuit_limit(defense, target, [0, 63, 0], now=100))
        self.assertIsNone(pursuit_limit(defense, {"distance": 6}, [1, 63, 0], now=107))
        self.assertIn(
            "8 seconds", pursuit_limit(defense, {"distance": 6}, [1, 63, 0], now=115)
        )
        self.assertIn(
            "24 seconds", pursuit_limit(defense, {"distance": 2}, [5, 63, 0], now=124)
        )
        self.assertIn("12-block", pursuit_limit(defense, target, [13, 63, 0], now=108))


class DefensiveApproachTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.gateway, self.nav = Mock(), Mock()
        self.entry = self.gateway.entry_point
        self.entry.hasActiveTask.return_value = False
        self.entry.getGameState.return_value = {
            "self": {"pos": "0.5,63,0.5", "hp": 20, "food": 20}
        }
        self.entry.getBlocksAround.return_value = [
            {"position": f"{x},62,{z}", "block": "stone"}
            for x in range(-5, 6)
            for z in range(-5, 6)
        ]
        self.game = {"alive": True, "position": "0.5,63,0.5"}
        self.target = {
            "uuid": "z",
            "type": "minecraft:zombie",
            "position": [5.5, 63, 0.5],
            "distance": 5,
            "visible": True,
        }
        patches = [
            patch.object(hunting, "_gateway", return_value=self.gateway),
            patch("saruman.trees._gateway", return_value=self.gateway),
            patch("saruman.trees.NativeWorldRules", FakeWorldRules),
            patch.object(
                hunting,
                "scan_hostiles",
                side_effect=lambda **kw: {
                    "status": "observed",
                    "targets": [dict(self.target)],
                },
            ),
            patch.object(
                hunting.scepter, "cast", return_value={"status": "mod_unavailable"}
            ),
            patch.object(
                hunting, "attack_hostile_at", return_value={"status": "out_of_reach"}
            ),
            patch("saruman.hunting.time.time", side_effect=lambda: self.now),
        ]
        self.started = [p.start() for p in patches]
        self.attack = self.started[5]
        for p in patches:
            self.addCleanup(p.stop)

    def tick(self):
        return hunting.defend_step(self.game, self.nav)

    def test_real_observed_planner_dispatches_only_one_adjacent_dry_step(self):
        self.assertEqual(self.tick()["status"], "approaching_threat")
        args = self.nav.send.call_args
        self.assertEqual(args.args[1], "@goto 1 63 0")
        self.assertEqual(args.kwargs, {"purpose": "combat"})
        # A startup observation cannot issue the same route again.
        self.now += 0.5
        self.assertEqual(self.tick()["status"], "waiting_for_game_task")
        self.nav.send.assert_called_once()

    def test_water_target_is_attacked_from_shore_but_not_chased_if_out_of_reach(self):
        self.target.update(wet=True, submerged=True)
        self.assertEqual(self.tick()["status"], "watching_target")
        self.assertFalse(self.game["self_defense"]["active"])
        self.assertEqual(self.tick()["status"], "no_threat")
        self.attack.assert_called_once()
        self.nav.send.assert_not_called()
        # If it comes into a visible strike distance, defense is immediate.
        self.target["distance"] = 2
        self.attack.return_value = {"status": "attacked"}
        self.assertEqual(self.tick()["status"], "attacked")

    def test_available_ranged_attack_does_not_walk_into_water(self):
        self.target["wet"] = True
        self.attack.return_value = {"status": "cast_requested"}
        self.assertEqual(self.tick()["status"], "cast_requested")
        self.nav.send.assert_not_called()

    def test_skeleton_is_engaged_at_sixteen_blocks_without_waiting_for_melee(self):
        self.target.update(
            type="minecraft:skeleton", distance=16, position=[16.5, 63, 0.5]
        )
        self.assertEqual(self.tick()["status"], "approaching_threat")
        self.attack.assert_called_once_with("z", self.game, emergency=True)
        self.started[3].assert_called_once_with(radius=24, game=self.game)
        self.started[
            4
        ].assert_not_called()  # No protection cooldown before distant attack.

    def test_distant_confirmed_drowned_attacker_is_selected_beyond_sixteen_blocks(self):
        self.target.update(type="minecraft:drowned", distance=20, wet=True)
        self.game["combat_threats"] = {
            "z": {"source": "native_damage", "at": 99, "until": 120}
        }
        self.tick()
        self.attack.assert_called_once_with("z", self.game, emergency=True)
        self.nav.send.assert_not_called()

    def test_watched_water_target_can_receive_in_range_spell_without_route_retry(self):
        self.target.update(type="minecraft:drowned", wet=True)
        watch_target(self.game, self.target, "water", now=self.now)
        self.game["scepter_supply"] = {
            "scepters": [
                {"attack": "magicalscepter:magical_attack", "enough_experience": True}
            ]
        }
        self.attack.return_value = {"status": "cast_requested"}
        self.assertEqual(self.tick()["status"], "cast_requested")
        self.nav.send.assert_not_called()
        self.assertEqual(self.tick()["status"], "no_threat")
        self.now += 2
        self.assertEqual(self.tick()["status"], "cast_requested")
        self.assertEqual(self.attack.call_count, 2)

    def test_failed_spell_does_not_reopen_a_watched_navigation_route(self):
        watch_target(self.game, self.target, "no dry route", now=self.now)
        self.game["scepter_supply"] = {
            "scepters": [
                {"attack": "magicalscepter:magical_attack", "enough_experience": True}
            ]
        }
        self.assertEqual(self.tick()["status"], "watching_target")
        self.nav.send.assert_not_called()

    def test_pursuit_stops_at_staff_range_instead_of_waiting_for_melee_contact(self):
        self.game["scepter_supply"] = {
            "scepters": [
                {"attack": "magicalscepter:magical_attack", "enough_experience": True}
            ]
        }
        self.target.update(distance=12, position=[12.5, 63, 0.5])
        self.tick()
        self.entry.hasActiveTask.return_value = True
        self.target.update(distance=7, position=[7.5, 63, 0.5])
        self.now += 1
        self.assertEqual(self.tick()["status"], "stopping_for_defense")
        self.nav.interrupt_for_combat.assert_called_once()
        self.entry.hasActiveTask.return_value = False
        self.attack.return_value = {"status": "cast_requested"}
        self.assertEqual(self.tick()["status"], "cast_requested")

    def test_dry_shore_step_towards_water_target_never_becomes_a_swim_route(self):
        self.game["scepter_supply"] = {
            "scepters": [
                {"attack": "magicalscepter:magical_attack", "enough_experience": True}
            ]
        }
        self.target.update(
            type="minecraft:drowned", wet=True, distance=12, position=[12.5, 63, 0.5]
        )
        self.assertEqual(self.tick()["status"], "approaching_threat")
        self.assertTrue(self.game["self_defense"]["shore_approach"])
        self.assertEqual(self.nav.send.call_args.args[1], "@goto 1 63 0")
        self.entry.hasActiveTask.return_value = True
        self.now += 0.5
        self.assertEqual(self.tick()["status"], "approaching_threat")
        self.nav.interrupt_for_combat.assert_not_called()

    def test_water_strip_between_dry_banks_is_not_an_approach(self):
        for block in self.entry.getBlocksAround.return_value:
            if block["position"].startswith("1,62,"):
                block["block"] = "water"
        self.assertEqual(self.tick()["status"], "watching_target")
        self.assertFalse(self.game["self_defense"]["active"])
        self.nav.send.assert_not_called()

    def test_stalled_chase_stops_once_waits_for_idle_then_releases_other_work(self):
        self.tick()
        self.entry.hasActiveTask.return_value = True
        self.now = 109
        self.assertEqual(self.tick()["status"], "waiting_for_task_stop")
        self.now = 110
        self.tick()
        self.nav.interrupt_for_combat.assert_called_once()
        self.entry.hasActiveTask.return_value = False
        self.assertEqual(self.tick()["status"], "watching_target")
        self.assertFalse(self.game["self_defense"]["active"])
        self.assertEqual(self.tick()["status"], "no_threat")
        self.nav.send.assert_called_once()

    def test_target_entering_water_cancels_owned_step_without_new_route(self):
        self.tick()
        self.entry.hasActiveTask.return_value = True
        self.target["wet"] = True
        self.now += 0.5
        self.assertEqual(self.tick()["status"], "waiting_for_task_stop")
        self.nav.interrupt_for_combat.assert_called_once()
        self.nav.send.assert_called_once()

    def test_maximum_pursuit_time_releases_even_a_moving_target(self):
        self.game["self_defense"] = {
            "active": True,
            "target": dict(self.target),
            "stop_sent": False,
            "protect_attempted": True,
            "pursuit_started_at": 75,
            "progress_at": 99,
            "best_distance": 6,
        }
        result = self.tick()
        self.assertEqual(result["status"], "watching_target")
        self.assertIn("24 seconds", result["reason"])
        self.nav.send.assert_not_called()

    def test_hobby_cannot_restart_a_deferred_route_or_dispatch_its_own_chase(self):
        self.tick()
        self.game["combat_watch"] = {
            "z": {"until": 130, "position": [5.5, 63, 0.5], "damage_at": 0}
        }
        self.attack.reset_mock()
        self.assertEqual(hunting.hunt_step(game=self.game)["status"], "no_target")
        self.attack.assert_not_called()
        self.game["combat_watch"] = {}
        result = hunting.hunt_step(game=self.game)
        self.assertEqual(result["status"], "no_target")
        self.assertNotIn("next_action", result)

    def test_checkpoint_failure_does_not_prevent_urgent_stop(self):
        self.entry.hasActiveTask.return_value = True
        callback = Mock(side_effect=OSError("checkpoint unavailable"))
        self.assertEqual(
            hunting.defend_step(self.game, self.nav, before_stop=callback)["status"],
            "stopping_for_defense",
        )
        self.nav.interrupt_for_combat.assert_called_once()


class NativeCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.gateway, self.nav = Mock(), Mock()
        self.nav.lock = threading.RLock()
        self.gateway.jvm.java.lang.System.identityHashCode.return_value = 7
        self.gateway.entry_point.inGame.return_value = True
        self.gateway.entry_point.hasActiveTask.return_value = False
        self.game = {
            "alive": True,
            "player_priority": {"intent": 10},
            "work_command_revision": 1,
        }
        self.reflex = CombatReflex()
        self.invoke = patch("saruman.combat_reflex.on_client").start()
        self.water = patch(
            "saruman.combat_reflex.water_state",
            return_value={"wet": False, "submerged": False, "grounded": True},
        ).start()
        self.addCleanup(patch.stopall)
        self.reflex._capture(self.gateway, self.game)

    def test_exact_native_goal_resumes_once_after_dry_idle_and_not_during_rescue(self):
        task = self.reflex.saved_task
        self.game["survival"] = {"active": True}
        self.reflex._resume(self.gateway, self.game)
        self.invoke.assert_not_called()
        self.game["survival"] = {}
        self.water.return_value["wet"] = True
        self.reflex._resume(self.gateway, self.game)
        self.invoke.assert_not_called()
        self.water.return_value["wet"] = False
        self.reflex._resume(self.gateway, self.game)
        self.reflex._resume(self.gateway, self.game)
        self.invoke.assert_called_once()
        self.assertEqual(self.invoke.call_args.args[-1], (task,))
        self.assertTrue(self.game["hasActiveTask"])

    def test_new_command_player_intent_pause_or_world_change_cancels_resume(self):
        for change in (
            {"work_command_revision": 2},
            {"player_priority": {"intent": 11}},
            {"autonomy": {"paused": True}},
        ):
            with self.subTest(change=change):
                self.game = {
                    "alive": True,
                    "player_priority": {"intent": 10},
                    "work_command_revision": 1,
                }
                self.reflex.saved_task = None
                self.reflex._capture(self.gateway, self.game)
                self.game.update(deepcopy(change))
                self.reflex._resume(self.gateway, self.game)
                self.assertIsNone(self.reflex.saved_task)
        self.reflex._capture(self.gateway, self.game)
        self.game = {
            "alive": True,
            "player_priority": {"intent": 10},
            "work_command_revision": 1,
        }
        self.reflex.saved_task = None
        self.reflex._capture(self.gateway, self.game)
        self.gateway.jvm.java.lang.System.identityHashCode.return_value = 8
        self.reflex._resume(self.gateway, self.game)
        self.assertIsNone(self.reflex.saved_task)
        self.invoke.assert_not_called()

    def test_stop_death_and_world_switch_discard_checkpoint_and_watch_list(self):
        self.game["combat_controls"] = {"defend_self": True}
        self.reflex.tick(self.gateway, self.game, self.nav, enabled=False)
        self.assertIsNone(self.reflex.saved_task)
        self.reflex.next_poll = 0
        self.reflex._capture(self.gateway, self.game)
        self.game["alive"] = False
        self.reflex.tick(self.gateway, self.game, self.nav, enabled=True)
        self.assertIsNone(self.reflex.saved_task)
        self.reflex.next_poll = 0
        self.reflex.world_session = 7
        self.game.update(world_session=8, combat_watch={"z": {"until": 999}})
        self.reflex.tick(self.gateway, self.game, self.nav, enabled=True)
        self.assertEqual(self.game["combat_watch"], {})
        self.invoke.assert_not_called()

    def test_new_native_owner_or_unknown_resume_result_is_never_overwritten_or_replayed(
        self,
    ):
        self.gateway.entry_point.hasActiveTask.return_value = True
        self.reflex._resume(self.gateway, self.game)
        self.assertIsNone(self.reflex.saved_task)
        self.invoke.assert_not_called()
        self.gateway.entry_point.hasActiveTask.return_value = False
        self.reflex._capture(self.gateway, self.game)
        self.invoke.side_effect = TimeoutError("dispatch unknown")
        with self.assertRaises(TimeoutError):
            self.reflex._resume(self.gateway, self.game)
        self.reflex._resume(self.gateway, self.game)
        self.invoke.assert_called_once()

    def test_counted_goals_hobbies_and_combat_steps_do_not_get_duplicate_native_checkpoints(
        self,
    ):
        for extra in (
            {"tree_task": {"status": "active"}},
            {"autonomy": {"activity": "farm"}},
            {"navigation": {"purpose": "combat", "status": "running"}},
        ):
            with self.subTest(extra=extra):
                self.reflex.saved_task = None
                self.reflex._capture(self.gateway, {"alive": True, **extra})
                self.assertIsNone(self.reflex.saved_task)

    def test_original_navigation_and_collection_limits_survive_resume(self):
        nav = NavigationGuard()
        self.reflex.navigation = nav
        self.reflex.saved_task = None
        now = 100
        with patch("saruman.navigation.time.monotonic", side_effect=lambda: now):
            nav.collection.start(self.gateway.entry_point, "@get bread 1", self.game)
            original_started = nav.collection.started
            nav.collection.best_counts = {"minecraft:wheat": 2}
            self.reflex._capture(self.gateway, self.game)
            nav.collection.forget(self.game)
            now = 110
            self.reflex._resume(self.gateway, self.game)
            self.assertEqual(nav.collection.started, original_started)
            self.assertEqual(nav.collection.best_counts, {"minecraft:wheat": 2})
            self.assertEqual(nav.collection.goal, {"item": "bread", "count": 1})

    def test_expired_original_route_or_an_existing_cancellation_cannot_resume(self):
        nav = NavigationGuard()
        nav.target, nav.started = (10, 63, 0), 0
        self.reflex.navigation = nav
        self.reflex.saved_task = None
        self.reflex._capture(self.gateway, self.game)
        with patch("saruman.combat_reflex.time.monotonic", return_value=200):
            self.reflex._resume(self.gateway, self.game)
        self.assertIsNone(self.reflex.saved_task)
        self.invoke.assert_not_called()
        self.game["navigation"] = {"status": "stop_requested"}
        self.reflex._capture(self.gateway, self.game)
        self.assertIsNone(self.reflex.saved_task)
