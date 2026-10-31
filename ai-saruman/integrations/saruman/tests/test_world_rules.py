"""Native predicates, failure boundaries and no world mutation while inspecting."""

import unittest
from unittest.mock import Mock, patch

from saruman.world_rules import NativeWorldRules


class NativeRulesTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock()
        self.gateway.new_array.side_effect = lambda kind, n: [None] * n
        self.world = self.gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getWorld.return_value
        self.state = self.world.method_8320.return_value
        self.shape = self.state.method_26220.return_value
        self.shape.method_1110.return_value = False
        self.shape.method_1105.return_value = 1.0
        self.state.method_26206.return_value = True
        self.state.method_26227.return_value.method_15769.return_value = True
        self.rules = NativeWorldRules(self.gateway)
        self.methods = {}
        factory = patch.object(self.rules, "_method", side_effect=self.method)
        factory.start()
        self.addCleanup(factory.stop)
        self.method("", "method_58").invoke.return_value = "BLOCKED"

    def method(self, cls, name, parameters=()):
        return self.methods.setdefault(name, Mock())

    def test_unknown_solid_uses_collision_and_surface_not_a_name_list(self):
        self.assertTrue(self.rules.support((0, 62, 0)))
        self.assertFalse(self.rules.free((0, 62, 0)))
        self.world.method_8320.assert_called_once()
        self.state.method_26220.assert_called_once()
        self.state.getName.assert_not_called()
        self.world.setBlockState.assert_not_called()

    def test_empty_collision_can_be_walked_even_if_block_is_not_replaceable(self):
        self.shape.method_1110.return_value = True
        self.state.isReplaceable.return_value = False
        self.assertTrue(self.rules.free((0, 63, 0)))
        self.assertFalse(self.rules.support((0, 63, 0)))

    def test_fluid_and_native_damage_nodes_are_rejected(self):
        for fluid, node in [
            (True, "BLOCKED"),
            (False, "DAMAGE_FIRE"),
            (False, "DAMAGE_OTHER"),
            (False, "POWDER_SNOW"),
        ]:
            with self.subTest(fluid=fluid, node=node):
                self.rules.cells.clear()
                self.state.method_26227.return_value.method_15769.return_value = (
                    not fluid
                )
                self.method("", "method_58").invoke.return_value = node
                self.assertFalse(self.rules.support((0, 62, 0)))
                self.shape.method_1110.return_value = True
                self.assertFalse(self.rules.free((1, 63, 0)))
                self.shape.method_1110.return_value = False

    def test_half_height_and_tall_collision_are_not_integer_height_supports(self):
        for height in (0.5, 0.9375, 1.5):
            self.shape.method_1105.return_value = height
            self.rules.cells.clear()
            self.assertFalse(self.rules.support((0, 62, 0)))
        self.shape.method_1105.return_value = 1.0
        self.rules.cells.clear()
        self.assertTrue(self.rules.support((0, 62, 0)))

    def test_only_actual_grounded_partial_perch_can_seed_a_dry_departure(self):
        from saruman.trees import _walk_paths

        player = self.gateway.jvm.adris.altoclef.AltoClef.getInstance.return_value.getPlayer.return_value
        player.method_23317.return_value = 0.5
        player.method_23318.return_value = 62.9375
        player.method_23321.return_value = 0.5
        player.method_24828.return_value = True
        observed = {(0, 62, 0): "farmland", (1, 62, 0): "stone"}
        self.rules.observed = observed
        self.rules.center, self.rules.radius = (0, 62, 0), 5
        self.rules.cells[(0, 62, 0)] = {
            "free": False,
            "surface_water": False,
            "support": False,
            "shore_support": True,
            "height": 0.9375,
        }
        self.assertTrue(self.rules.can_depart_partial((0, 62, 0)))
        paths = _walk_paths(observed, (0, 62, 0), self.rules)
        self.assertIn((1, 63, 0), paths)
        self.assertFalse(self.rules.support((0, 62, 0)))
        player.method_24828.return_value = False
        self.assertEqual(_walk_paths(observed, (0, 62, 0), self.rules), {})
        player.method_24828.return_value = True
        player.method_23318.return_value = 62.3
        self.assertFalse(self.rules.can_depart_partial((0, 62, 0)))

    def test_rescue_can_climb_farmland_and_slabs_without_relaxing_normal_walks(self):
        for height in (0.5, 0.9375):
            self.shape.method_1105.return_value = height
            self.rules.cells.clear()
            self.assertFalse(self.rules.support((0, 62, 0)))
            self.assertTrue(self.rules.shore_support((0, 62, 0)))
        self.state.method_26227.return_value.method_15769.return_value = False
        self.rules.cells.clear()
        self.assertFalse(self.rules.shore_support((0, 62, 0)))

    def test_missing_cells_are_air_only_inside_the_observed_cube(self):
        self.rules.center = (0, 63, 0)
        self.rules.radius = 2
        self.rules.observed = {}
        self.assertTrue(self.rules.free((2, 64, 0)))
        self.assertFalse(self.rules.free((3, 64, 0)))
        self.assertFalse(self.rules.support((0, 62, 0)))
        self.world.method_8320.assert_not_called()

    def test_sapling_support_is_queried_and_cached_without_soil_names(self):
        item = Mock()
        self.gateway.jvm.adris.altoclef.TaskCatalogue.getItemMatches.return_value = [
            item
        ]
        item.method_7711.return_value.method_9564.return_value.method_26184.side_effect = [
            True,
            False,
        ]
        self.assertTrue(self.rules.can_plant("oak_sapling", (0, 63, 0)))
        self.assertFalse(self.rules.can_plant("oak_sapling", (2, 63, 0)))
        self.gateway.jvm.adris.altoclef.TaskCatalogue.getItemMatches.assert_called_once()

    def test_native_height_check_replaces_the_fixed_eight_block_prism(self):
        config, feature = Mock(), Mock()
        self.rules.configs["oak_sapling"] = (config, 4, feature)
        self.method("", "method_31607").invoke.return_value = -64
        self.method("", "method_31605").invoke.return_value = 384
        top = self.method("", "method_29963")
        top.invoke.return_value = 4
        self.assertTrue(self.rules.growth_space("oak_sapling", (0, 63, 0)))
        args = top.invoke.call_args.args[1]
        self.assertEqual(args[1], 4)
        self.assertIs(args[3], config)
        top.invoke.return_value = 3
        self.assertFalse(self.rules.growth_space("oak_sapling", (0, 63, 0)))
        feature.generate.assert_not_called()
        self.world.setBlockState.assert_not_called()
        self.world.getRandom.assert_not_called()

    def test_dimension_limit_and_unknown_species_do_not_call_generation(self):
        self.rules.configs["oak_sapling"] = (Mock(), 4, Mock())
        self.method("", "method_31607").invoke.return_value = -64
        self.method("", "method_31605").invoke.return_value = 384
        self.assertFalse(self.rules.growth_space("oak_sapling", (0, 319, 0)))
        self.assertFalse(self.rules.growth_space("oak_sapling", (0, -64, 0)))
        self.assertNotIn("method_29963", self.methods)
        with self.assertRaises(ValueError):
            self.rules.growth_space("jungle_sapling", (0, 63, 0))

    def test_native_api_failure_does_not_invent_a_clear_route(self):
        self.world.method_8320.side_effect = RuntimeError("native call failed")
        with self.assertRaisesRegex(RuntimeError, "native call failed"):
            self.rules.support((0, 62, 0))
        self.assertFalse(self.rules.cells)

    def test_water_surface_is_allowed_with_clear_head_but_not_underwater_or_lava(self):
        self.shape.method_1110.return_value = True
        self.state.method_26227.return_value.method_15769.return_value = False
        self.method("", "method_58").invoke.return_value = "WATER"
        self.assertTrue(self.rules.free((0, 63, 0), allow_water=True))
        self.assertFalse(self.rules.free((0, 63, 0)))
        self.rules.cells[(0, 64, 0)] = {
            "free": True,
            "support": False,
            "surface_water": False,
        }
        self.rules.cells[(0, 62, 0)] = {
            "free": False,
            "support": False,
            "surface_water": True,
        }
        self.assertTrue(self.rules.standing((0, 63, 0)))
        self.rules.cells[(0, 64, 0)] = {
            "free": False,
            "support": False,
            "surface_water": True,
        }
        self.assertFalse(self.rules.standing((0, 63, 0)))
        self.rules.cells.clear()
        self.method("", "method_58").invoke.return_value = "LAVA"
        self.assertFalse(self.rules.free((0, 63, 0), allow_water=True))

    def test_search_stays_dry_and_only_rescue_may_cross_observed_surface_water(self):
        from saruman.trees import _walk_paths

        observed = {
            (0, 62, 0): "stone",
            (1, 63, 0): "water",
            (2, 63, 0): "water",
            (3, 62, 0): "stone",
        }
        self.rules.observed = observed
        self.rules.center = (0, 63, 0)
        properties = {
            (0, 62, 0): {"free": False, "support": True, "surface_water": False},
            (3, 62, 0): {"free": False, "support": True, "surface_water": False},
            (1, 63, 0): {"free": False, "support": False, "surface_water": True},
            (2, 63, 0): {"free": False, "support": False, "surface_water": True},
        }
        self.rules.cell = Mock(
            side_effect=lambda p: properties.get(
                p, {"free": True, "support": False, "surface_water": False}
            )
        )
        paths = _walk_paths(observed, (0, 63, 0), self.rules)
        self.assertNotIn((3, 63, 0), paths)
        self.assertIn(
            (3, 63, 0), _walk_paths(observed, (0, 63, 0), self.rules, allow_water=True)
        )
        properties[(1, 64, 0)] = {
            "free": False,
            "support": False,
            "surface_water": True,
        }
        observed[(1, 64, 0)] = "water"
        observed[(1, 65, 0)] = "stone"
        properties[(1, 65, 0)] = {
            "free": False,
            "support": True,
            "surface_water": False,
        }
        self.assertNotIn(
            (3, 63, 0), _walk_paths(observed, (0, 63, 0), self.rules, allow_water=True)
        )


if __name__ == "__main__":
    unittest.main()
