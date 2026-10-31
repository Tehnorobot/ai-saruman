"""Emergency ascent, shore escape and command isolation without moving a real player."""

import unittest
from unittest.mock import Mock, patch

from saruman.navigation import NavigationGuard
from saruman.survival import WaterSafety, buoyancy, nearest_dry_shore, water_state
from saruman.terrain_search import search_max_moves


class WaterTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.gateway, self.nav = Mock(), Mock()
        self.gateway.entry_point.hasActiveTask.return_value = False
        self.gateway.jvm.java.lang.System.identityHashCode.return_value = 17
        self.game = {
            "alive": True,
            "tree_task": {"status": "active", "targets": [[1, 63, 2]]},
        }
        self.controller = WaterSafety(clock=lambda: self.now)
        patch.object(self.controller, "_world", return_value=7).start()
        self.state = {"wet": True, "submerged": True, "air": 100, "max_air": 300}
        self.observe = patch(
            "saruman.survival.water_state", side_effect=lambda _: dict(self.state)
        ).start()
        self.rise = patch("saruman.survival.buoyancy").start()
        self.swim = patch("saruman.survival.swim_input").start()
        self.shore = patch(
            "saruman.survival.nearest_dry_shore", return_value=None
        ).start()
        self.addCleanup(patch.stopall)

    def tick(self, now, enabled=True):
        self.now = now
        return self.controller.tick(self.gateway, self.game, self.nav, enabled=enabled)

    def test_submersion_preempts_busy_work_before_air_runs_out(self):
        self.gateway.entry_point.hasActiveTask.return_value = True
        self.assertFalse(self.tick(0))
        self.assertTrue(self.game["survival"]["active"])
        self.assertEqual(self.game["survival"]["status"], "surfacing")
        self.nav.interrupt_for_survival.assert_called_once()
        self.rise.assert_called_once_with(self.gateway, True)
        self.assertEqual(self.game["tree_task"]["status"], "paused")
        self.assertEqual(self.game["tree_task"]["targets"], [[1, 63, 2]])
        self.shore.assert_called_once()
        self.tick(0.1)
        self.observe.assert_called_once()
        self.tick(0.3)
        self.nav.interrupt_for_survival.assert_called_once()

    def test_failed_stop_cannot_prevent_emergency_ascent(self):
        self.nav.interrupt_for_survival.side_effect = TimeoutError("unknown stop")
        self.tick(0)
        self.rise.assert_called_once_with(self.gateway, True)
        self.assertEqual(self.game["survival"]["status"], "surfacing")

    def test_surface_escape_uses_one_dry_shore_route(self):
        self.state["submerged"] = False
        self.shore.return_value = (3, 63, 0)
        self.tick(0)
        self.swim.assert_called_with(self.gateway, (3, 63, 0))
        self.nav.send.assert_not_called()
        self.gateway.entry_point.hasActiveTask.return_value = True
        self.tick(0.3)
        self.nav.send.assert_not_called()
        self.assertEqual(self.game["survival"]["status"], "reaching_shore")
        self.state.update(wet=False)
        self.gateway.entry_point.hasActiveTask.return_value = False
        self.assertTrue(self.tick(0.6))
        self.rise.assert_called_with(self.gateway, False)
        self.assertFalse(self.game["survival"]["active"])

    def test_no_visible_shore_keeps_floating_without_an_invented_destination(self):
        self.state["submerged"] = False
        self.tick(0)
        self.tick(0.3)
        self.assertEqual(self.game["survival"]["status"], "afloat")
        self.nav.send.assert_not_called()
        self.assertEqual(self.rise.call_count, 2)

    def test_failed_shore_is_not_replayed(self):
        self.state["submerged"] = False
        self.shore.side_effect = lambda _, failed: (
            None if (3, 63, 0) in failed else (3, 63, 0)
        )
        self.tick(0)
        self.tick(1)
        self.tick(4)
        self.nav.send.assert_not_called()
        self.assertIn((3, 63, 0), self.controller.failed)

    def test_stop_or_death_releases_owned_input_and_never_starts_work(self):
        self.tick(0)
        self.tick(1, enabled=False)
        self.rise.assert_called_with(self.gateway, False)
        self.assertFalse(self.game["survival"]["active"])
        self.tick(2)
        self.game["alive"] = False
        self.tick(3)
        self.rise.assert_called_with(self.gateway, False)
        self.nav.send.assert_not_called()

    def test_observation_failure_during_rescue_keeps_normal_tasks_blocked(self):
        self.tick(0)
        self.observe.side_effect = TimeoutError("observation lost")
        self.assertFalse(self.tick(1))
        self.assertTrue(self.game["survival"]["active"])
        self.assertEqual(self.game["survival"]["status"], "unavailable")

    def test_dry_stopped_agent_never_acquires_input(self):
        self.tick(0, enabled=False)
        self.rise.assert_not_called()
        self.nav.interrupt_for_survival.assert_not_called()

    def test_unknown_stop_after_reaching_land_is_not_spammed(self):
        self.tick(0)
        self.state.update(wet=False, submerged=False)
        self.gateway.entry_point.hasActiveTask.return_value = True
        self.nav.interrupt_for_survival.side_effect = TimeoutError("unknown stop")
        self.tick(1)
        self.tick(2)
        self.assertEqual(self.nav.interrupt_for_survival.call_count, 2)
        self.assertTrue(self.game["survival"]["active"])

    def test_shutdown_releases_owned_jump(self):
        self.tick(0)
        self.controller.close(self.gateway)
        self.rise.assert_called_with(self.gateway, False)
        self.assertFalse(self.controller.holding)

    def test_surface_crossing_keeps_native_work_instead_of_stop_escape_repeat(self):
        self.state.update(submerged=False, air=300)
        self.gateway.entry_point.hasActiveTask.return_value = True
        for now in (0, 0.5, 2, 5, 20):
            self.assertTrue(self.tick(now))
        self.assertEqual(self.game["survival"]["status"], "swimming")
        self.assertEqual(self.game["tree_task"]["status"], "active")
        self.nav.interrupt_for_survival.assert_not_called()
        self.shore.assert_not_called()

    def test_short_dip_rises_without_cancelling_but_prolonged_dip_preempts_once(self):
        self.gateway.entry_point.hasActiveTask.return_value = True
        self.state["air"] = 299
        self.assertTrue(self.tick(0))
        self.assertTrue(self.tick(1))
        self.nav.interrupt_for_survival.assert_not_called()
        self.assertFalse(self.tick(2.1))
        self.assertFalse(self.tick(3))
        self.nav.interrupt_for_survival.assert_called_once()
        self.rise.assert_called_with(self.gateway, True)

    def test_idle_surface_player_swims_to_shore_even_with_full_air(self):
        self.state.update(submerged=False, air=300)
        self.shore.return_value = (1, 63, 0)
        self.assertFalse(self.tick(0))
        self.swim.assert_called_with(self.gateway, (1, 63, 0))
        self.assertTrue(self.game["survival"]["active"])

    def test_combat_water_contact_rescues_immediately_and_never_restores_the_chase(
        self,
    ):
        self.game.pop("tree_task")
        target = {"uuid": "z", "position": [5, 63, 0]}
        self.game["self_defense"] = {
            "active": True,
            "approaching": True,
            "target": target,
        }
        self.state.update(submerged=False, air=300)
        self.gateway.entry_point.hasActiveTask.return_value = True
        with patch("saruman.survival.on_client") as resume:
            self.assertFalse(
                self.tick(0)
            )  # No 2/4-second surface grace during a chase.
            self.nav.interrupt_for_survival.assert_called_once()
            self.assertEqual(self.game["self_defense"]["status"], "water_interrupted")
            self.assertIn("z", self.game["combat_watch"])
            self.assertIsNone(self.controller.native_task)
            self.gateway.entry_point.hasActiveTask.return_value = False
            self.state.update(wet=False, submerged=False, grounded=True)
            self.assertTrue(self.tick(1))
            resume.assert_not_called()

    def test_combat_route_marker_alone_also_prevents_water_resume(self):
        self.game.pop("tree_task")
        self.game["navigation"] = {"purpose": "combat", "status": "running"}
        self.state.update(submerged=False, air=300)
        self.gateway.entry_point.hasActiveTask.return_value = True
        self.assertFalse(self.tick(0))
        self.assertIsNone(self.controller.native_task)

    def test_stalled_surface_route_is_interrupted_without_waiting_to_drown(self):
        self.state.update(submerged=False, air=300, position=[0.5, 63, 0.5])
        self.gateway.entry_point.hasActiveTask.return_value = True
        self.assertTrue(self.tick(0))
        self.assertTrue(self.tick(2))
        self.assertFalse(self.tick(4.1))
        self.nav.interrupt_for_survival.assert_called_once()

    def test_submerged_escape_can_move_horizontally_instead_of_waiting_under_ceiling(
        self,
    ):
        self.shore.return_value = (1, 63, 0)
        self.tick(0)
        self.swim.assert_called_with(self.gateway, (1, 63, 0))
        self.state.update(wet=False, submerged=False, grounded=False)
        self.assertFalse(self.tick(1))  # A jump above water is not arrival on land.
        self.assertTrue(self.game["survival"]["active"])

    def test_native_goal_resumes_once_only_on_ground_in_the_same_world(self):
        self.game.pop("tree_task")
        self.gateway.entry_point.hasActiveTask.return_value = True
        root = self.gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getUserTaskChain.return_value.getCurrentTask.return_value
        with (
            patch.object(self.controller, "_world", return_value=7),
            patch("saruman.survival.on_client") as invoke,
        ):
            self.tick(0)
            self.gateway.entry_point.hasActiveTask.return_value = False
            self.state.update(wet=False, submerged=False, grounded=True)
            self.assertTrue(self.tick(1))
            self.tick(2)
            invoke.assert_called_once()
            self.assertEqual(invoke.call_args.args[-1], (root,))
            self.assertTrue(self.game["hasActiveTask"])

    def test_stop_or_world_change_discards_native_resume(self):
        self.game.pop("tree_task")
        self.gateway.entry_point.hasActiveTask.return_value = True
        with (
            patch.object(self.controller, "_world", side_effect=[7, 8]),
            patch("saruman.survival.on_client") as invoke,
        ):
            self.tick(0)
            self.gateway.entry_point.hasActiveTask.return_value = False
            self.state.update(wet=False, submerged=False)
            self.tick(1)
            invoke.assert_not_called()
            self.assertIsNone(self.controller.native_task)

    def test_same_native_goal_is_not_resumed_forever_after_repeated_submersion(self):
        self.game.pop("tree_task")
        with patch("saruman.survival.on_client") as invoke:
            for start in (0, 3, 6):
                self.state.update(wet=True, submerged=True)
                self.gateway.entry_point.hasActiveTask.return_value = True
                self.tick(start)
                self.state.update(wet=False, submerged=False, grounded=True)
                self.gateway.entry_point.hasActiveTask.return_value = False
                self.tick(start + 1)
            self.assertEqual(invoke.call_count, 2)
            self.assertIsNone(self.controller.native_task)

    def test_saving_counted_goal_keeps_budget_progress_and_positions_for_resume(self):
        self.game["tree_task"].update(
            id="trees",
            config={"count": 2, "max_bone_meal": 9},
            bone_meal_charged=4,
            progress={"completed": 1},
        )
        self.tick(0)
        saved = self.game["deferred_goals"][0]["task"]
        self.assertEqual(saved["pause_reason"], "Water rescue")
        self.assertEqual(saved["bone_meal_charged"], 4)
        self.assertEqual(saved["config"]["max_bone_meal"], 9)
        self.assertEqual(saved["progress"]["completed"], 1)
        self.assertEqual(saved["targets"], [[1, 63, 2]])


class NativeWaterTests(unittest.TestCase):
    def test_escape_destination_is_dry_and_can_exclude_a_failed_shore(self):
        gateway, rules = Mock(), Mock()
        gateway.entry_point.getGameState.return_value = {"self": {"pos": "0,63,0"}}
        gateway.entry_point.getBlocksAround.return_value = []
        rules.free.side_effect = lambda p: p == (2, 63, 0)
        rules.support.side_effect = lambda p: p == (2, 62, 0)
        with (
            patch(
                "saruman.survival.swim_paths",
                return_value={
                    (0, 63, 0): [],
                    (1, 63, 0): [(1, 63, 0)],
                    (2, 63, 0): [(1, 63, 0), (2, 63, 0)],
                },
            ),
            patch("saruman.world_rules.NativeWorldRules", return_value=rules),
        ):
            self.assertEqual(nearest_dry_shore(gateway, set()), (1, 63, 0))
            self.assertIsNone(nearest_dry_shore(gateway, {(2, 63, 0)}))
        gateway.entry_point.RunInnerCommand.assert_not_called()
        gateway.entry_point.getGameState.return_value = {"self": None}
        self.assertIsNone(nearest_dry_shore(gateway, set()))

    def test_water_observation_reads_actual_player_methods_without_mutation(self):
        gateway = Mock()
        player = gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getPlayer.return_value
        player.method_5799.return_value = True
        player.method_5869.return_value = False
        player.method_5669.return_value = 275
        player.method_5748.return_value = 300
        player.method_23317.return_value = 1.5
        player.method_23318.return_value = 63.2
        player.method_23321.return_value = 2.5
        player.method_24828.return_value = False
        self.assertEqual(
            water_state(gateway),
            {
                "wet": True,
                "submerged": False,
                "air": 275,
                "max_air": 300,
                "position": [1.5, 63.2, 2.5],
                "grounded": False,
            },
        )
        player.method_7331.assert_not_called()

    def test_buoyancy_releases_sneak_then_holds_jump_on_client_thread(self):
        gateway = Mock()
        with patch("saruman.survival.on_client") as execute:
            buoyancy(gateway, True)
            self.assertEqual(
                [c.args[3] for c in execute.call_args_list], ["release", "hold"]
            )
            self.assertEqual(
                execute.call_args.args[-1],
                (gateway.jvm.kaptainwutax.tungsten.path.movements.Input.JUMP,),
            )
            buoyancy(gateway, False)
            self.assertEqual(execute.call_args.args[3], "release")

    def test_interruption_remembers_failed_water_route_and_forgets_collection(self):
        nav, entry, game = NavigationGuard(), Mock(), {}
        nav.target = (4, 63, 2)
        entry.stopPathing.return_value = {"ok": True}
        nav.interrupt_for_survival(entry, game)
        self.assertIsNone(nav.target)
        self.assertEqual(nav.failures[0]["target"], [4, 63, 2])
        self.assertEqual(game["navigation"]["status"], "stop_requested")

    def test_search_cap_is_configurable_and_rejects_unbounded_values(self):
        with patch.dict("os.environ", {"SARUMAN_SEARCH_MAX_MOVES": "128"}):
            self.assertEqual(search_max_moves(), 128)
        for value in ("0", "-1", "1000000", "bad"):
            with (
                patch.dict("os.environ", {"SARUMAN_SEARCH_MAX_MOVES": value}),
                self.assertRaises(ValueError),
            ):
                search_max_moves()
