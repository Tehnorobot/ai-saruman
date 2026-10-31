"""One observed crop operation per call: sow, harvest ripe plants, or prepare soil."""

import math
import time
from contextlib import ExitStack

from saruman.fertilize import select_inventory_item
from saruman.minecraft import _java_to_python
from saruman.trees import _gateway, _position, _walk_paths
from saruman.world_actions import interaction_manager, on_client
from saruman.world_rules import NativeWorldRules

CROPS = {
    "wheat": "wheat_seeds",
    "carrots": "carrot",
    "potatoes": "potato",
    "beetroots": "beetroot_seeds",
}
FOODS = ("bread", "baked_potato", "carrot", "beetroot", "potato")
HOES = {
    f"minecraft:{m}_hoe"
    for m in ("wooden", "stone", "iron", "golden", "diamond", "netherite")
}


def _state(rules, p):
    state = rules.world.method_8320(rules.position(p))
    block = state.method_26204()
    identifier = str(rules.jvm.net.minecraft.class_7923.field_41175.method_10221(block))
    return identifier.removeprefix("minecraft:"), state, block


def _mature(rules, p):
    kind, state, block = _state(rules, p)
    return kind in CROPS and bool(block.method_9825(state))


def _counts(inventory):
    result = {}
    for slot in inventory.get("slots", []):
        result[slot.get("item")] = result.get(slot.get("item"), 0) + slot.get(
            "count", 0
        )
    return result


def _approach(paths, center, target, pickup=False):
    radius = 1.4 if pickup else 2.7
    choices = [p for p in paths if p != target and math.dist(p, target) <= radius]
    if not choices:
        return None
    p = min(choices, key=lambda p: (math.dist(p, target), paths[p]))
    if p == center:
        return None
    return {
        "tool": "agent_action",
        "arguments": {"action": "@goto " + " ".join(map(str, p))},
    }


def _click_soil(entry, cleanup, inventory, item, p):
    if not entry.closeOpenScreen() or not entry.lookAt(
        p[0] + 0.5, p[1] - 0.01, p[2] + 0.5
    ):
        return {"ok": False, "reason": "aim_failed"}
    expected = (p[0], p[1] - 1, p[2])
    deadline = time.monotonic() + 1.2
    while True:
        looking = (
            _java_to_python(entry.getGameState()).get("self", {}).get("lookingAt", "")
        )
        if str(looking).partition("@")[2].replace(" ", "") == ",".join(
            map(str, expected)
        ):
            break
        if time.monotonic() >= deadline:
            return {"ok": False, "reason": "soil_not_in_crosshair"}
        time.sleep(0.05)
    select_inventory_item(entry, cleanup, inventory, "minecraft:" + item)
    return _java_to_python(entry.placeBlockLooking())


def farm_step(mode="auto"):
    """No growth waiting, fertilizer, water placement, or destruction of unripe crops."""
    if mode not in ("auto", "plant", "harvest"):
        raise ValueError("mode must be auto, plant or harvest")
    with ExitStack() as cleanup:
        gateway = _gateway()
        cleanup.callback(gateway.close)
        entry = gateway.entry_point
        if not entry.inGame():
            return {"status": "disconnected"}
        if entry.hasActiveTask():
            return {"status": "waiting_for_game_task"}
        game = _java_to_python(entry.getGameState()).get("self", {})
        center = _position(game["pos"])
        inventory = _java_to_python(entry.getInventoryFull())
        if not inventory.get("ok"):
            return {"status": "inventory_unavailable"}
        counts = _counts(inventory)
        observed = {
            _position(b["position"]): b["block"]
            for b in _java_to_python(entry.getBlocksAround(5))
        }
        rules = NativeWorldRules(gateway, observed, center, 5)
        paths = _walk_paths(observed, center, rules)
        rejected = {"unreachable": 0, "protected": 0, "planting_rule": 0}
        ripe, young, farmland, ground, water = [], [], [], [], []
        for p in observed:
            kind, _, _ = _state(rules, p)
            above = (p[0], p[1] + 1, p[2])
            if kind in CROPS:
                (ripe if _mature(rules, p) else young).append(p)
            elif kind == "farmland" and above not in observed:
                farmland.append(above)
            elif kind in ("dirt", "grass_block") and above not in observed:
                ground.append(above)
            elif kind == "water":
                water.append(p)
        seeds = [
            (kind, seed)
            for kind, seed in CROPS.items()
            if counts.get("minecraft:" + seed, 0)
        ]
        # Seed replenishment uses the same maturity-checked harvest as food.
        # Native @get seeds may break young crops, including our own recent sowing.
        targets = [(p, "harvest") for p in ripe] if mode != "plant" or not seeds else []
        if mode != "harvest" and seeds:
            targets += [(p, "plant") for p in farmland]
            hoe = next(
                (item for item in counts if item in HOES and counts[item] > 0), None
            )
            if hoe:
                targets += [
                    (p, "till")
                    for p in ground
                    if any(
                        abs(p[0] - w[0]) <= 4
                        and abs(p[2] - w[2]) <= 4
                        and w[1] in (p[1], p[1] - 1)
                        for w in water
                    )
                ]
        targets.sort(
            key=lambda pair: (pair[1] != "harvest", math.dist(center, pair[0]))
        )
        for p, action in targets:
            if math.dist(center, p) > 3.4:
                approach = _approach(paths, center, p)
                if approach:
                    return {
                        "status": "needs_move",
                        "activity": action,
                        "position": list(p),
                        "next_action": approach,
                    }
                rejected["unreachable"] += 1
                continue
            if action == "harvest":
                if not _mature(rules, p) or not entry.canBreakBlock(*p):
                    rejected["protected"] += 1
                    continue
                if not entry.closeOpenScreen() or not entry.lookAt(
                    p[0] + 0.5, p[1] + 0.25, p[2] + 0.5
                ):
                    continue
                deadline = time.monotonic() + 1
                while True:
                    looking = (
                        _java_to_python(entry.getGameState())
                        .get("self", {})
                        .get("lookingAt", "")
                    )
                    if str(looking).partition("@")[2].replace(" ", "") == ",".join(
                        map(str, p)
                    ):
                        break
                    if time.monotonic() >= deadline:
                        return {
                            "status": "no_work",
                            "reason": "Mature crop is not visible in the crosshair; choose another observed approach.",
                        }
                    time.sleep(0.05)
                if not _mature(rules, p):
                    continue
                accepted = on_client(
                    gateway,
                    interaction_manager(gateway),
                    "net.minecraft.class_636",
                    "method_2910",
                    ("net.minecraft.class_2338", "net.minecraft.class_2350"),
                    (
                        rules.position(p),
                        gateway.jvm.net.minecraft.class_2350.field_11036,
                    ),
                )
                time.sleep(0.2)
                after, _, _ = _state(rules, p)
                after_counts = _counts(_java_to_python(entry.getInventoryFull()))
                collected = {
                    k: v - counts.get(k, 0)
                    for k, v in after_counts.items()
                    if v > counts.get(k, 0)
                }
                harvested = bool(accepted) and after == "air"
                return {
                    "status": "harvested" if harvested else "interaction_unconfirmed",
                    "position": list(p),
                    "collected": collected,
                    "next_action": _approach(paths, center, p, pickup=True)
                    if harvested
                    else None,
                }
            if not _java_to_python(entry.canPlaceBlock(*p)).get("canPlace"):
                rejected["protected"] += 1
                continue
            if action == "till":
                interaction = _click_soil(
                    entry, cleanup, inventory, hoe.removeprefix("minecraft:"), p
                )
                time.sleep(0.15)
                kind, _, _ = _state(rules, (p[0], p[1] - 1, p[2]))
                return {
                    "status": "tilled"
                    if interaction.get("ok") and kind == "farmland"
                    else "interaction_unconfirmed",
                    "position": list(p),
                }
            for kind, seed in seeds:
                if not rules.can_plant(seed, p):
                    rejected["planting_rule"] += 1
                    continue
                interaction = _click_soil(entry, cleanup, inventory, seed, p)
                time.sleep(0.2)
                after, _, _ = _state(rules, p)
                return {
                    "status": "planted"
                    if interaction.get("ok") and after == kind
                    else "interaction_unconfirmed",
                    "crop": kind,
                    "position": list(p),
                }
        wet_ground = [
            p
            for p in ground
            if any(
                abs(p[0] - w[0]) <= 4
                and abs(p[2] - w[2]) <= 4
                and w[1] in (p[1], p[1] - 1)
                for w in water
            )
        ]
        if (
            mode != "harvest"
            and wet_ground
            and seeds
            and not any(item in HOES and count > 0 for item, count in counts.items())
        ):
            return {
                "status": "needs_materials",
                "reason_code": "missing_hoe",
                "next_action": {
                    "tool": "obtain_item",
                    "arguments": {"item": "wooden_hoe", "count": 1},
                },
            }
        if young and not ripe and (not seeds or not (farmland or wet_ground)):
            return {
                "status": "waiting_for_growth",
                "reason_code": "young_crops",
                "young_crops": len(young),
                "reason": "Leave young crops intact and do another activity; replenish planting supplies only from mature harvests.",
            }
        if mode != "harvest" and not seeds:
            return {
                "status": "no_work",
                "reason_code": "no_seeds_or_mature_crops",
                "reason": "No planting supplies or reachable mature harvest. Find mature crops elsewhere; never use native @get seeds or break young crops.",
            }
        return {
            "status": "no_work",
            "reason_code": (
                "unreachable"
                if rejected["unreachable"]
                else "protected"
                if rejected["protected"]
                else "planting_rule"
                if rejected["planting_rule"]
                else "no_water"
                if ground and not wet_ground and not farmland
                else "no_crop_site"
            ),
            "scan": {
                "radius": 5,
                "mature": len(ripe),
                "young": len(young),
                "empty_plots": len(farmland),
                "wet_ground": len(wet_ground),
                "reachable_cells": len(paths),
                "rejected": rejected,
            },
            "reason": "No reachable mature crop or plantable empty plot with seeds/tools; leave young plants to grow naturally.",
        }
