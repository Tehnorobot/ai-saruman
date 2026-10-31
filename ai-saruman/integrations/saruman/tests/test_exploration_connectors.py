import math
import unittest
from unittest.mock import Mock, patch

from saruman.autonomy import exploration_step
from saruman.terrain_search import plan_exploration


class ConnectorTests(unittest.TestCase):
    def test_known_corridor_connects_to_new_scan_and_read_is_idempotent(self):
        state = {"visited": [[x, 63, z] for x in range(-5, 6) for z in range(-5, 6)]}
        paths = {(0, 63, 0): 0, (1, 63, 0): 1, (2, 63, 0): 2}
        first = plan_exploration(paths, (0, 63, 0), state, 4)
        self.assertEqual(first["route_kind"], "connector")
        self.assertEqual(first["position"], [2, 63, 0])
        repeated = plan_exploration(paths, (0, 63, 0), state, 4)
        self.assertEqual(repeated["command"], first["command"])
        self.assertEqual(repeated["moves_used"], 1)
        # The only exit from a dead end is a deliberate return, not permanent idle.
        back = plan_exploration(
            {(2, 63, 0): 0, (1, 63, 0): 1, (0, 63, 0): 2}, (2, 63, 0), state, 4
        )
        self.assertEqual(back["route_kind"], "backtrack")
        self.assertEqual(back["position"], [0, 63, 0])
        repeated_back = plan_exploration(
            {(2, 63, 0): 0, (1, 63, 0): 1, (0, 63, 0): 2}, (2, 63, 0), state, 4
        )
        self.assertEqual(repeated_back["moves_used"], back["moves_used"])
        no_loop = plan_exploration(paths, (0, 63, 0), state, 4)
        self.assertEqual(no_loop["status"], "no_safe_frontier")
        other_branch = plan_exploration({**paths, (0, 63, 2): 2}, (0, 63, 0), state, 4)
        self.assertEqual(other_branch["position"], [0, 63, 2])

    def test_backtracking_respects_failed_exits_and_the_original_budget(self):
        paths = {(2, 63, 0): 0, (1, 63, 0): 1, (0, 63, 0): 2}
        state = {"trail": [[0, 63, 0], [2, 63, 0]], "visited": [[0, 63, 0], [2, 63, 0]]}
        blocked = plan_exploration(
            paths, (2, 63, 0), state, 4, [{"until": math.inf, "target": [0, 63, 0]}]
        )
        self.assertNotIn("next_action", blocked)
        state["proposals"] = [{"from": [1, 63, 0], "position": [2, 63, 0]}] * 4
        self.assertEqual(
            plan_exploration(paths, (2, 63, 0), state, 4)["status"],
            "search_budget_exhausted",
        )

    def test_single_block_reposition_is_possible_but_failure_and_disabled_movement_win(
        self,
    ):
        paths = {(0, 63, 0): 0, (1, 63, 0): 1}
        self.assertEqual(
            plan_exploration(paths, (0, 63, 0), {}, 4)["position"], [1, 63, 0]
        )
        failure = {"until": math.inf, "target": [1, 63, 0]}
        self.assertEqual(
            plan_exploration(paths, (0, 63, 0), {}, 4, [failure])["status"],
            "no_safe_frontier",
        )
        self.assertNotIn("next_action", plan_exploration(paths, (0, 63, 0), {}, 0))
        self.assertNotIn("next_action", plan_exploration({}, (0, 63, 0), {}, 4))

    def test_new_autonomous_pass_preserves_visits_and_arrivals(self):
        gateway = Mock()
        entry = gateway.entry_point
        entry.inGame.return_value = True
        entry.hasActiveTask.return_value = False
        entry.getGameState.return_value = {"self": {"pos": "0,63,0"}}
        entry.getBlocksAround.return_value = []
        state = {
            "created_at": 100,
            "visited": [[0, 63, 0]],
            "proposals": [{"from": [9, 63, 9], "position": [10, 63, 9]}] * 64,
        }
        with (
            patch("saruman.trees._gateway", return_value=gateway),
            patch("saruman.world_rules.NativeWorldRules"),
            patch(
                "saruman.trees._walk_paths", return_value={(0, 63, 0): 0, (3, 63, 0): 3}
            ),
            patch("saruman.autonomy.time.time", return_value=100),
        ):
            first = exploration_step(state=state, search_steps=64)
            self.assertEqual(first["reason_code"], "search_budget_exhausted")
            self.assertNotIn("next_action", first)
            second = exploration_step(state=state, search_steps=64)
            self.assertEqual(second["status"], "needs_move")
            self.assertEqual(state["visited"], [[0, 63, 0]])
            self.assertEqual(state["trail"], [[0, 63, 0]])
        entry.RunInnerCommand.assert_not_called()

    def test_large_displacement_starts_a_local_background_pass_without_old_budget_lock(
        self,
    ):
        gateway = Mock()
        gateway.entry_point.inGame.return_value = True
        gateway.entry_point.hasActiveTask.return_value = False
        gateway.entry_point.getGameState.return_value = {"self": {"pos": "100,63,0"}}
        gateway.entry_point.getBlocksAround.return_value = []
        state = {
            "created_at": 100,
            "last_center": [0, 63, 0],
            "trail": [[0, 63, 0]],
            "proposals": [{"from": [0, 63, 0], "position": [2, 63, 0]}] * 4,
        }
        with (
            patch("saruman.trees._gateway", return_value=gateway),
            patch("saruman.world_rules.NativeWorldRules"),
            patch(
                "saruman.trees._walk_paths",
                return_value={(100, 63, 0): 0, (102, 63, 0): 2},
            ),
            patch("saruman.autonomy.time.time", return_value=100),
        ):
            result = exploration_step(state=state, search_steps=4)
        self.assertEqual(result["status"], "needs_move")
        self.assertEqual(result["moves_used"], 1)
        self.assertEqual(state["trail"], [[100, 63, 0]])
