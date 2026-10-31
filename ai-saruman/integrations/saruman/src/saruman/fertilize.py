"""Apply one dose through UnionClef's existing client-thread block interaction."""

import os
import time
from contextlib import ExitStack

from py4j.java_gateway import GatewayParameters, JavaGateway

from saruman.minecraft import _java_to_python

SAPLINGS = {
    "minecraft:" + kind + "_sapling"
    for kind in (
        "oak",
        "spruce",
        "birch",
        "jungle",
        "acacia",
        "dark_oak",
        "cherry",
        "pale_oak",
    )
} | {"minecraft:mangrove_propagule"}
BONE_MEAL = "minecraft:bone_meal"


def _count(inventory):
    if not inventory.get("ok"):
        raise RuntimeError("Could not read inventory to verify bone meal consumption")
    return sum(
        slot.get("count", 0)
        for slot in inventory.get("slots", [])
        if slot.get("item") == BONE_MEAL
    )


def _position_matches(value, position):
    return str(value).replace(" ", "") == ",".join(str(n) for n in position)


def select_inventory_item(entry, cleanup, inventory, item, *, index=None):
    """Select a known item; register restoration before the interaction can fail."""
    slots = [
        slot
        for slot in inventory.get("slots", [])
        if slot.get("item") == item
        and slot.get("count", 0) > 0
        and 0 <= slot.get("index", -1) < 36
        and (index is None or slot["index"] == index)
    ]
    if not slots:
        raise ValueError(f"No accessible {item} in inventory")
    selected = inventory.get("selectedHotbar")
    if type(selected) is not int or not 0 <= selected <= 8:
        raise RuntimeError("Selected hotbar slot is unavailable")
    source = slots[0]["index"]
    hotbar = source if source < 9 else selected
    cleanup.callback(entry.selectHotbar, selected)
    if source >= 9:
        if not entry.clickUiSlot(source, hotbar, "SWAP"):
            raise RuntimeError(f"Could not move {item} to the hotbar")
        cleanup.callback(entry.clickUiSlot, source, hotbar, "SWAP")
    if not entry.selectHotbar(hotbar):
        raise RuntimeError(f"Could not select {item}")
    equipped = _java_to_python(entry.getInventoryFull())
    if not equipped.get("ok") or equipped.get("selectedHotbar") != hotbar:
        raise RuntimeError("Selected item slot was not confirmed")
    if not any(
        slot.get("index") == hotbar
        and slot.get("item") == item
        and slot.get("count", 0) > 0
        for slot in equipped.get("slots", [])
    ):
        raise RuntimeError("Item selection was not confirmed")


def apply_bone_meal_at(x: int, y: int, z: int) -> dict:
    if any(type(value) is not int for value in (x, y, z)):
        raise ValueError("Use integer coordinates of a planted sapling")
    position = (x, y, z)
    with ExitStack() as cleanup:
        gateway = JavaGateway(
            gateway_parameters=GatewayParameters(
                address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
                port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
                read_timeout=5,
                auto_convert=True,
            )
        )
        cleanup.callback(gateway.close)
        entry = gateway.entry_point
        if not entry.inGame():
            raise RuntimeError("Minecraft world is not open")
        if entry.hasActiveTask():
            return {
                "applied": False,
                "reason": "active_game_task",
                "hint": "Wait for completion, or stop that task before interacting.",
            }
        block = _java_to_python(entry.getBlockAt(*position))
        if block.get("id") not in SAPLINGS:
            return {"applied": False, "reason": "target_not_sapling", "target": block}
        inventory = _java_to_python(entry.getInventoryFull())
        count_before = _count(inventory)
        slots = [
            slot
            for slot in inventory.get("slots", [])
            if slot.get("item") == BONE_MEAL
            and slot.get("count", 0) > 0
            and 0 <= slot.get("index", -1) < 36
        ]
        if not slots:
            return {
                "applied": False,
                "reason": "no_bone_meal",
                "hint": "Obtain bone_meal first; it is used on a block, not placed as a block.",
            }
        selected = inventory.get("selectedHotbar")
        if type(selected) is not int or not 0 <= selected <= 8:
            raise RuntimeError("Selected hotbar slot is unavailable")
        if not entry.closeOpenScreen():
            raise RuntimeError("Could not close the inventory/menu")
        # Aim at the plant itself, then require a real crosshair hit. No blind clicking
        # on nearby grass (which would consume bone meal on a different target).
        if not entry.lookAt(x + 0.5, y + 0.35, z + 0.5):
            raise RuntimeError("Could not aim at the sapling")
        deadline = time.monotonic() + 1.5
        while True:
            looking_at = (
                _java_to_python(entry.getGameState())
                .get("self", {})
                .get("lookingAt", "")
            )
            if _position_matches(str(looking_at).partition("@")[2], position):
                break
            if time.monotonic() >= deadline:
                return {
                    "applied": False,
                    "reason": "target_not_in_crosshair",
                    "looking_at": looking_at,
                    "hint": "Move within reach with clear line of sight; inspect terrain before retrying.",
                }
            time.sleep(0.05)
        select_inventory_item(entry, cleanup, inventory, BONE_MEAL)
        # Despite its name, placeBlockLooking calls interactBlock for ANY held item.
        # useHeldItem calls interactItem (air) and cannot fertilize a plant.
        result = _java_to_python(entry.placeBlockLooking())
        if not result.get("ok"):
            return {
                "applied": False,
                "reason": "application_not_confirmed",
                "interaction": result,
                "hint": "Do not automatically repeat: a timed-out interaction might still execute. Inspect inventory and the target first.",
            }
        target_matches = _position_matches(result.get("target"), position)
        deadline = time.monotonic() + 2
        count_after = count_before
        after = block
        while True:
            time.sleep(0.1)
            count_after = _count(_java_to_python(entry.getInventoryFull()))
            after = _java_to_python(entry.getBlockAt(*position))
            if (
                count_after < count_before
                or after.get("id") != block["id"]
                or time.monotonic() >= deadline
            ):
                break
        after_id = after.get("id", "")
        tree_grown = (
            (after_id.endswith("_log") or after_id == "minecraft:mangrove_roots")
            if after_id not in SAPLINGS
            else False
        )
        if after_id not in SAPLINGS and not tree_grown:
            tree_grown = None  # Disappearing/changing into another block alone is not proof of a tree.
        applied = target_matches and (count_after < count_before or tree_grown is True)
        retry_safe = (
            target_matches and after_id in SAPLINGS and result.get("ok") is True
        )
        return {
            "applied": applied,
            "retry_safe": retry_safe,
            "reason": "confirmed"
            if applied
            else (
                "target_changed" if not target_matches else "application_not_confirmed"
            ),
            "position": list(position),
            "count_before": count_before,
            "count_after": count_after,
            "consumed": max(0, count_before - count_after),
            "tree_grown": tree_grown,
            "target_after": after,
            "interaction": result,
            "hint": "One processed click only. Consumption does not guarantee growth. A planner may retry a matching unchanged sapling within its persistent five-attempt cap; never replay a timed-out or mismatched interaction.",
        }
