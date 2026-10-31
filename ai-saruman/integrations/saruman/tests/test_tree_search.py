"""Search breadth, exploration depth and obstacle reasons without world mutations."""

import math
import unittest
from unittest.mock import patch

from saruman import trees
from saruman.procedures import ProcedureContinuation
from tests.fake_world_rules import FakeWorldRules


class TreeSearchTests(unittest.TestCase):
    def setUp(self):
        rules = patch("saruman.trees.NativeWorldRules", FakeWorldRules)
        rules.start()
        self.addCleanup(rules.stop)
        factory = patch("saruman.trees._gateway")
        self.gateway = factory.start().return_value
        self.addCleanup(factory.stop)
        self.entry = self.gateway.entry_point
        self.center = (0, 63, 0)
        self.entry.getGameState.side_effect = lambda: {
            "self": {"pos": ",".join(map(str, self.center))}
        }
        self.entry.getBlockAt.return_value = {"id": "minecraft:air", "block": "air"}
        self.entry.canPlaceBlock.return_value = {"canPlace": True, "policyAllows": True}
        native = patch(
            "saruman.trees._can_plant",
            side_effect=lambda g, s, p, rules=None: rules.can_plant(s, p),
        )
        self.native = native.start()
        self.addCleanup(native.stop)
        self.state = {}

    def floor(self, kind="smooth sandstone"):
        x, _, z = self.center
        return [
            {"position": f"{i},62,{j}", "block": kind}
            for i in range(x - 5, x + 6)
            for j in range(z - 5, z + 6)
        ]

    def scan(self, **kwargs):
        return trees.find_tree_spots(search_state=self.state, **kwargs)

    def test_red_sand_and_terracotta_support_real_approaches_to_grass(self):
        for kind in ("red sand", "orange terracotta", "terracotta", "gravel"):
            with self.subTest(ground=kind):
                self.entry.getBlocksAround.return_value = self.floor(kind) + [
                    {"position": "4,62,0", "block": "grass block"}
                ]
                scan = self.scan()
                self.assertGreater(scan["navigation"]["reachable_cells"], 1)
                self.assertEqual(scan["navigation"]["standing_on"], kind)
                self.assertTrue(scan["approaches"])
                approach = trees.tree_approach([4, 63, 0], allow_partial=True)
                self.assertIsNotNone(approach)
                self.assertGreater(approach["walk_steps"], 0)
        self.entry.RunInnerCommand.assert_not_called()

    def test_reachable_soil_is_ranked_ahead_of_a_closer_disconnected_site(self):
        self.entry.getBlocksAround.return_value = [
            {"position": f"{x},62,0", "block": "grass block" if x == -5 else "red sand"}
            for x in range(-5, 1)
        ] + [{"position": "4,62,0", "block": "grass block"}]
        scan = self.scan()
        self.assertEqual(scan["spots"][0]["position"], [-5, 63, 0])
        self.assertTrue(
            any(a["plant_position"] == [-5, 63, 0] for a in scan["approaches"])
        )
        self.assertFalse(
            any(a["plant_position"] == [4, 63, 0] for a in scan["approaches"])
        )

    def test_support_expansion_does_not_accept_hazards_or_partial_blocks(self):
        for kind in ("cactus", "magma block", "water", "oak slab", "sandstone stairs"):
            with self.subTest(ground=kind):
                self.entry.getBlocksAround.return_value = self.floor(kind)
                self.assertIsNone(trees.tree_approach([4, 63, 0], allow_partial=True))

    def test_returns_over_a_hundred_blocks_to_saved_target_in_observed_steps(self):
        destination = [100, 63, -40]
        self.entry.getBlocksAround.side_effect = lambda radius: self.floor()
        visited = []
        while math.dist(self.center, destination) > 3.5:
            previous = math.dist(self.center, destination)
            approach = trees.tree_approach(
                destination, allow_partial=True, previous_waypoints=visited
            )
            self.assertIsNotNone(approach)
            self.assertEqual(approach["plant_position"], destination)
            self.assertLessEqual(approach["walk_steps"], 8)
            self.center = tuple(approach["standing_position"])
            self.assertLess(math.dist(self.center, destination), previous)
            visited.append(list(self.center))
            self.assertLess(len(visited), 40)
        self.assertGreater(len(visited), 1)
        self.entry.RunInnerCommand.assert_not_called()

    def test_return_does_not_cross_missing_ground_or_reuse_failed_waypoint(self):
        self.entry.getBlocksAround.return_value = [
            {"position": "0,62,0", "block": "stone"}
        ]
        self.assertIsNone(trees.tree_approach([100, 63, 0], allow_partial=True))
        self.entry.getBlocksAround.return_value = self.floor()
        first = trees.tree_approach([100, 63, 0], allow_partial=True)
        failure = {"target": first["standing_position"], "until": math.inf}
        second = trees.tree_approach([100, 63, 0], [failure], allow_partial=True)
        self.assertGreater(
            math.dist(first["standing_position"], second["standing_position"]), 1.5
        )

    def test_finds_valid_candidate_beyond_old_24_candidate_cutoff(self):
        self.entry.getBlocksAround.return_value = self.floor("grass block")
        self.native.side_effect = [False] * 24 + [True] * 128
        result = self.scan()
        self.assertTrue(result["spots"])
        self.assertGreater(result["native_soil_checked"], 24)
        self.assertEqual(result["candidate_limit"], 128)
        self.assertEqual(result["rejected"]["soil_rule"], 24)
        self.assertFalse(result["search_complete"])
        self.entry.RunInnerCommand.assert_not_called()

    def test_buried_soil_is_filtered_before_spending_candidate_budget(self):
        self.entry.getBlocksAround.return_value = self.floor("grass block") + [
            {"position": f"{x},61,{z}", "block": "dirt"}
            for x in range(-5, 6)
            for z in range(-5, 6)
        ]
        result = self.scan(candidate_limit=24)
        self.assertEqual(result["candidate_count"], 120)
        self.assertEqual(result["soil_cells_seen"], 242)
        self.assertEqual(result["rejected"]["occupied"], 122)
        self.assertEqual(result["candidates_checked"], 3)
        self.assertTrue(result["spots"])

    def test_stone_surfaces_do_not_hide_rare_soil_behind_the_candidate_budget(self):
        self.entry.getBlocksAround.return_value = (
            self.floor("stone")
            + [
                {"position": f"{x},59,{z}", "block": "stone"}
                for x in range(-5, 6)
                for z in range(-5, 6)
            ]
            + [{"position": "5,62,5", "block": "grass block"}]
        )
        result = self.scan(candidate_limit=24)
        self.assertGreater(result["native_soil_checked"], 128)
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["spots"][0]["position"], [5, 63, 5])

    def test_native_soil_on_a_different_height_is_not_rejected_by_name(self):
        self.entry.getBlocksAround.return_value = self.floor("stone") + [
            {"position": "2,65,0", "block": "custom fertile rock"}
        ]
        self.native.side_effect = lambda g, s, p, rules=None: p == (2, 66, 0)
        result = self.scan()
        self.assertEqual(result["spots"][0]["position"], [2, 66, 0])

    def test_grass_beside_sapling_does_not_reject_site_but_trunk_and_roof_do(self):
        soil = {"position": "2,62,0", "block": "grass block"}
        self.entry.getBlocksAround.return_value = [
            soil,
            {"position": "3,63,0", "block": "short grass"},
        ]
        self.assertEqual(self.scan()["spots"][0]["position"], [2, 63, 0])
        self.entry.getBlocksAround.return_value.append(
            {"position": "2,66,0", "block": "oak planks"}
        )
        result = self.scan()
        self.assertFalse(result["spots"])
        self.assertEqual(result["rejected"]["growth_space"], 1)
        self.entry.getBlocksAround.return_value = [
            soil,
            {"position": "2,63,0", "block": "short grass"},
        ]
        self.assertEqual(self.scan()["rejected"]["occupied"], 1)

    def test_new_area_proposed_and_repeated_read_does_not_spend_more_depth(self):
        self.entry.getBlocksAround.return_value = self.floor()
        first = self.scan()["exploration"]
        second = self.scan()["exploration"]
        self.assertEqual(first["status"], "move_to_scan")
        self.assertEqual(first["command"], second["command"])
        self.assertEqual(second["moves_used"], 1)
        self.assertGreater(first["new_columns_estimate"], 0)
        self.assertEqual(first["position"][1], 63)
        self.entry.RunInnerCommand.assert_not_called()
        self.entry.placeBlockLooking.assert_not_called()

    def test_search_reaches_grass_beyond_initial_five_block_radius(self):
        def blocks(radius):
            return [
                {
                    **block,
                    "block": "grass block"
                    if block["position"] == "14,62,7"
                    else block["block"],
                }
                for block in self.floor()
            ]

        self.entry.getBlocksAround.side_effect = blocks
        moves = 0
        while moves <= 4:
            result = self.scan()
            if result["spots"]:
                break
            self.center = tuple(result["exploration"]["position"])
            moves += 1
        self.assertTrue(result["spots"])
        self.assertEqual(result["spots"][0]["position"], [14, 63, 7])
        self.assertGreater(moves, 0)
        self.assertLessEqual(moves, 4)
        self.assertGreater(len(self.state["visited"]), 1)

    def test_depth_limit_stops_even_on_an_endless_open_plain(self):
        self.entry.getBlocksAround.side_effect = lambda radius: self.floor()
        for _ in range(4):
            exploration = self.scan()["exploration"]
            self.center = tuple(exploration["position"])
        result = self.scan()["exploration"]
        self.assertEqual(result["status"], "search_budget_exhausted")
        self.assertEqual(result["moves_used"], 4)
        self.assertNotIn("next_action", result)

    def test_failed_goal_is_not_proposed_again_and_zero_depth_stays_local(self):
        self.entry.getBlocksAround.return_value = self.floor()
        proposed = self.scan()["exploration"]["position"]
        failure = {"target": proposed, "until": math.inf, "reason": "loop"}
        different = self.scan(failed_goals=[failure])["exploration"]
        self.assertGreater(math.dist(different["position"], proposed), 1.5)
        disabled = self.scan(search_steps=0)["exploration"]
        self.assertEqual(disabled["status"], "exploration_disabled")
        self.assertNotIn("next_action", disabled)

    def test_isolated_ground_reports_route_limit_and_keeps_solid_obstacles(self):
        self.entry.getBlocksAround.return_value = [
            {"position": "0,62,0", "block": "stone"}
        ]
        result = self.scan()
        self.assertEqual(result["exploration"]["status"], "no_safe_frontier")
        self.entry.getBlocksAround.return_value = self.floor() + [
            {"position": f"{x},63,{z}", "block": "tall grass"}
            for x in range(-5, 6)
            for z in range(-5, 6)
        ]
        self.assertEqual(self.scan()["exploration"]["status"], "move_to_scan")

    def test_config_is_bounded_and_species_change_resets_search_memory(self):
        self.entry.getBlocksAround.return_value = self.floor()
        self.scan()
        self.scan(sapling="birch_sapling")
        self.assertEqual(len(self.state["proposals"]), 1)
        for kwargs in (
            {"candidate_limit": 999},
            {"search_steps": 9},
            {"candidate_limit": True},
        ):
            with self.assertRaises(ValueError):
                self.scan(**kwargs)

    def test_natural_one_block_step_has_supported_feet_and_no_gap_is_invented(self):
        world = {
            (0, 62, 0): "stone",
            (1, 62, 0): "stone",
            (2, 63, 0): "grass block",
            (3, 63, 0): "grass block",
        }
        paths = trees._walk_paths(world, (0, 63, 0), FakeWorldRules(observed=world))
        self.assertIn((3, 64, 0), paths)
        self.assertNotIn((2, 65, 0), paths)
        world[(1, 65, 0)] = "oak planks"
        self.assertNotIn(
            (3, 64, 0),
            trees._walk_paths(world, (0, 63, 0), FakeWorldRules(observed=world)),
        )

    def test_final_plant_clearance_agrees_about_low_side_vegetation(self):
        self.entry.getBlockAt.side_effect = lambda x, y, z: (
            {"id": "minecraft:short_grass", "block": "short grass"}
            if (x, y, z) == (1, 63, 0)
            else {"id": "minecraft:air", "block": "air"}
        )
        self.assertTrue(trees._clearance(self.gateway, (0, 63, 0)))


class SearchIntegrationTests(unittest.TestCase):
    def test_exploration_has_separate_bounded_budget_from_material_continuations(self):
        continuation = ProcedureContinuation("Вырастить дуб")
        for step in range(4):
            command = f"@goto {step * 4 + 4} 63 0"
            continuation.observe(
                "grow_tree",
                {},
                {
                    "status": "needs_move",
                    "scan": {
                        "exploration": {"status": "move_to_scan", "command": command}
                    },
                },
            )
            with patch("saruman.procedures.time.monotonic", return_value=0):
                continuation.observe("agent_action", {"action": command}, True)
            with patch("saruman.procedures.time.monotonic", return_value=3):
                self.assertIn("Вырастить дуб", continuation.poll(False))
        self.assertEqual(continuation.remaining, 4)
        self.assertFalse(continuation.done)

    def test_grow_tree_propagates_exploration_as_next_action_without_clicking(self):
        action = {"tool": "agent_action", "arguments": {"action": "@goto 4 63 2"}}
        with (
            patch("saruman.trees._game_busy", return_value=False),
            patch(
                "saruman.trees.read_player_inventory",
                return_value={
                    "position": "0,63,0",
                    "items": [
                        {"item": "minecraft:oak_sapling", "count": 1},
                        {"item": "minecraft:bone_meal", "count": 3},
                    ],
                },
            ),
            patch(
                "saruman.trees.find_tree_spots",
                return_value={
                    "spots": [],
                    "existing_saplings": [],
                    "exploration": {"status": "move_to_scan", "next_action": action},
                },
            ),
            patch("saruman.trees.plant_sapling_at") as plant,
        ):
            result = trees.grow_tree_fast()
        self.assertEqual(result["status"], "needs_move")
        self.assertEqual(result["next_action"], action)
        plant.assert_not_called()


if __name__ == "__main__":
    unittest.main()
