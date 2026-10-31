"""Read-only Minecraft 1.21.11 collision, sapling and ordinary-tree predicates.

Yarn mappings: AbstractBlockState, LandPathNodeMaker and TreeFeature. Never call
generate/randomTick or a world setter: getTopPosition only tests existing states.
"""

from py4j.java_gateway import get_field


class NativeWorldRules:
    def __init__(self, gateway, observed=None, center=None, radius=5):
        self.gateway = gateway
        self.jvm = gateway.jvm
        self.world = self.jvm.adris.altoclef.AltoClef.getInstance().getWorld()
        if self.world is None:
            raise RuntimeError("Minecraft world is not available for native checks")
        self.observed, self.center, self.radius = observed, center, radius
        self.cells, self.saplings, self.configs, self.methods = {}, {}, {}, {}

    def _method(self, cls, name, parameters=()):
        key = (cls, name, parameters)
        if key not in self.methods:
            types = self.gateway.new_array(self.jvm.java.lang.Class, len(parameters))
            for i, parameter in enumerate(parameters):
                types[i] = (
                    self.jvm.java.lang.Integer.TYPE
                    if parameter == "int"
                    else self.jvm.java.lang.Class.forName(parameter)
                )
            method = self.jvm.java.lang.Class.forName(cls).getDeclaredMethod(
                name, types
            )
            method.setAccessible(True)
            self.methods[key] = method
        return self.methods[key]

    def _invoke(self, method, owner, *values):
        args = self.gateway.new_array(self.jvm.java.lang.Object, len(values))
        for i, value in enumerate(values):
            args[i] = value
        return method.invoke(owner, args)

    def position(self, p):
        return self.jvm.net.minecraft.class_2338(*p)

    def inside(self, p):
        return self.center is None or all(
            abs(p[i] - self.center[i]) <= self.radius for i in range(3)
        )

    def cell(self, p):
        p = tuple(p)
        if p not in self.cells:
            pos = self.position(p)
            state = self.world.method_8320(pos)
            shape = state.method_26220(self.world, pos)
            empty = bool(shape.method_1110())
            fluid = not bool(state.method_26227().method_15769())
            node_method = self._method(
                "net.minecraft.class_14",
                "method_58",
                ("net.minecraft.class_1922", "net.minecraft.class_2338"),
            )
            node = str(self._invoke(node_method, None, self.world, pos))
            danger = node.startswith(("DAMAGE", "DANGER")) or node in (
                "LAVA",
                "POWDER_SNOW",
            )
            height = (
                0
                if empty
                else float(
                    shape.method_1105(
                        self.jvm.net.minecraft.class_2350.class_2351.field_11052
                    )
                )
            )
            top = (
                not empty
                and abs(height - 1) < 1e-6
                and bool(
                    state.method_26206(
                        self.world, pos, self.jvm.net.minecraft.class_2350.field_11036
                    )
                )
            )
            self.cells[p] = {
                "height": height,
                "free": empty and not fluid and not danger,
                "surface_water": empty and fluid and node == "WATER" and not danger,
                "support": top and not fluid and not danger,
                "shore_support": 0.5 <= height <= 1 and not fluid and not danger,
                "node": node,
            }
        return self.cells[p]

    def free(self, p, allow_water=False):
        if not self.inside(p):
            return False
        if self.observed is not None and p not in self.observed:
            return True  # getBlocksAround contains all non-air cells inside its cube.
        cell = self.cell(p)
        return cell["free"] or (allow_water and cell["surface_water"])

    def standing(self, p):
        x, y, z = p
        return (
            self.free(p, allow_water=True)
            and self.free((x, y + 1, z))
            and (self.support((x, y - 1, z)) or self.cell(p)["surface_water"])
        )

    def support(self, p):
        if not self.inside(p) or (self.observed is not None and p not in self.observed):
            return False
        return self.cell(p)["support"]

    def shore_support(self, p):
        """Swimming can climb partial-height banks; ordinary integer walks stay strict."""
        if not self.inside(p) or (self.observed is not None and p not in self.observed):
            return False
        return self.cell(p)["shore_support"]

    def can_depart_partial(self, p):
        """Allow leaving an actual slab/farmland perch, never targeting it as dry ground."""
        import math

        cell = self.cell(p)
        if not cell["shore_support"] or cell["support"]:
            return False
        player = self.jvm.adris.altoclef.AltoClef.getInstance().getPlayer()
        position = (
            float(player.method_23317()),
            float(player.method_23318()),
            float(player.method_23321()),
        )
        return (
            tuple(math.floor(v) for v in position) == tuple(p)
            and bool(player.method_24828())
            and abs(position[1] - p[1] - cell["height"]) < 0.1
            and self.free((p[0], p[1] + 1, p[2]))
            and self.free((p[0], p[1] + 2, p[2]))
        )

    def can_plant(self, sapling, p):
        if sapling not in self.saplings:
            matches = self.jvm.adris.altoclef.TaskCatalogue.getItemMatches(sapling)
            if not matches:
                raise RuntimeError(
                    "Sapling is absent from the Minecraft item catalogue"
                )
            self.saplings[sapling] = matches[0].method_7711().method_9564()
        return bool(self.saplings[sapling].method_26184(self.world, self.position(p)))

    def growth_space(self, sapling, p):
        """Check the native minimum-height ordinary oak/birch variant, without RNG or writes.

        Vanilla client configuration is a feasibility test, not a promise about a
        server's datapacks, a random taller/fancy variant, or one bone-meal dose.
        """
        if sapling not in ("oak_sapling", "birch_sapling"):
            raise ValueError("Native growth check supports ordinary oak and birch")
        if sapling not in self.configs:
            builder_method = self._method(
                "net.minecraft.class_6808",
                "method_39710" if sapling == "oak_sapling" else "method_39712",
            )
            config = self._invoke(builder_method, None).method_23445()
            base = self.jvm.java.lang.Class.forName(
                "net.minecraft.class_5141"
            ).getDeclaredField("field_23760")
            base.setAccessible(True)
            height = int(base.getInt(get_field(config, "field_24136")))
            feature = self.jvm.net.minecraft.class_2944(
                self.jvm.net.minecraft.class_4643.field_24921
            )
            self.configs[sapling] = config, height, feature
        config, height, feature = self.configs[sapling]
        bottom = int(
            self._invoke(
                self._method("net.minecraft.class_5539", "method_31607"), self.world
            )
        )
        world_height = int(
            self._invoke(
                self._method("net.minecraft.class_5539", "method_31605"), self.world
            )
        )
        if p[1] < bottom + 1 or p[1] + height + 1 >= bottom + world_height:
            return False
        method = self._method(
            "net.minecraft.class_2944",
            "method_29963",
            (
                "net.minecraft.class_3746",
                "int",
                "net.minecraft.class_2338",
                "net.minecraft.class_4643",
            ),
        )
        return (
            int(
                self._invoke(
                    method, feature, self.world, height, self.position(p), config
                )
            )
            >= height
        )
