"""Agent combat is available only through the exact, hostile-only attack tool."""

HOSTILES = frozenset(
    {
        "zombie",
        "husk",
        "drowned",
        "skeleton",
        "stray",
        "bogged",
        "wither_skeleton",
        "creeper",
        "witch",
        "pillager",
        "vindicator",
        "evoker",
        "vex",
        "ravager",
        "phantom",
        "blaze",
        "ghast",
        "slime",
        "magma_cube",
        "silverfish",
        "endermite",
        "shulker",
        "guardian",
        "elder_guardian",
        "zoglin",
        "breeze",
        "cave_spider",
        "warden",
        "wither",
        "ender_dragon",
    }
)

CROP_MATERIALS = frozenset(
    {
        "wheat_seeds",
        "beetroot_seeds",
        "wheat",
        "carrot",
        "potato",
        "beetroot",
    }
)
CROP_BLOCKS = frozenset({"wheat", "carrots", "potatoes", "beetroots", "farmland"})


def guard_crop_inputs(command, inventory=None):
    """Block native crop gathering, and require harvested wheat before bread crafting."""
    parts = command.strip().lstrip("@$/!").lower().split()
    if len(parts) < 2:
        return
    name, item = parts[0], parts[1].removeprefix("minecraft:")
    if (
        (name == "get" and item in CROP_MATERIALS | CROP_BLOCKS)
        or (name == "mineblock" and item in CROP_BLOCKS)
        or (name == "place" and item in CROP_MATERIALS | CROP_BLOCKS)
    ):
        raise ValueError(
            "Use farm_crops to gather planting supplies from mature crops only. "
            "Native crop gathering/placement may destroy young plants and is disabled."
        )
    if name == "get" and item == "bread" and inventory is not None:
        counts = {}
        for slot in inventory.get("slots", inventory.get("items", [])):
            key = slot.get("item", "").removeprefix("minecraft:")
            counts[key] = counts.get(key, 0) + slot.get("count", 0)
        target = int(parts[2]) if len(parts) > 2 else 1
        needed = max(0, target - counts.get("bread", 0)) * 3
        if counts.get("wheat", 0) < needed:
            raise ValueError(
                "Bread crafting requires enough already harvested wheat in inventory. "
                "Use farm_crops to harvest mature wheat first; young crops stay intact."
            )


def hostile_type(identifier):
    # Endermen, spiders, piglins, animals, golems, villagers and unknown mods stay out.
    return (
        identifier == "magicalscepter:sorcerer"
        or identifier in HOSTILES
        or identifier in {"minecraft:" + name for name in HOSTILES}
    )


def install_native_guard(gateway):
    """Exclude entities from native automatic attacks; explicit attacks use exact targets.

    This JVM predicate has no Python callback and survives a Python disconnection.
    """
    from saruman.world_actions import on_client, set_int_on_client

    jvm = gateway.jvm
    handles = jvm.java.lang.invoke.MethodHandles
    handle = handles.constant(jvm.java.lang.Class.forName("java.lang.Boolean"), True)
    types = gateway.new_array(jvm.java.lang.Class, 1)
    types[0] = jvm.java.lang.Class.forName("java.lang.Object")
    handle = handles.dropArguments(handle, 0, types)
    predicate = jvm.java.lang.invoke.MethodHandleProxies.asInterfaceInstance(
        jvm.java.lang.Class.forName("java.util.function.Predicate"), handle
    )
    behaviour = jvm.adris.altoclef.AltoClef.getInstance().getBehaviour()
    # Protect parent states too: completing a pre-existing task must not pop away
    # the prohibition. Native push() copies the protected lists into child states.
    states_field = behaviour.getClass().getDeclaredField("states")
    states_field.setAccessible(True)
    for state in states_field.get(behaviour).toArray():
        for name in ("attackExclusions", "excludeFromForceField"):
            field = state.getClass().getDeclaredField(name)
            field.setAccessible(True)
            on_client(
                gateway,
                field.get(state),
                "java.util.List",
                "add",
                ("java.lang.Object",),
                (predicate,),
            )
    for name in ("addAttackExclusion", "addForceFieldExclusion"):
        on_client(
            gateway,
            behaviour,
            "adris.altoclef.BotBehaviour",
            name,
            ("java.util.function.Predicate",),
            (predicate,),
        )
    # FoodChain can silently start CollectFoodTask, which may hunt livestock.
    # Keep auto-eating existing food, but let our crop/bread planner supply it.
    settings = jvm.adris.altoclef.AltoClef.getInstance().getModSettings()
    for name in ("minimumFoodAllowed", "foodUnitsToCollect"):
        set_int_on_client(gateway, settings, name, 0)
    return {
        "status": "hostile_tool_only",
        "native_automatic_attacks": False,
        "native_food_collection": False,
    }


def guard_command(command):
    """Apply also at bridge dispatch, so chat/aliases cannot bypass tool validation."""
    parts = command.strip().lstrip("@$/!").lower().split()
    guard_crop_inputs(command)
    if not parts:
        return
    name = parts[0]
    unsafe = {
        "attack",
        "strike",
        "combat",
        "pursue",
        "chase",
        "shoot",
        "shootat",
        "punk",
        "hero",
        "agent",
        "gamer",
        "dummy",
        "marvion",
        "custom2",
        "test",
        "meat",
        "food",
        "kill",
        "damage",
        "summon",
        "execute",
        "game",
    }
    if name in unsafe or name == "t":
        raise ValueError(
            "Use guarded hunt_hostiles/attack_hostile/cast_scepter: villagers, animals and golems are protected; players require native damage proof. Raw combat/meat tasks are disabled."
        )
    allowed = {
        "goto",
        "follow",
        "stop",
        "cancel",
        "get",
        "inventory",
        "coords",
        "status",
        "help",
        "check_block",
        "check_player",
        "scan",
        "pause",
        "unpause",
        "resume",
        "gesture",
        "nick",
        "shift",
        "equip",
        "timeout",
        "avoid",
        "alive",
        "idle",
        "wander",
        "place",
        "mineblock",
        "build",
        "deposit",
        "stash",
        "give",
        "sign",
        "coverwithsand",
        "coverwithblocks",
        "grave",
        "locate_structure",
        "connect",
        "gamma",
        "list",
        "say",
        "msg",
        "tell",
        "w",
        "me",
        "tpa",
        "tpaccept",
        "tpdeny",
        "spawn",
        "home",
        "sethome",
        "back",
        "warp",
        "lobby",
    }
    if name not in allowed:
        raise ValueError(
            "This command has no checked peaceful execution path; use the registered farming, movement or hostile-only tools."
        )
    if name == "get" and len(parts) > 1:
        item = parts[1].removeprefix("minecraft:")
        if (
            any(
                word in item
                for word in (
                    "beef",
                    "porkchop",
                    "chicken",
                    "mutton",
                    "rabbit",
                    "leather",
                    "feather",
                    "wool",
                    "salmon",
                    "cod",
                    "pufferfish",
                    "tropical_fish",
                    "ink_sac",
                    "scute",
                    "turtle_helmet",
                    "book",
                    "item_frame",
                    "arrow",
                    "bundle",
                    "carpet",
                    "banner",
                    "painting",
                    "brush",
                    "spider_eye",
                )
            )
            or item in {"bed", "food", "meat"}
            or item.endswith("_bed")
        ):
            raise ValueError(
                "Native collection may kill animals for this item. Use plant-based food and farm_crops instead."
            )
