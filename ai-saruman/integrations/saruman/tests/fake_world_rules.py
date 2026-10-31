"""Small explicit block-property fixture for existing navigation/tree scenarios."""


class FakeWorldRules:
    def __init__(self, gateway=None, observed=None, center=None, radius=5):
        self.entry = gateway.entry_point if gateway is not None else None
        self.observed = observed
        self.center, self.radius = center, radius

    def _world(self):
        if self.observed is not None:
            return self.observed
        from saruman.trees import _position

        return {
            _position(b["position"]): b["block"] for b in self.entry.getBlocksAround(5)
        }

    def inside(self, p):
        return self.center is None or all(
            abs(p[i] - self.center[i]) <= self.radius for i in range(3)
        )

    def free(self, p, allow_water=False):
        return self.inside(p) and (
            p not in self._world()
            or self._world()[p]
            in {
                "short grass",
                "tall grass",
                "fern",
                "large fern",
                "dandelion",
                "poppy",
                "dead bush",
            }
        )

    def support(self, p):
        kind = self._world().get(p)
        return (
            self.inside(p)
            and kind is not None
            and kind
            not in (
                "cactus",
                "magma block",
                "water",
                "lava",
                "oak slab",
                "sandstone stairs",
                "short grass",
                "tall grass",
                "fern",
                "large fern",
                "dandelion",
                "poppy",
            )
        )

    def standing(self, p):
        return (
            self.support((p[0], p[1] - 1, p[2]))
            and self.free(p)
            and self.free((p[0], p[1] + 1, p[2]))
        )

    def can_plant(self, sapling, p):
        return self._world().get((p[0], p[1] - 1, p[2])) in {
            "grass block",
            "dirt",
            "coarse dirt",
            "rooted dirt",
            "podzol",
            "moss block",
        }

    def growth_space(self, sapling, p):
        # Fixture obstacle oracle; production delegates all geometry to Minecraft.
        height = 4 if sapling == "oak_sapling" else 5
        for dy in range(height + 2):
            radius = 0 if dy == 0 else 1
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    q = (p[0] + dx, p[1] + dy, p[2] + dz)
                    info = self.entry.getBlockAt(*q) if self.entry is not None else {}
                    kind = self._world().get(
                        q,
                        info.get(
                            "block",
                            info.get("id", "minecraft:air")
                            .removeprefix("minecraft:")
                            .replace("_", " "),
                        ),
                    )
                    if kind not in (
                        "air",
                        "short grass",
                        "tall grass",
                        "fern",
                        "large fern",
                        "dandelion",
                        "poppy",
                        "oak sapling",
                        "birch sapling",
                    ):
                        return False
        return True
