# -*- coding: utf-8 -*-
"""
Minecraft tools for Baritone-like agent and Minecraft chat commands.

TODO:
- add chat status / inner command cooldowns / last calls statistics
- implement checking tools like @check block / @check player
"""

import os
import re
import traceback
from copy import deepcopy
from typing import Dict, Optional, TypedDict

from agent.tools import tools_config
from agent.tools.control_tools import (
    _add_tool_error_log,
    _add_tool_event_waiter,
    _add_tool_result_log,
    _add_tool_run_log,
)
from data_schema.chat_structures import ToolEventWaiter
from data_schema.structure_templates import REPO_RESOURCE_PATH
from utils.minecraft_helper import PlayerStatus, get_nearby_players
from utils.yaml_utils import YamlReader
from saruman.minecraft import CORE_COMMANDS, consume_inventory_food, read_nearby_blocks, read_player_inventory, validate_action_command
from saruman.fertilize import apply_bone_meal_at
from saruman.procedures import read_procedure
from saruman.trees import find_tree_spots, grow_tree_fast, plant_sapling_at, tree_approach
from saruman.navigation import check_recent_failure, goto_target
from saruman import tree_tasks
from saruman.tree_memory import resume_paused
from saruman.farming import farm_step
from saruman.hunting import attack_hostile_at, hunt_step
from saruman.autonomy import exploration_step
from saruman.peaceful import guard_command, guard_crop_inputs

queue_send = tools_config.queue_send

TASK_COMMANDS_MAP = {
    "askpeace": "gesture",
    "walkthrough": "game walkthrough",
    "speedrun": "game walkthrough",
    "gamer": "game walkthrough",
    "skywars": "game sw",
    "sky_wars": "game sw",
    "wars": "game sw",
    "sky": "game sw",
    "sw": "game sw",
    "murdermystery": "game mm",
    "mm": "game mm",
    "murder": "game mm",
    "murder_mystery": "game mm",
    "mystery": "game mm",
    "shiftnearest": "t shift",
    "shift": "t shift",
    "breed": "t shift",
    "duck": "t shift",
    "adult": "t shift",
    "fuck": "t shift",
    "love": "t shift",
    "kiss": "t shift",
    "pokebutt": "t shift",
    "yandere": "t combat",
    "find": "t follow",
    "grave": "t grave",
    "build": "t build",
    "sign": "t sign",
    "hunt": "pursue",
    "kill": "pursue",
    "chase": "t punk",
    "attack": "pursue",
    "combat": "pursue",
    "beat": "pursue",
    "punk": "pursue",
    "damage": "pursue",
    "remove": "pursue",
    "strike": "pursue",
    "fight": "pursue",
    "reconnect": "stop",  # TODO ADD reconnect / rejoin =(
    "tp": "t pearl",
    "pearl": "t pearl",
    "enderpearl": "t pearl",
    "shoot": "t shoot",
    "bow": "t shoot",
    "build_grave": "t grave",
    "place_sign": "t sign",
    "smash": "t test mace",
}

SERVER_COMMANDS_MAP = {
    "rejoin": "hub",
    "exit": "hub",
    "leave": "hub",
    "stop": "hub",
    "request help": "mm m spawn AgressiveTeacher",
}

# not implemented yet
WHITELIST_COMMANDS = []
WHITELIST_ACTIONS = []
BLACKLIST_COMMANDS = [
    # "spawn",
    "ignore",
    "unignore",
    "stop",
    "reload",
    "restart",
    "gamerule",
    # "setblock",
]
BLACKLIST_ACTIONS = []  # ["stop", "game"]

# removed from commands for now
"""
        `/spawn` - teleport on spawn zone
        `/lobby` - exit current minigame / go to lobby (works only on minigame servers) 
"""

MC_COMMAND_PREFIX = "/"


MC_ROFL_COMMANDS = ["mute", "ban", "emute", "ignore", "voteban", "kick", "votemute"]


def _add_chat_event_waiter(call_id: int) -> bool:
    """Add default chat event waiter"""
    if call_id:
        return _add_tool_event_waiter(
            call_id,
            ToolEventWaiter(
                timeout=0.4,
                match_events=[{"type": "chat"}],
                # return_format=[{"field": "msg", "before": "", "after": "", "add_empty": False}]
            ),
        )  # match_events=[{"type": "chat"}]))
    return False


def _add_agent_event_waiter(call_id: int) -> bool:
    """Add default agent event waiter"""
    if call_id:
        return _add_tool_event_waiter(
            call_id,
            ToolEventWaiter(
                timeout=5,
                match_events=[
                    {"env": "minecraft", "type": "mc_executor_log", "isEvent": True}
                ],
                return_format=[
                    {
                        "field": "description",
                        "before": "",
                        "after": "",
                        "add_empty": False,
                    }
                ],
            ),
        )
    return False


def get_nearby_players_info() -> str:
    """
    Get list of nearby players.

    Returns:
        str info of players nearby
    """

    return ""


def get_player_status(player: str) -> PlayerStatus:
    """
    Get player status by name.
    Args:
        player: String, name of player.

    Returns:
        PlayerStatus: Dictionary with player status fields.
    """
    # FAR TODO add pipeline getting precise from minebridge minebridge_query_queue.put("get_player_status", player)

    near = get_nearby_players(tools_config.ctx_swarm)

    for p in near.values():
        if p["name"] == player:
            return p
    return {}


def _is_rofl_command(command: str) -> bool:
    return False  # disabled this...
    for rofl_cmd in MC_ROFL_COMMANDS:
        if command.startswith(rofl_cmd):
            return True
    return False


# TODO EXTRA VARIOUS!!!
# move command examples logic from docstring to something other


def minecraft_command(command: str) -> bool:
    """Run a server command in minecraft (like typing /command in chat).

    Args:
        command: Server command without / prefix, e.g. "tpa Chochok" or "lobby".
    """
    guard_command(command)
    from saruman.mod_goals import using_mods, submit_command
    call_id = _add_tool_run_log("minecraft_command", {"command": command})
    command_prefix = MC_COMMAND_PREFIX
    try:
        command = command.strip()
        if command and len(command) > 0:
            if command[0] == "/":
                command = command[1:]
            if command[0] == "@":  # mda...
                return agent_action(command[1:])
        if command:
            if command in list(SERVER_COMMANDS_MAP.keys()):
                command = SERVER_COMMANDS_MAP[command]

            if len(command) > 5:
                if _is_rofl_command(command):
                    # DISABLED CMD, with just chat !cmd like a rofl
                    command_prefix = "!"
                    # tools_config.ctx_swarm["fx_queue"].put("banword")
                elif command.startswith("ban"):
                    try:
                        # tools_config.ctx_swarm["fx_queue"].put("banword")
                        command = command.replace("ban", "tempban")
                        command = command.split(" ")
                        nick = command[1]
                        reason = " ".join(command[2:])

                        command = "tempban " + nick + " 20s " + reason
                        print("[EXECUTING BAN COMMAND]", command)
                    except Exception as e:
                        print(f"Error while parsing ban command: {e}")
                        traceback.print_exc()

            final_split = command.split(" ")
            if len(final_split) > 0:
                command_name = final_split[0].lower()
                if command_name in BLACKLIST_COMMANDS:
                    print(
                        "[MC CMD TOOL] TRIED TO RUN BLACKLISTED "
                        + command_name
                        + " COMMAND!"
                    )
                    _add_tool_result_log(
                        "minecraft_command",
                        {"returned": "Command blacklisted", "call_id": call_id},
                    )
                    return False

            if len(command) > 200:
                command = command[:200]

            if using_mods(tools_config.ctx_swarm.get("game", {})) and command_prefix == "/":
                return submit_command(tools_config.ctx_swarm["game"], "/" + command, tools_config._current_tool_call_id)
            result = queue_send(
                "chat_queue", [command_prefix + command], ignore_queue=True
            )
            _add_tool_result_log(
                "minecraft_command", {"returned": "Command sent", "call_id": call_id}
            )
            _add_chat_event_waiter(call_id)
            return result
    except Exception as e:
        _add_tool_error_log(
            "minecraft_command", {"returned": str(e), "call_id": call_id}
        )
    return False


def minecraft_command_tool_status() -> str:
    """
    Get status of the minecraft command tool. Returns the server relevant commands that can be executed
    TODO: should show server chat/command cooldows
    """

    server_ip = tools_config.ctx_swarm["game"].get("server", "")

    # dict: key = command name, value = command patterns with description
    command_patterns: Dict[str, str] = YamlReader.read_yaml(
        os.path.join(
            REPO_RESOURCE_PATH, "Customization", "minecraft_command_patterns.yml"
        )
    )

    # dict: key = server ip, value = dict[command patterns list, description]
    server_bank: Dict[str, Dict[str, str]] = YamlReader.read_yaml(
        os.path.join(REPO_RESOURCE_PATH, "Customization", "minecraft_server_bank.yml")
    )

    command_examples = ""
    if not server_ip:
        server_ip = "default"
    if server_ip and server_ip in server_bank:
        command_examples = ""
        server_entry = server_bank[server_ip]
        server_command_patterns = server_entry.get("commands", [])
        for command_pattern in server_command_patterns:  # e.g. administrative
            pattern_examples = command_patterns.get(command_pattern, "")
            if pattern_examples:
                command_examples += f"\n{command_pattern} commands:\n{pattern_examples}"
        # TODO ideally put logic in command_description key, and overall info put somewhere near separated server info description
        base_description = server_entry.get("description", "")
        commands_description = server_entry.get("commands_description", "")
        description = (
            (base_description + "\n" + commands_description)
            if base_description
            else commands_description
        )
        if description:
            return (
                f"{command_examples}\n"
                f"Current server ip - {server_ip}. "
                f"Description: {description}"
            )
    return command_examples


def agent_action_tool_status() -> str:
    """
    Get status of the minecraft agent action tool.

    Returns:
        str, the list of server relevant actions that can be executed.
    """
    server_ip = tools_config.ctx_swarm["game"].get("server", "")
    print("DEBUG SERVER IP +=======" + server_ip)
    action_patterns: Dict[str, str] = YamlReader.read_yaml(
        os.path.join(
            REPO_RESOURCE_PATH, "Customization", "minecraft_action_patterns.yml"
        )
    )

    server_bank: Dict[str, Dict[str, str]] = YamlReader.read_yaml(
        os.path.join(REPO_RESOURCE_PATH, "Customization", "minecraft_server_bank.yml")
    )

    action_examples = ""
    if not server_ip:
        server_ip = "default"
    if server_ip and server_ip in server_bank:
        server_entry = server_bank[server_ip]
        server_action_patterns = server_entry.get("actions", [])
        for action_pattern in server_action_patterns:
            pattern_examples = action_patterns.get(action_pattern, "")
            if pattern_examples:
                action_examples += f"\n{action_pattern} actions:\n{pattern_examples}"
        description = server_entry.get("actions_description", "")
        if description:
            return f"{action_examples}\nCurrent server ip - {server_ip}. Description: {description}"
    return action_examples


# TODO add command chains like:
# @game murder -> disconnect -> join mysteryworld -> mm mode


def agent_action(action: str) -> bool:
    """Execute one literal UnionClef command, such as @goto 10 64 20 or @stop.

    This is a command interpreter, not another AI agent. Never pass a goal, prose,
    or a request to inspect/check/report something. Use get_game_status() for task
    state and get_nearby_blocks() for observed terrain. Choose coordinates yourself.
    Queue acceptance is not completed movement; check task state after issuing a command.
    Gather seeds and crop ingredients only via farm_crops; native @get seeds is disabled.
    Craft bread via obtain_item only from already harvested wheat in inventory.
    There is no @eat command; use eat_food for existing food. Read get_inventory first.

    Args:
        action: Exactly one command, e.g. "@goto 10 64 20", "@follow NickName", or "@stop". Never a natural-language task or semicolon chain.

    Common actions:
        "@follow PlayerName" — walk to and follow a player
        "@goto X Y Z" — walk to coordinates
        "@avoid PlayerName" — run away from player
        "@gesture PlayerName Hey" — wave/gesture at player
        "@stop" — stop current action

    Attacks must use hunt_hostiles/attack_hostile; players and peaceful mobs cannot be attacked.
    """
    # , @status removed, agent not need it
    # Validate before creating a run log or enqueueing a command.
    available = set(tools_config.ctx_swarm.get("game", {}).get("available_commands", [])) or CORE_COMMANDS
    first = action.strip().removeprefix("@").split(maxsplit=1) if isinstance(action, str) else []
    candidate = action
    if first and first[0] not in available and first[0] in TASK_COMMANDS_MAP:
        candidate = TASK_COMMANDS_MAP[first[0]] + (" " + first[1] if len(first) > 1 else "")
        if first[0] == "love":
            candidate += " forward"
        elif first[0] == "askpeace":
            candidate += " disagree"
    normalized = validate_action_command(candidate, available)
    from saruman.mod_goals import using_mods, submit_command
    if using_mods(tools_config.ctx_swarm.get("game", {})):
        return submit_command(tools_config.ctx_swarm["game"], normalized, tools_config._current_tool_call_id)
    target = goto_target(normalized)
    if target is not None:
        navigation = tools_config.ctx_swarm.get("game", {}).get("navigation", {})
        check_recent_failure(target, navigation.get("recent_failures", []))
        if navigation.get("status") == "stopping" or (navigation.get("status") in ("stop_failed", "stop_requested")
                                                      and tools_config.ctx_swarm.get("game", {}).get("hasActiveTask")):
            raise ValueError("Previous navigation has not confirmed cancellation. Check get_game_status before moving.")
    call_id = _add_tool_run_log("agent_action", {"action": normalized})
    try:
        action = normalized[1:]
        if action:
            final_split = action.split(" ")
            if len(final_split) > 0 and final_split[0] == "t":
                final_split = final_split[1:]
            if len(final_split) > 0:
                command_name = final_split[0].lower()
                if command_name in BLACKLIST_ACTIONS:
                    print(
                        "[MC ACT TOOL] TRIED TO RUN BLACKLISTED "
                        + command_name
                        + " ACTION!"
                    )
                    _add_tool_result_log(
                        "agent_action",
                        {"returned": "Agent action not sent", "call_id": call_id},
                    )
                    return False

            result = queue_send("chat_queue", ["@" + action], ignore_queue=True)
            _add_tool_result_log(
                "agent_action", {"returned": "Command queued; completion not confirmed", "call_id": call_id}
            )
            # INVESTIGATE WHY NOT WORKING!
            _add_agent_event_waiter(call_id)
            # _add_tool_event_waiter(
            #     call_id,
            #     tool_event_waiter=ToolEventWaiter(
            #         timeout=3,
            #         match_events=[{"type": "mc_executor_log"}],
            #         # {"env": "minecraft", "type": "mc_executor_log", "isEvent": True}
            #         # return_format=[{"field": "msg", "before": "", "after": "", "add_empty": False}]
            #     ),
            # )
            # TODO, need to check first output from autoclef logs
            # _add_tool_event_waiter
            return result
    except Exception as e:
        _add_tool_error_log("agent_action", {"returned": str(e), "call_id": call_id})
    return False


MC_CONTROL_TOOLS = [agent_action, minecraft_command]


def farm_crops(mode: str = "auto") -> dict:
    """Do ONE crop step: plant seeds, harvest ONLY ripe crops, or hoe empty dirt near water.

    Supports wheat, carrots, potatoes, beetroots. mode auto/plant/harvest. Uses existing
    seeds and hoe; no fertilizer, watering, growth waiting or animal slaughter. Follow
    an observed next_action once, wait for movement, then repeat. harvested only means
    the ripe block was removed; collected reports actual inventory gains. Stop on
    interaction_unconfirmed instead of blindly repeating. Young plants stay intact.
    When seeds run out, even plant mode harvests mature crops for planting supplies.
    waiting_for_growth means do another activity and let young crops grow naturally.
    Never replace this with native @get seeds/wheat/carrot/potato.
    """
    return farm_step(mode)


def _combat_game():
    game = tools_config.ctx_swarm.get("game", {})
    game["scepter_enabled"] = not tools_config.tool_bank.get("cast_scepter", {}).get("disabled", False)
    return game


def hunt_hostiles(uuid: Optional[str] = None) -> dict:
    """Find a hostile mob or confirmed recent attacker; prefer the Magical Scepter.

    Keep the returned target UUID to continue; never interpret disappearance as a kill.
    Villagers, animals and golems are always protected. Players and certain neutral
    monsters require recent native damage proof; chat claims never authorize attack.
    Spell XP/cooldown and noncombatant clearance are checked. A vanilla sword (with sweep clearance), axe or pickaxe
    is a fallback when magic is unavailable. Stop when health/food is low, a route fails or the
    target is lost. Follow next_action once and wait for movement before repeating.
    """
    failures = tools_config.ctx_swarm.get("game", {}).get("navigation", {}).get("recent_failures", [])
    return hunt_step(uuid, failures, _combat_game())


def attack_hostile(uuid: str) -> dict:
    """One guarded spell or melee attack on a hostile or confirmed attacker UUID.

    Villagers, livestock, animals and golems are never targets, even if they hit you.
    Players need fresh native damage evidence. Prefer the scepter; spells must have
    enough XP, no cooldown and no protected entity in their danger zone. killed=true requires
    observed zero health; attacked alone does not prove damage or death.
    """
    return attack_hostile_at(uuid, _combat_game())


def get_scepter_status() -> dict:
    """Inspect actual staff variants, both spells, XP sufficiency, durability and cooldown.

    Includes all twenty known spells and the current acquisition/cast state. This
    only reads the world. Possessing a blank or unconfigured staff is not readiness.
    """
    from saruman.scepter import status
    return status(tools_config.ctx_swarm.get("game", {}))


def ensure_scepter(allow_crafting: bool = True) -> dict:
    """Prepare equipment before work using server-advertised command permissions.

    With give and experience: supply missing self sword/shield, configured staff
    and advance XP reserve if the server supports magic. Verify inventory and XP;
    preflight_required requests a brief wait for confirmation, never assumed success.
    Otherwise only a sword is required; next_action may request ordinary acquisition.
    allow_crafting is retained for compatibility. Staff crafting/material gathering
    is not automatic. Emergency defense and rescue take priority over preparation.
    """
    from saruman.scepter import ensure
    return ensure(tools_config.ctx_swarm.get("game", {}), allow_crafting=allow_crafting)


def cast_scepter(action: str = "attack", uuid: Optional[str] = None, slot: Optional[int] = None) -> dict:
    """Cast ONE actual staff spell using attack or protect, selecting an observed slot.

    action attack needs an eligible observed target UUID; protect uses the equipped
    variant's second spell (buff, movement or guarded area effect). Read staff status
    to choose a slot. Villagers/animals/golems stay protected; players require a native
    confirmed hit. Area/projectile casts are blocked while noncombatants are tracked.
    XP, cooldown and sight are checked. cast_requested does not prove damage or a kill.
    """
    from saruman.scepter import cast
    return cast(_combat_game(), action, uuid, slot)


def defend_self() -> dict:
    """Respond to nearby hostiles or a recent native-confirmed attacker, preferring the staff.

    Never treats chat accusations as proof or retaliates against villagers, golems or
    animals. Stops prior native work once and waits for idle before attacking. Does
    not chase players; self-preservation in water has priority over fighting.
    """
    from saruman.hunting import defend_step
    game = _combat_game()
    if game.get("combat_runtime") == "reflex-v2":
        return dict(game.get("self_defense", {}).get("result", {"status": "waiting_for_bridge"}).items())
    return defend_step(game)


def explore_nearby(search_steps: int = 4, purpose: str = "explore") -> dict:
    """Find one short connected observed route into a new area; returns next_action.

    Does not move itself. Follow the command once, wait for native completion, rescan.
    No digging, bridges or invented destinations; recent failed destinations are excluded.
    Expands the observed area by dry walking, with budgets up to SARUMAN_SEARCH_MAX_MOVES,
    the mod's radius-5 snapshot can see farther. purpose keeps independent search memory.
    """
    game = tools_config.ctx_swarm.get("game", {})
    from saruman.terrain_search import search_max_moves

    if (isinstance(search_steps, bool) or not isinstance(search_steps, int)
            or not 1 <= search_steps <= search_max_moves()
            or purpose not in ("farm", "hunt", "trees", "explore")):
        raise ValueError("Use search_steps within SARUMAN_SEARCH_MAX_MOVES and a known activity purpose")
    searches = deepcopy(dict(game.get("work_searches", {}).items()))
    world = game.get("world_session")
    if world and game.get("work_search_world") != world:
        searches = {}
        game["work_search_trail"] = []
        game["work_search_exhausted"] = []
        game["work_search_retreat"] = None
        game["work_search_world"] = world
    state = searches.setdefault(purpose, {})
    state["trail"] = list(game.get("work_search_trail", []))
    state["exhausted"] = list(game.get("work_search_exhausted", []))
    if game.get("work_search_retreat"):
        state["retreat"] = dict(game["work_search_retreat"].items())
    else:
        state.pop("retreat", None)
    visited = []
    for memory in searches.values():
        for position in memory.get("visited", []):
            if position not in visited:
                visited.append(position)
    state["visited"] = visited[-128:]
    result = exploration_step(game.get("navigation", {}).get("recent_failures", []), state, search_steps)
    game["work_searches"] = searches
    game["work_search_trail"] = state.get("trail", [])
    game["work_search_exhausted"] = state.get("exhausted", [])
    game["work_search_retreat"] = state.get("retreat")
    return result


def tend_trees() -> dict:
    """Plant one oak/birch from observed suitable soil; leave it to grow naturally.

    Obtain a missing sapling with the returned action, or approach an observed site.
    Does not consume bone meal or replace saved counted goals. Sand is not assumed
    to be valid soil: the actual Minecraft planting/growth rules decide.
    """
    game = tools_config.ctx_swarm.get("game", {})
    if game.get("hasActiveTask"):
        return {"status": "waiting_for_game_task"}
    if game.get("tree_task", {}).get("status") == "active":
        return {"status": "waiting_for_saved_goal"}
    inventory = read_player_inventory()
    sapling = next((item["item"].removeprefix("minecraft:") for item in inventory.get("items", [])
                    if item.get("item") in ("minecraft:oak_sapling", "minecraft:birch_sapling")
                    and item.get("count", 0) > 0), None)
    if sapling is None:
        return {"status": "needs_materials", "reason_code": "no_saplings", "next_action": {
            "tool": "obtain_item", "arguments": {"item": "oak_sapling", "count": 1}}}
    sites = list(game.get("background_tree_sites", []))
    scan = find_tree_spots(sapling, search_steps=0, allow_exploration=False,
                           avoid_positions=sites, min_spacing=4)
    for spot in scan.get("spots", []):
        if spot["within_reach"]:
            planted = plant_sapling_at(*spot["position"], sapling=sapling)
            planted.setdefault("position", list(spot["position"]))
            if planted.get("already_present"):
                return {**planted, "status": "already_planted"}
            if planted.get("planted"):
                game["background_tree_sites"] = (sites + [list(spot["position"])])[-32:]
            if planted.get("planted"):
                return {**planted, "status": "planted"}
            status = planted.get("status")
            if status == "waiting_for_game_task":
                return planted
            if status == "missing_sapling":
                return {**planted, "status": "needs_materials", "reason_code": "no_saplings", "next_action": {
                    "tool": "obtain_item", "arguments": {"item": sapling, "count": 1}}}
            if status == "needs_move":
                approach = tree_approach(spot["position"], game.get("navigation", {}).get("recent_failures", []))
                if approach:
                    return {**planted, "status": "needs_move", "next_action": {
                        "tool": "agent_action", "arguments": {"action": approach["command"]}}}
            if status in ("invalid_soil_or_occupied", "protected_or_obstructed", "out_of_reach", "aim_failed", "needs_move"):
                return {**planted, "status": "blocked", "placement_status": status,
                        "reason": "Placement was rejected before any click: " + status}
            return {**planted, "status": "interaction_unconfirmed",
                    "reason": planted.get("hint") or "Planting result not confirmed; choose another activity and inspect before retrying"}
    approach = next(iter(scan.get("approaches", [])), None)
    if approach:
        return {"status": "needs_move", "next_action": {"tool": "agent_action",
                "arguments": {"action": approach["command"]}}}
    return {"status": "no_work", "reason_code": "no_tree_site", "scan": scan.get("rejected", {})}


def get_nearby_blocks(radius: int = 2) -> dict:
    """Inspect observed non-air blocks around the player without moving or modifying the world.

    Returns block names, counts and up to 12 sample coordinates per type. Use this to look for ground, water and
    obstacles before choosing a concrete @goto target; do not invent unseen terrain.

    Args:
        radius: Cube radius in blocks, from 1 to 5. Start with 2 to keep output small.
    """
    grouped = {}
    for block in read_nearby_blocks(radius):
        entry = grouped.setdefault(block["block"], {"block": block["block"], "count": 0, "positions": []})
        entry["count"] += 1
        if len(entry["positions"]) < 12:
            entry["positions"].append(block["position"])
    return {"radius": radius, "blocks_by_type": list(grouped.values()),
            "positions_per_type_limit": 12,
            "truncated": any(entry["count"] > 12 for entry in grouped.values())}


def get_inventory() -> dict:
    """Read actual inventory items, held item, health, hunger (food 0..20) and position.

    Use before deciding whether food/materials are missing. Full health is not full hunger.
    Normal food cannot be consumed at food=20. UnionClef autoEat handles eating when hungry.
    This reads the game and does not move, eat, craft or change inventory.
    """
    return read_player_inventory()


def obtain_item(item: str, count: int = 1) -> str:
    """Obtain or craft a catalogued Minecraft item, gathering required materials when needed.

    Uses UnionClef's resource/crafting catalogue, e.g. wooden_hoe or bread from existing wheat.
    Seeds and crop ingredients must come from mature harvests through farm_crops.
    Animal-derived materials and bed recipes are blocked because native gathering may
    kill peaceful animals. Read inventory and task state to verify actual completion.

    Args:
        item: Catalogue identifier such as bread or wooden_hoe. Not a sentence.
        count: Desired quantity in inventory, at least 1.
    """
    item = item.removeprefix("minecraft:")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", item) or count < 1:
        raise ValueError("Use an item identifier and a positive count, e.g. obtain_item('bread', 1)")
    guard_crop_inputs(f"@get {item} {count}")
    if item == "bread":
        guard_crop_inputs(f"@get {item} {count}", read_player_inventory())
    if not agent_action(f"@get {item} {count}"):
        raise RuntimeError("Resource task could not be queued")
    return f"Queued obtaining/crafting {item}, target count {count}. Completion not confirmed; check get_inventory and get_game_status."


def eat_food(item: str = "bread") -> dict:
    """Consume food from existing inventory and verify the item's count actually decreases.

    Selects the food, uses it for up to four seconds, then releases use and restores
    the previous hotbar selection. Reads actual hunger; does not obtain/craft missing food.
    If consumed=false, report the reason instead of claiming the food was eaten.

    Args:
        item: Food catalogue identifier, for example bread, apple, or carrot.
    """
    return consume_inventory_food(item)


def _require_no_counted_tree_task():
    if tools_config.ctx_swarm.get("game", {}).get("tree_task", {}).get("status") == "active":
        raise ValueError("A counted tree task is active. Resume manage_trees with its saved arguments so counts and the total bone meal budget remain correct.")


def apply_bone_meal(x: int, y: int, z: int) -> dict:
    """Apply ONE dose of bone meal from inventory to a planted tree sapling at exact block coordinates.

    Bone meal is an item used on a block; @place bone_meal is invalid. Inspect planted saplings
    with get_nearby_blocks and inventory with get_inventory first. The player must be within
    reach with a clear line of sight and no active movement/build task. This tool aims, selects
    bone meal (including from main inventory), right-clicks the actual target and restores slots.
    Reports applied, consumed, tree_grown and target_after separately. A dose may be consumed
    without growing a tree; multiple doses can be needed. Do not claim a tree grew unless
    tree_grown=true. If applied=false, read the reason; do not repeat unconfirmed clicks blindly.

    Args:
        x: X coordinate of the planted sapling block, not the soil below it.
        y: Y coordinate of the planted sapling block.
        z: Z coordinate of the planted sapling block.
    """
    _require_no_counted_tree_task()
    return apply_bone_meal_at(x, y, z)


def get_minecraft_procedure(task: str = "") -> dict:
    """Read a detailed local execution guide for a Minecraft task; empty task lists available guides.

    Use grow_tree for full tree growth, plant_sapling for planting only, apply_bone_meal
    for fertilizing an existing plant. These are instructions, not completed world actions.

    Args:
        task: Procedure ID or short requested outcome in Russian/English.
    """
    return read_procedure(task)


def find_tree_planting_spots(sapling: str = "oak_sapling", radius: int = 5,
                             candidate_limit: int = 128, search_steps: int = 4) -> dict:
    """Find concrete nearby planting coordinates in one bounded read-only terrain scan, without moving.

    Checks actual sapling soil rules (Minecraft canPlaceAt), placement policy and conservative
    growth clearance. Returns up to three spots, rejection counts and safe approaches.
    If no local site/approach, exploration.next_action proposes a walk to NEW observed terrain.
    Execute it once, wait for arrival, then rescan; do not refuse after one local snapshot.
    Search memory resets on a new operator directive; repeated reads do not spend moves.
    This replaces repeated prose about searching. For complete growth prefer grow_tree.

    Args:
        sapling: oak_sapling or birch_sapling. Single-sapling procedures only.
        radius: Local search radius, 2 to 5 blocks. No indefinite exploration.
        candidate_limit: Width of each local scan, 24 to 256, default 128.
        search_steps: Maximum proposed exploration walks per task, 0 to 8, default 4. Zero disables exploration.
    """
    game = tools_config.ctx_swarm["game"]
    # RedisNestedDict is a persistence proxy, not a mutable dict: take a plain
    # snapshot (including nested lists), then commit it after the tool succeeds.
    state = deepcopy(dict(game.get("tree_search", {}).items()))
    result = find_tree_spots(sapling, radius, candidate_limit, search_steps, state,
                             game.get("navigation", {}).get("recent_failures", []))
    game["tree_search"] = state
    return result


def manage_trees(count: int = 1, mode: str = "plant", sapling: str = "oak_sapling",
                 max_bone_meal: Optional[int] = None, candidate_limit: int = 128,
                 search_steps: int = 4, new_task: bool = False) -> dict:
    """Start/resume a persisted counted tree task, including self-initiated goals.

    Use for 'plant two oaks' or 'grow N trees'. One bounded step per call; repeat
    with the SAME arguments or follow next_action until progress.completed=count.
    Checks resources for all remaining plantings, plans separated sites, verifies
    each sapling/trunk, preserves partial progress across collection and movement.
    Existing saplings do not count as new planting; grow may reuse existing saplings.
    Read get_game_status for progress. Completed calls are idempotent. Stop or a
    new operator assignment replaces the task; an explicit 'continue' keeps it.
    A paused checkpoint resumes with saved counts, positions and dose budget.
    No native task is queued here;
    execute returned obtain_item/agent_action first, wait, then resume manage_trees.

    Args:
        count: Requested number of trees, 1..32.
        mode: plant means new saplings, grow means verified trunks (uses bone meal).
        sapling: oak_sapling or birch_sapling.
        max_bone_meal: TOTAL allowed attempt/dose budget across the task, 0..256. Omit for count*5 in grow mode. At most five attempts per sapling, preserved across calls. Preserve any stricter operator limit. Plant never uses bone meal.
        candidate_limit: Local search width 24..256, default 128.
        search_steps: Exploration walks 0..8, default 4. Never move if operator forbids movement.
        new_task: Explicitly replace an existing/completed goal. False resumes without resetting counts/budget.
    """
    config = tree_tasks.task_config(count, mode, sapling, max_bone_meal, candidate_limit, search_steps)
    game = tools_config.ctx_swarm["game"]
    task = deepcopy(dict(game.get("tree_task", {}).items()))
    search = deepcopy(dict(game.get("tree_search", {}).items()))
    if new_task or not task:
        task = tree_tasks.new_task(config)
        search = {}
    elif task["config"] != config:
        raise ValueError("A different counted task is stored; use its original arguments to resume, or new_task=true for a new goal")
    resume_paused(task)

    def save(value):
        value["progress"] = tree_tasks.progress(value)
        game["tree_task"] = deepcopy(value)
        game["tree_search"] = deepcopy(search)

    save(task)
    result = tree_tasks.advance(task, save, search, game.get("navigation", {}).get("recent_failures", []))
    save(task)
    return result


def plant_sapling(x: int, y: int, z: int, sapling: str = "oak_sapling") -> dict:
    """Plant ONE sapling at a validated spot and verify its appearance; never use a guessing @place loop.

    Use coordinates from find_tree_planting_spots. Checks soil, policy, space and actual crosshair.
    Does not move, break blocks or use bone meal. planted=true is planting, not a grown tree.

    Args:
        x: X coordinate of the empty sapling cell above soil.
        y: Y coordinate of the sapling cell, not the soil below it.
        z: Z coordinate of the sapling cell.
        sapling: oak_sapling or birch_sapling.
    """
    _require_no_counted_tree_task()
    return plant_sapling_at(x, y, z, sapling)


def grow_tree(sapling: str = "oak_sapling", max_bone_meal: int = 5,
              candidate_limit: int = 128, search_steps: int = 4) -> dict:
    """Fast bounded procedure: find a valid spot, plant once if needed, fertilize until a trunk is observed.

    Prefer this for 'grow a tree' to avoid a model call for every scan, slot and dose. Reuses
    an existing nearby matching sapling. Missing materials are returned for obtain_item;
    active native work is not interrupted. No blind click retries or automatic walking/digging.
    Success is ONLY status=grown. Other statuses include needs_materials, needs_move, no_site,
    waiting_for_game_task, interaction_not_confirmed and bone_meal_budget_exhausted.
    needs_move may propose exploration of a new area, not just approach to an existing site.
    Execute next_action, wait, then call grow_tree again before declaring no suitable place.

    Args:
        sapling: oak_sapling or birch_sapling; 2x2 trees are unsupported by this procedure.
        max_bone_meal: Explicit dose budget, from 1 to 16, default 5. Never more than five attempts in this call. For a persistent limit across resumptions use manage_trees.
        candidate_limit: Search width per snapshot, 24 to 256 candidates, default 128.
        search_steps: Exploration depth per task, 0 to 8 walks, default 4; zero means stay local.
    """
    _require_no_counted_tree_task()
    game = tools_config.ctx_swarm["game"]
    state = deepcopy(dict(game.get("tree_search", {}).items()))
    result = grow_tree_fast(sapling, max_bone_meal, candidate_limit, search_steps, state,
                            game.get("navigation", {}).get("recent_failures", []))
    game["tree_search"] = state
    return result
