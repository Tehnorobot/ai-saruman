"""The host-side UnionClef connection; no second game decision loop."""

import os
import re
import time

from py4j.java_gateway import CallbackServerParameters, GatewayParameters, JavaGateway
from py4j.java_collections import JavaArray, JavaList, JavaMap


# Core commands verified against UnionClef 0.95.61. The live registry extends this set.
CORE_COMMANDS = frozenset({"goto", "follow", "stop", "cancel", "get", "give", "inventory",
                           "status", "help", "wander", "forget", "t", "game", "pursue",
                           "avoid", "gesture", "connect", "check_player", "check_block",
                           "pearl", "shoot", "build", "grave", "sign", "place", "mineblock"})
CORE_COMMANDS = CORE_COMMANDS | {"food", "equip", "set"}

COMMAND_GUIDE = (
    "@goto X Y Z: travel; @follow Nick: follow; @stop: stop tasks; "
    "Planting supplies and crop ingredients come only from mature harvests via farm_crops; native @get seeds/wheat/carrot/potato is disabled. "
    "obtain_item('bread', 1): craft bread only from already harvested wheat in inventory; "
    "farm_crops(): plant crops or harvest ripe plants for food; @inventory: inspect inventory. "
    "There is no @eat command: use eat_food('bread') for food already in inventory; "
    "UnionClef can also auto-eat when its hunger thresholds are met; "
    "at food level 20/20 normal food cannot be eaten. Check get_inventory() for hunger and items. "
    "Bone meal is NOT a placeable block: never use @place bone_meal. "
    "Use apply_bone_meal(x,y,z) on an observed planted sapling within reach; "
    "it reports dose consumption separately from tree growth, which is not guaranteed after one dose. "
    "A missing command spelling does not mean the whole activity is unsupported."
)


def validate_action_command(action: str, commands=None) -> str:
    """Reject prose and command chains before they reach the mod's interpreter."""
    if not isinstance(action, str) or not action.strip():
        raise ValueError("Expected one UnionClef command, for example @goto 10 64 20 or @stop")
    if len(action) > 200 or any(char in action for char in "\n\r;"):
        raise ValueError("Send one command per call, without newlines or semicolons")
    command = action.strip().removeprefix("@")
    parts = command.split()
    name = parts[0] if parts else ""
    if name not in (commands or CORE_COMMANDS):
        raise ValueError("Unknown UnionClef command. " + COMMAND_GUIDE + " "
                         "Use get_game_status() to check tasks and get_nearby_blocks() to inspect terrain; "
                         "agent_action does not accept natural-language instructions")
    # Only the native commands are strict here; custom tasks have their own argument parsers.
    if name == "goto":
        coordinates = parts[1:]
        if coordinates and coordinates[-1] in {"overworld", "nether", "end"}:
            coordinates = coordinates[:-1]
        if (not parts[1:] or len(coordinates) > 3
                or any(not re.fullmatch(r"[+-]?\d+", value) for value in coordinates)):
            raise ValueError("goto requires block coordinates, e.g. @goto 10 64 20")
    if name in {"stop", "cancel"} and len(parts) != 1:
        raise ValueError("Use @stop without arguments")
    if name == "place" and len(parts) > 1 and parts[1].removeprefix("minecraft:") == "bone_meal":
        raise ValueError("Bone meal is used on a plant, not placed as a block. Use apply_bone_meal(x,y,z) on a planted sapling.")
    if name == "follow" and (len(parts) > 2 or (len(parts) == 2 and not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", parts[1]))):
        raise ValueError("Use @follow PlayerNickname")
    from saruman.peaceful import guard_command
    guard_command(command)
    return "@" + " ".join(parts)


def read_nearby_blocks(radius: int) -> list[dict[str, str]]:
    if not 1 <= radius <= 5:
        raise ValueError("radius must be between 1 and 5")
    gateway = JavaGateway(gateway_parameters=GatewayParameters(
        address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
        read_timeout=5,
    ))
    try:
        if not gateway.entry_point.inGame():
            raise RuntimeError("Minecraft world is not open")
        return [dict(block.items()) for block in gateway.entry_point.getBlocksAround(radius)]
    finally:
        gateway.close()


def _java_to_python(value):
    if isinstance(value, (JavaMap, dict)):
        return {str(key): _java_to_python(item) for key, item in value.items()}
    if isinstance(value, (JavaList, JavaArray, list, tuple)):
        return [_java_to_python(item) for item in value]
    return value


def read_player_inventory() -> dict:
    gateway = JavaGateway(gateway_parameters=GatewayParameters(
        address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")), read_timeout=5,
    ))
    try:
        inventory = _java_to_python(gateway.entry_point.getInventoryFull())
        if not inventory.get("ok"):
            raise RuntimeError(inventory.get("error", "Inventory is not available"))
        state = _java_to_python(gateway.entry_point.getGameState()).get("self", {})
        return {
            "health": state.get("hp"), "food": state.get("food"),
            "saturation": state.get("saturation"), "position": state.get("pos"),
            "held_item": inventory.get("heldItem"),
            "items": [slot for slot in inventory.get("slots", []) if slot.get("count", 0) > 0],
        }
    finally:
        gateway.close()


def consume_inventory_food(item: str) -> dict:
    item = item.removeprefix("minecraft:")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", item):
        raise ValueError("Expected an item identifier such as bread")
    gateway = JavaGateway(gateway_parameters=GatewayParameters(
        address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
        read_timeout=6, auto_convert=True,
    ))
    entry = gateway.entry_point
    pressed = False
    swapped = False
    selected = None
    source = None
    try:
        matches = gateway.jvm.adris.altoclef.TaskCatalogue.getItemMatches(item)
        if not matches or gateway.jvm.adris.altoclef.multiversion.item.ItemVer.getFoodComponent(matches[0]) is None:
            raise ValueError("This item is not recognised as food")
        before = _java_to_python(entry.getInventoryFull())
        if not before.get("ok"):
            raise RuntimeError(before.get("error", "Inventory unavailable"))
        food_before = _java_to_python(entry.getGameState()).get("self", {}).get("food")
        if food_before is None:
            raise RuntimeError("Hunger state unavailable")
        if food_before >= 20:
            return {"consumed": False, "reason": "hunger_full", "food": food_before}
        identifier = "minecraft:" + item
        slots = [slot for slot in before.get("slots", [])
                 if slot.get("item") == identifier and slot.get("count", 0) > 0 and 0 <= slot["index"] < 36]
        if not slots:
            raise ValueError(f"No {item} in inventory; use obtain_item('{item}', 1) first")
        source = slots[0]["index"]
        selected = before["selectedHotbar"]
        target = source if source < 9 else selected
        if not entry.closeOpenScreen():
            raise RuntimeError("Could not close the inventory/menu before eating")
        if source >= 9:
            if not entry.clickUiSlot(source, target, "SWAP"):
                raise RuntimeError("Could not move food to the hotbar")
            swapped = True
        if not entry.selectHotbar(target):
            raise RuntimeError("Could not select the food slot")
        total_before = sum(slot.get("count", 0) for slot in before.get("slots", [])
                           if slot.get("item") == identifier)
        pressed = True
        entry.executeAgentActions({"use": 1})
        use_result = _java_to_python(entry.useHeldItem())
        if not use_result.get("ok"):
            raise RuntimeError(use_result.get("reason", "Could not use held food"))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            current = _java_to_python(entry.getInventoryFull())
            if not current.get("ok"):
                raise RuntimeError("Inventory could not be read while eating")
            total = sum(slot.get("count", 0) for slot in current.get("slots", [])
                        if slot.get("item") == identifier)
            if total < total_before:
                food_after = _java_to_python(entry.getGameState()).get("self", {}).get("food")
                return {"consumed": True, "item": identifier, "count_before": total_before,
                        "count_after": total, "food_before": food_before, "food_after": food_after}
            time.sleep(0.1)
        return {"consumed": False, "reason": "consumption_not_confirmed", "food_before": food_before}
    finally:
        try:
            if pressed:
                entry.executeAgentActions({"use": 0})
            if swapped:
                entry.clickUiSlot(source, selected, "SWAP")
            if selected is not None:
                entry.selectHotbar(selected)
        finally:
            gateway.close()


def normalize_chat_message(message: dict) -> dict:
    """Recognize vanilla '<nick> text' when UnionClef supplies no parsed author."""
    result = dict(message)
    if not result.get("user"):
        match = re.fullmatch(r"<([A-Za-z0-9_-]{1,16})>\s?(.*)", result.get("msg") or "")
        if match:
            result.update(user=match[1], msg=match[2])
    return result


def connect(callback):
    return JavaGateway(
        gateway_parameters=GatewayParameters(
            address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
            port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
            read_timeout=float(os.getenv("MINECRAFT_GATEWAY_TIMEOUT", "5")),
            auto_convert=True,
        ),
        callback_server_parameters=CallbackServerParameters(
            address=os.getenv("MINECRAFT_CALLBACK_HOST", "127.0.0.1"),
            port=int(os.getenv("MINECRAFT_CALLBACK_PORT", "25334")),
            daemonize=True,
            daemonize_connections=True,
        ),
        python_server_entry_point=callback,
        start_callback_server=True,
    )
