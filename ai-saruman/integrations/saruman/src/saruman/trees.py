"""Bounded tree actions: scan locally once, validate in Minecraft, then interact."""

import math
import os
import time
from collections import deque
from contextlib import ExitStack

from py4j.java_gateway import GatewayParameters, JavaGateway

from saruman.fertilize import apply_bone_meal_at, select_inventory_item
from saruman.minecraft import _java_to_python, read_player_inventory
from saruman.terrain_search import plan_exploration
from saruman.world_rules import NativeWorldRules

# These procedures deliberately handle single-sapling oak/birch trees.
TREE_TYPES = {"oak_sapling", "birch_sapling"}
MAX_GROWTH_ATTEMPTS = 5


def _sapling_name(sapling):
    name = sapling.removeprefix("minecraft:")
    if name not in TREE_TYPES:
        raise ValueError(
            "Fast tree procedure supports oak_sapling or birch_sapling; do not treat 2x2 trees as one sapling"
        )
    return name


def _gateway():
    return JavaGateway(
        gateway_parameters=GatewayParameters(
            address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
            port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
            read_timeout=5,
            auto_convert=True,
        )
    )


def _position(value):
    return tuple(math.floor(float(part.strip())) for part in value.split(","))


def _walk_paths(observed, center, rules, *, allow_water=False, routes=None):
    """Short connected dry walks; surface water is reserved for emergency escape.

    No diving, gaps, pillars or bridges. An actual grounded partial-height start
    may leave for supported full blocks; partial blocks are not destinations.
    Native routing and the runtime progress guard decide actual execution.
    """

    free = rules.free

    def walkable(p):
        return rules.standing(p) and (
            allow_water or (rules.free(p) and rules.support((p[0], p[1] - 1, p[2])))
        )

    partial_start = False
    if not walkable(center):
        partial_start = bool(
            getattr(rules, "can_depart_partial", lambda p: False)(center)
        )
        if not partial_start:
            return {}
    paths = {center: 0}
    if routes is not None:
        routes[center] = []
    queue = deque([center])
    while queue:
        x, y, z = p = queue.popleft()
        if paths[p] >= 8:
            continue
        for nx, nz in ((x - 1, z), (x + 1, z), (x, z - 1), (x, z + 1)):
            for ny in (y, y + 1, y - 1):
                if partial_start and p == center and ny < y:
                    continue
                q = (nx, ny, nz)
                if q in paths or not walkable(q):
                    continue
                if ny > y and not free((x, y + 2, z)):
                    continue
                if ny < y and not free((nx, y + 1, nz)):
                    continue
                paths[q] = paths[p] + 1
                if routes is not None:
                    routes[q] = routes[p] + [q]
                queue.append(q)
    return paths


def _tree_approaches(observed, center, targets, paths=None, rules=None):
    paths = _walk_paths(observed, center, rules) if paths is None else paths
    results = []
    for target in targets:
        target = tuple(target)
        choices = [p for p in paths if p != target and 0 < math.dist(p, target) <= 2.5]
        if not choices:
            continue
        standing = min(choices, key=lambda p: (paths[p], math.dist(p, target)))
        results.append(
            {
                "plant_position": list(target),
                "standing_position": list(standing),
                "walk_steps": paths[standing],
                "command": "@goto " + " ".join(map(str, standing)),
            }
        )
    return results


def _game_busy():
    gateway = _gateway()
    try:
        if not gateway.entry_point.inGame():
            raise RuntimeError("Minecraft world is not open")
        return bool(gateway.entry_point.hasActiveTask())
    finally:
        gateway.close()


def _can_plant(gateway, sapling, position, rules=None):
    return (rules or NativeWorldRules(gateway)).can_plant(sapling, position)


def _clearance(gateway, position, sapling="oak_sapling", rules=None):
    return (rules or NativeWorldRules(gateway)).growth_space(sapling, position)


def find_tree_spots(
    sapling="oak_sapling",
    radius=5,
    candidate_limit=128,
    search_steps=4,
    search_state=None,
    failed_goals=(),
    allow_exploration=True,
    avoid_positions=(),
    min_spacing=0,
):
    sapling = _sapling_name(sapling)
    if type(radius) is not int or not 2 <= radius <= 5:
        raise ValueError("Tree search radius must be an integer from 2 to 5")
    if type(candidate_limit) is not int or not 24 <= candidate_limit <= 256:
        raise ValueError("candidate_limit must be an integer from 24 to 256")
    if type(search_steps) is not int or not 0 <= search_steps <= 8:
        raise ValueError("search_steps must be an integer from 0 to 8")
    search_state = {} if search_state is None else search_state
    if search_state.get("sapling") != sapling:
        search_state.clear()
        search_state["sapling"] = sapling
    start = time.monotonic()
    gateway = _gateway()
    try:
        entry = gateway.entry_point
        if not entry.inGame():
            raise RuntimeError("Minecraft world is not open")
        state = _java_to_python(entry.getGameState()).get("self", {})
        center = tuple(math.floor(float(p.strip())) for p in state["pos"].split(","))
        blocks = _java_to_python(entry.getBlocksAround(radius))
        observed = {_position(block["position"]): block["block"] for block in blocks}
        rules = NativeWorldRules(gateway, observed, center, radius)
        paths = _walk_paths(observed, center, rules)
        cache = {}
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    p = (center[0] + dx, center[1] + dy, center[2] + dz)
                    cache[p] = p not in observed

        def distance(p):
            return math.dist(center, p)

        def reachable(p):
            return distance(p) <= 3.5 or any(0 < math.dist(q, p) <= 2.5 for q in paths)

        existing = sorted(
            (p for p, block in observed.items() if block == sapling.replace("_", " ")),
            key=distance,
        )
        soil_cells = [
            (x, y + 1, z)
            for (x, y, z), block in observed.items()
            if abs(y + 1 - center[1]) <= radius
        ]
        # Buried dirt and occupied cells are not planting candidates. Filtering
        # them before the budget prevents underground layers starving the surface.
        surface_candidates = sorted(
            (p for p in soil_cells if p != center and cache.get(p, False)),
            key=lambda p: (not reachable(p), distance(p)),
        )
        candidates = [
            p for p in surface_candidates if _can_plant(gateway, sapling, p, rules)
        ]
        spots, checked = [], 0
        rejected = {
            "occupied": len(soil_cells) - len(surface_candidates),
            "growth_space": 0,
            "soil_rule": len(surface_candidates) - len(candidates),
            "placement_policy": 0,
        }
        for p in candidates[:candidate_limit]:
            checked += 1
            if any(
                math.hypot(p[0] - q[0], p[2] - q[2]) < min_spacing
                for q in list(avoid_positions) + [s["position"] for s in spots]
            ):
                continue
            if not _clearance(gateway, p, sapling, rules):
                rejected["growth_space"] += 1
                continue
            policy = _java_to_python(entry.canPlaceBlock(*p))
            if not policy.get("canPlace"):
                rejected["placement_policy"] += 1
                continue
            spots.append(
                {
                    "position": list(p),
                    "distance": round(distance(p), 2),
                    "within_reach": distance(p) <= 3.5,
                }
            )
            if len(spots) == 3:
                break
        usable_existing = []
        for p in existing[:3]:
            if any(
                math.hypot(p[0] - q[0], p[2] - q[2]) < min_spacing
                for q in avoid_positions
            ):
                continue
            was_air = cache.get(p, False)
            cache[p] = True  # Allow the sapling itself when checking canopy clearance.
            policy = _java_to_python(entry.canPlaceBlock(*p))
            if (
                policy.get("policyAllows") is not False
                and _clearance(gateway, p, sapling, rules)
                and _can_plant(gateway, sapling, p, rules)
            ):
                usable_existing.append(list(p))
            cache[p] = was_air
        approaches = _tree_approaches(
            observed, center, [s["position"] for s in spots] + usable_existing, paths
        )
        approaches = [
            a
            for a in approaches
            if not any(
                f["until"] > time.time()
                and math.dist(a["standing_position"], f["target"]) <= 1.5
                for f in failed_goals
            )
        ]
        in_reach = any(s["within_reach"] for s in spots) or any(
            distance(p) <= 3.5 for p in usable_existing
        )
        exploration = None
        if (
            allow_exploration
            and not in_reach
            and not any(a["walk_steps"] > 0 for a in approaches)
        ):
            exploration = plan_exploration(
                paths, center, search_state, search_steps, failed_goals, radius
            )
        return {
            "sapling": sapling,
            "spots": spots,
            "existing_saplings": usable_existing,
            "approaches": approaches,
            "navigation": {
                "standing_on": observed.get((center[0], center[1] - 1, center[2])),
                "reachable_cells": len(paths),
            },
            "exploration": exploration,
            "rejected": rejected,
            "radius": radius,
            "candidates_checked": checked,
            "candidate_limit": candidate_limit,
            "candidate_count": len(candidates),
            "native_soil_checked": len(surface_candidates),
            "soil_cells_seen": len(soil_cells),
            "search_limited": len(candidates) > checked,
            "elapsed_ms": round((time.monotonic() - start) * 1000),
            "search_complete": len(candidates) <= checked,
            "growth_space": "Minecraft ordinary oak/birch minimum-height clearance; vanilla client configuration; random/server-specific growth is not guaranteed",
            "hint": "Use a within_reach site now, otherwise a supported approach (natural one-block steps allowed). If none, follow exploration.next_action and rescan after walking. Radius 5 is a local snapshot, not a global search. Report rejected counts or the exploration limit only after trying available new areas. Never invent Y or repeat a failed route.",
        }
    finally:
        gateway.close()


def tree_approach(
    position,
    failed_goals=(),
    allow_partial=False,
    previous_waypoints=(),
    one_step=False,
):
    """Read one short observed approach to a persisted task target."""
    gateway = _gateway()
    try:
        entry = gateway.entry_point
        state = _java_to_python(entry.getGameState()).get("self", {})
        center = _position(state["pos"])
        observed = {
            _position(b["position"]): b["block"]
            for b in _java_to_python(entry.getBlocksAround(5))
        }
        rules = NativeWorldRules(gateway, observed, center, 5)
        routes = {}
        paths = {
            p: steps
            for p, steps in _walk_paths(observed, center, rules, routes=routes).items()
            if not (one_step and routes[p] and list(routes[p][0]) in previous_waypoints)
            if not any(
                f["until"] > time.time()
                and any(
                    math.dist(q, f["target"]) <= 1.5
                    for q in (routes[p] if one_step else [p])
                )
                for f in failed_goals
            )
        }

        def short_step(approach):
            if approach is None or not one_step:
                return approach
            route = routes[tuple(approach["standing_position"])]
            if not route:
                return None
            step = route[0]
            return {
                **approach,
                "standing_position": list(step),
                "walk_steps": 1,
                "dry_path": [list(p) for p in route],
                "command": "@goto " + " ".join(map(str, step)),
            }

        approaches = _tree_approaches(observed, center, [position], paths)
        direct = next((a for a in approaches if a["walk_steps"] > 0), None)
        if direct or not allow_partial:
            return short_step(direct)

        # Far destinations need successive observed waypoints. Never abandon the
        # persisted planting target just because it lies outside this local map.
        def distance(p):
            return math.dist(p, position)

        options = [
            p
            for p in paths
            if paths[p] > 0
            and p != tuple(position)
            and distance(p) < distance(center) - 1
            and not any(math.dist(p, old) < 1.5 for old in previous_waypoints)
        ]
        if not options:
            return None
        waypoint = min(options, key=lambda p: (distance(p), paths[p]))
        return short_step(
            {
                "plant_position": list(position),
                "standing_position": list(waypoint),
                "walk_steps": paths[waypoint],
                "partial": True,
                "command": "@goto " + " ".join(map(str, waypoint)),
            }
        )
    finally:
        gateway.close()


def plant_sapling_at(x, y, z, sapling="oak_sapling"):
    sapling = _sapling_name(sapling)
    if any(type(n) is not int for n in (x, y, z)):
        raise ValueError("Use integer coordinates of the empty cell above the soil")
    p = (x, y, z)
    with ExitStack() as cleanup:
        gateway = _gateway()
        cleanup.callback(gateway.close)
        entry = gateway.entry_point
        if not entry.inGame():
            raise RuntimeError("Minecraft world is not open")
        if entry.hasActiveTask():
            return {"planted": False, "status": "waiting_for_game_task"}
        before = _java_to_python(entry.getBlockAt(*p))
        if before.get("id") == "minecraft:" + sapling:
            return {"planted": True, "already_present": True, "position": list(p)}
        if before.get("id") != "minecraft:air" or not _can_plant(gateway, sapling, p):
            return {
                "planted": False,
                "status": "invalid_soil_or_occupied",
                "target": before,
            }
        if not _java_to_python(entry.canPlaceBlock(*p)).get(
            "canPlace"
        ) or not _clearance(gateway, p, sapling):
            return {"planted": False, "status": "protected_or_obstructed"}
        inventory = _java_to_python(entry.getInventoryFull())
        if not inventory.get("ok"):
            raise RuntimeError("Inventory unavailable")
        if not any(
            s.get("item") == "minecraft:" + sapling
            and s.get("count", 0) > 0
            and 0 <= s.get("index", -1) < 36
            for s in inventory.get("slots", [])
        ):
            return {"planted": False, "status": "missing_sapling"}
        if not entry.closeOpenScreen() or not entry.lookAt(x + 0.5, y - 0.01, z + 0.5):
            return {"planted": False, "status": "aim_failed"}
        support = (x, y - 1, z)
        deadline = time.monotonic() + 1.5
        while True:
            looking = (
                _java_to_python(entry.getGameState())
                .get("self", {})
                .get("lookingAt", "")
            )
            if str(looking).partition("@")[2].replace(" ", "") == ",".join(
                map(str, support)
            ):
                break
            if time.monotonic() >= deadline:
                return {
                    "planted": False,
                    "status": "needs_move",
                    "position": list(p),
                    "looking_at": looking,
                }
            time.sleep(0.05)
        select_inventory_item(entry, cleanup, inventory, "minecraft:" + sapling)
        interaction = _java_to_python(entry.placeBlockLooking())
        deadline = time.monotonic() + 2
        while True:
            time.sleep(0.1)
            after = _java_to_python(entry.getBlockAt(*p))
            if (
                after.get("id") == "minecraft:" + sapling
                or time.monotonic() >= deadline
            ):
                break
        planted = after.get("id") == "minecraft:" + sapling
        return {
            "planted": planted,
            "status": "planted" if planted else "placement_not_confirmed",
            "position": list(p),
            "target_after": after,
            "interaction": interaction,
            "hint": "One placement attempt only; inspect the world before any retry.",
        }


def grow_tree_fast(
    sapling="oak_sapling",
    max_bone_meal=MAX_GROWTH_ATTEMPTS,
    candidate_limit=128,
    search_steps=4,
    search_state=None,
    failed_goals=(),
):
    sapling = _sapling_name(sapling)
    if type(max_bone_meal) is not int or not 1 <= max_bone_meal <= 16:
        raise ValueError("Bone meal budget must be from 1 to 16")
    if _game_busy():
        return {
            "status": "waiting_for_game_task",
            "hint": "Let the current collection or movement finish; do not overwrite it.",
        }
    inventory = read_player_inventory()
    counts = {}
    for item in inventory["items"]:
        counts[item["item"]] = counts.get(item["item"], 0) + item["count"]
    scan = find_tree_spots(
        sapling,
        candidate_limit=candidate_limit,
        search_steps=search_steps,
        search_state=search_state,
        failed_goals=failed_goals,
        allow_exploration=bool(counts.get("minecraft:bone_meal")),
    )
    target = next(
        (
            p
            for p in scan["existing_saplings"]
            if math.dist(tuple(float(n) for n in inventory["position"].split(",")), p)
            <= 3.5
        ),
        None,
    )
    missing = []
    if (
        target is None
        and not scan["existing_saplings"]
        and not counts.get("minecraft:" + sapling)
    ):
        missing.append({"item": sapling, "count": 1})
    if not counts.get("minecraft:bone_meal"):
        missing.append({"item": "bone_meal", "count": max_bone_meal})
    if missing:
        return {
            "status": "needs_materials",
            "missing": missing,
            "scan": scan,
            "next_action": {"tool": "obtain_item", "arguments": missing[0]},
            "hint": "Obtain only missing items. Queue acceptance is not completion; wait for the native collection task, then call grow_tree again.",
        }
    if target is None:
        spot = next(
            (
                s
                for s in scan["spots"]
                if s["within_reach"] and counts.get("minecraft:" + sapling)
            ),
            None,
        )
        if spot is None:
            exploration = scan.get("exploration") or {}
            next_action = next(
                (
                    {"tool": "agent_action", "arguments": {"action": a["command"]}}
                    for a in scan.get("approaches", [])
                    if a["walk_steps"] > 0
                    and (
                        counts.get("minecraft:" + sapling)
                        or a["plant_position"] in scan["existing_saplings"]
                    )
                ),
                exploration.get("next_action"),
            )
            return {
                "status": "needs_move" if next_action else "no_site",
                "scan": scan,
                "next_action": next_action,
                "hint": "Use next_action once and rescan/grow_tree after arrival. No local site is not a reason to give up while exploration offers a new observed area. With no next_action, report the precise search/route limit and rejected counts, not global absence of soil.",
            }
        target = spot["position"]
        planted = plant_sapling_at(*target, sapling)
        if not planted.get("planted"):
            return {"status": planted["status"], "planting": planted}
    doses = []
    for _ in range(
        min(MAX_GROWTH_ATTEMPTS, max_bone_meal, counts.get("minecraft:bone_meal", 0))
    ):
        if sum(d.get("consumed", 0) for d in doses) >= max_bone_meal:
            break
        dose = apply_bone_meal_at(*target)
        doses.append(dose)
        if dose.get("tree_grown") is True:
            return {
                "status": "grown",
                "position": target,
                "doses": len(doses),
                "consumed": sum(d.get("consumed", 0) for d in doses),
                "target_after": dose["target_after"],
            }
        if not dose.get("applied") and not dose.get("retry_safe"):
            return {
                "status": "waiting_for_game_task"
                if dose.get("reason") == "active_game_task"
                else "interaction_not_confirmed",
                "position": target,
                "last_dose": dose,
                "consumed": sum(d.get("consumed", 0) for d in doses),
            }
    return {
        "status": "attempts_exhausted"
        if len(doses) >= MAX_GROWTH_ATTEMPTS
        else "bone_meal_budget_exhausted",
        "position": target,
        "consumed": sum(d.get("consumed", 0) for d in doses),
        "tree_grown": False,
        "hint": "A planted sapling remains. Report the actual result; inspect space and materials before requesting another bounded budget.",
    }
