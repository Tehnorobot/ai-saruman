"""Route loops, cancellation and observed tree approaches, without a live world."""

import unittest
from unittest.mock import Mock, patch

from saruman.navigation import NavigationGuard, check_recent_failure
from saruman.procedures import ProcedureContinuation
from saruman.trees import _position, _tree_approaches
from tests.fake_world_rules import FakeWorldRules


class NavigationTests(unittest.TestCase):
    def test_combat_dispatch_cannot_skip_observed_neighbour_or_change_work_revision(
        self,
    ):
        self.entry.getGameState.return_value = {"self": {"pos": "0.5,63,0.5"}}
        self.game["work_command_revision"] = 11
        with self.assertRaisesRegex(ValueError, "adjacent"):
            self.guard.send(self.entry, "@goto 3 63 0", self.game, purpose="combat")
        self.entry.RunInnerCommand.assert_not_called()
        self.guard.send(self.entry, "@goto 1 63 0", self.game, purpose="combat")
        self.assertEqual(self.game["navigation"]["purpose"], "combat")
        self.assertEqual(self.game["work_command_revision"], 11)
        self.guard.interrupt_for_survival(self.entry, self.game)
        self.assertEqual(
            self.game["navigation"]["recent_failures"][0]["target"], [1, 63, 0]
        )

    def test_new_ordinary_command_changes_resume_revision(self):
        self.game["work_command_revision"] = 11
        self.guard.send(self.entry, "@goto 1 63 0", self.game)
        self.assertNotEqual(self.game["work_command_revision"], 11)

    def setUp(self):
        clock_patch = patch("saruman.navigation.time.monotonic", return_value=0)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.entry = Mock()
        self.entry.getBlockAt.side_effect = lambda x, y, z: {
            "id": "minecraft:stone" if y == 62 else "minecraft:air",
            "replaceable": "false" if y == 62 else "true",
            "is_air": "false" if y == 62 else "true",
        }
        self.entry.stopPathing.return_value = {"ok": True}
        self.game = {}
        rules = Mock()
        rules.support.side_effect = lambda p: (
            self.entry.getBlockAt(*p)["replaceable"] == "false"
        )
        rules.free.side_effect = lambda p, **kw: (
            self.entry.getBlockAt(*p)["replaceable"] == "true"
        )
        self.guard = NavigationGuard(rules_factory=lambda: rules)
        rules.standing.side_effect = lambda p: (
            rules.support((p[0], p[1] - 1, p[2]))
            and rules.free(p)
            and rules.free((p[0], p[1] + 1, p[2]))
        )
        self.chain = "1. Main task: <Getting to block class_2338{x=10, y=63, z=0}> Tungsten (primary) pathfinding..."

    def start(self):
        self.guard.send(self.entry, "@goto 10 63 0", self.game)

    def tick(self, seconds, position, busy=True, chain=None):
        self.clock.return_value = seconds
        self.guard.tick(self.entry, self.game, position, busy, chain or self.chain)

    def test_rejects_airborne_goal_without_sending_or_correcting_it(self):
        with self.assertRaisesRegex(ValueError, "No ground"):
            self.guard.send(self.entry, "@goto 10 64 0", self.game)
        self.entry.RunInnerCommand.assert_not_called()
        self.entry.stopPathing.assert_not_called()
        self.assertEqual(self.game["navigation"]["status"], "rejected")

    def test_jumping_and_backtracking_do_not_reset_net_progress_timer(self):
        self.start()
        for seconds, position in [
            (3, "0.5,63,0.5"),
            (10, "0.5,64.2,0.5"),
            (15, "1.0,63,0.5"),
            (25, "0.2,64,0.5"),
            (34, "0.6,63,0.5"),
        ]:
            self.tick(seconds, position)
        self.entry.stopPathing.assert_called_once()
        self.assertEqual(self.game["navigation"]["status"], "stopping")
        self.tick(35, "0.6,63,0.5")
        self.entry.stopPathing.assert_called_once()
        self.tick(36, "0.6,63,0.5", busy=False)
        failure = self.game["navigation"]
        self.assertEqual(failure["status"], "blocked")
        self.assertIn("No net approach", failure["reason"])
        for destination in [(10, 63, 0), (11, 63, 0)]:
            with self.assertRaises(ValueError):
                check_recent_failure(destination, failure["recent_failures"])
        check_recent_failure((15, 63, 0), failure["recent_failures"])
        with patch(
            "saruman.navigation.time.time",
            return_value=failure["recent_failures"][0]["until"] + 1,
        ):
            check_recent_failure((10, 63, 0), failure["recent_failures"])

    def test_real_approach_continues_and_idle_requires_actual_arrival(self):
        self.start()
        for seconds, position in [
            (3, "0.5,63,0.5"),
            (25, "2.5,63,0.5"),
            (47, "5.5,63,0.5"),
            (69, "9.5,63,0.5"),
        ]:
            self.tick(seconds, position)
        self.entry.stopPathing.assert_not_called()
        self.tick(70, "10.5,63,0.5", busy=False)
        self.assertEqual(self.game["navigation"]["status"], "arrived")
        self.assertIsNone(self.guard.target)

    def test_native_finished_does_not_prove_arrival(self):
        self.start()
        self.tick(4, "0.5,63,0.5", busy=False)
        self.assertEqual(self.game["navigation"]["status"], "blocked")
        self.entry.stopPathing.assert_not_called()

    def test_replaced_task_is_never_cancelled(self):
        for chain in (
            "Main task: <Mining oak logs>",
            self.chain.replace("x=10", "x=20"),
        ):
            self.clock.return_value = 0
            self.start()
            self.tick(35, "0.5,63,0.5", chain=chain)
            self.assertIsNone(self.guard.target)
        self.entry.stopPathing.assert_not_called()

    def test_stop_uncertainty_does_not_repeat_stop_or_start_another_route(self):
        self.start()
        self.tick(3, "0.5,63,0.5")
        self.tick(34, "0.5,63,0.5")
        self.tick(40, "0.5,63,0.5")
        self.assertEqual(self.game["navigation"]["status"], "stop_failed")
        self.entry.hasActiveTask.return_value = True
        with self.assertRaisesRegex(ValueError, "Cancellation"):
            self.guard.send(self.entry, "@goto 20 63 0", self.game)
        self.entry.stopPathing.assert_called_once()
        self.entry.RunInnerCommand.assert_called_once()

    def test_explicit_stop_uses_both_native_navigators(self):
        self.start()
        self.guard.send(self.entry, "@stop", self.game)
        self.assertIsNone(self.guard.target)
        self.entry.stopPathing.assert_called_once()
        self.entry.RunInnerCommand.assert_called_once_with("@goto 10 63 0")

    def test_explicit_stop_is_acknowledged_only_after_observed_idle(self):
        self.start()
        failures = [{"target": [20, 63, 0], "reason": "blocked", "until": 99999999999}]
        self.guard.failures = failures.copy()
        self.guard.send(self.entry, "@stop", self.game)
        self.tick(4, "0.5,63,0.5", busy=True)
        self.assertEqual(self.game["navigation"]["status"], "stop_requested")
        self.game["hasActiveTask"] = True  # Old shared value is not confirmation.
        self.tick(5, "0.5,63,0.5", busy=False)
        self.assertEqual(self.game["navigation"]["status"], "stopped")
        self.assertEqual(self.game["navigation"]["recent_failures"], failures)
        event = self.game["navigation"]["event_id"]
        self.tick(6, "0.5,63,0.5", busy=False)
        self.assertEqual(self.game["navigation"]["event_id"], event)
        self.entry.stopPathing.assert_called_once()

    def test_late_idle_recovers_stop_failure_without_another_cancellation(self):
        self.start()
        self.tick(3, "0.5,63,0.5")
        self.tick(34, "0.5,63,0.5")
        self.tick(40, "0.5,63,0.5")
        self.assertEqual(self.game["navigation"]["status"], "stop_failed")
        self.tick(41, "0.5,63,0.5", busy=True)
        self.assertEqual(self.game["navigation"]["status"], "stop_failed")
        self.tick(42, "0.5,63,0.5", busy=False)
        self.assertEqual(self.game["navigation"]["status"], "stopped")
        self.assertEqual(self.game["navigation"]["previous_status"], "stop_failed")
        self.entry.stopPathing.assert_called_once()

    def test_dispatch_uses_collision_even_for_nonreplaceable_open_blocks(self):
        rules = Mock()
        rules.support.return_value = True
        rules.free.return_value = True
        guard = NavigationGuard(rules_factory=lambda: rules)
        self.entry.getBlockAt.side_effect = AssertionError(
            "Display/replaceability is not geometry"
        )
        guard.send(self.entry, "@goto 10 63 0", self.game)
        self.entry.RunInnerCommand.assert_called_once_with("@goto 10 63 0")
        rules.support.assert_called_once_with((10, 62, 0))

    def test_dispatch_without_native_collision_checks_fails_before_sending(self):
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            NavigationGuard().send(self.entry, "@goto 10 63 0", self.game)
        self.entry.RunInnerCommand.assert_not_called()

    def test_duplicate_goto_does_not_restart_timer(self):
        self.start()
        self.clock.return_value = 20
        with self.assertRaisesRegex(ValueError, "already running"):
            self.start()
        self.assertEqual(self.guard.started, 0)
        self.entry.RunInnerCommand.assert_called_once()

    def test_absolute_route_limit_even_when_it_keeps_approaching(self):
        self.start()
        for seconds, x in [
            (3, -100),
            (28, -90),
            (53, -80),
            (78, -70),
            (103, -60),
            (128, -50),
            (153, -40),
            (178, -30),
        ]:
            self.tick(seconds, f"{x},63,0.5")
        self.entry.stopPathing.assert_not_called()
        self.tick(181, "-29,63,0.5")
        self.entry.stopPathing.assert_called_once()
        self.assertIn("180", self.game["navigation"]["reason"])

    def test_unknown_stop_result_is_not_replayed(self):
        self.start()
        self.entry.stopPathing.side_effect = TimeoutError("lost response")
        self.tick(3, "0.5,63,0.5")
        self.tick(34, "0.5,63,0.5")
        self.tick(35, "0.5,63,0.5")
        self.assertEqual(self.game["navigation"]["status"], "stop_failed")
        self.entry.stopPathing.assert_called_once()

    def test_procedure_recovers_with_original_goal_and_bounded_alternatives(self):
        continuation = ProcedureContinuation("Вырастить дуб без повреждения построек")
        continuation.pending = True
        message = continuation.navigation_failed(
            {"status": "blocked", "reason": "loop"}
        )
        self.assertFalse(continuation.pending)
        self.assertEqual(continuation.remaining, 3)
        self.assertIn(continuation.goal, message)
        self.assertIn("Check actual inventory", message)
        continuation.navigation_failed({"status": "stop_failed"})
        self.assertTrue(continuation.done)
        self.assertTrue(
            continuation.blocks_task_start("agent_action", {"action": "@goto 50 63 0"})
        )
        self.assertFalse(
            continuation.blocks_task_start("agent_action", {"action": "@stop"})
        )


class TreeApproachTests(unittest.TestCase):
    def test_standing_y_is_above_observed_floor_not_above_sapling(self):
        world = {(x, 62, z): "smooth sandstone" for x in range(6) for z in (-1, 0, 1)}
        world[(5, 62, 0)] = "grass block"
        approaches = _tree_approaches(
            world, (0, 63, 0), [(5, 63, 0)], rules=FakeWorldRules(observed=world)
        )
        self.assertEqual(approaches[0]["standing_position"], [3, 63, 0])
        self.assertEqual(approaches[0]["command"], "@goto 3 63 0")
        self.assertNotEqual(
            approaches[0]["plant_position"], approaches[0]["standing_position"]
        )

    def test_no_approach_across_a_gap_or_through_low_ceiling(self):
        world = {(x, 62, 0): "stone" for x in (0, 1, 3, 4, 5)}
        self.assertEqual(
            _tree_approaches(
                world, (0, 63, 0), [(5, 63, 0)], rules=FakeWorldRules(observed=world)
            ),
            [],
        )
        world[(2, 62, 0)] = "stone"
        world[(2, 64, 0)] = "oak planks"
        self.assertEqual(
            _tree_approaches(
                world, (0, 63, 0), [(5, 63, 0)], rules=FakeWorldRules(observed=world)
            ),
            [],
        )
        self.assertEqual(_position("-0.8,63,-1.9"), (-1, 63, -2))


if __name__ == "__main__":
    unittest.main()
