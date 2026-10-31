"""Bounded hunting of observed, explicitly hostile Minecraft entities by UUID."""

import math
import time
from contextlib import ExitStack
from copy import deepcopy

from py4j.protocol import Py4JError

from saruman import scepter
from saruman.combat_policy import living_entities, permitted
from saruman.combat_pursuit import (
    current_position,
    pursuit_limit,
    watch_target,
    watched,
)
from saruman.fertilize import select_inventory_item
from saruman.minecraft import _java_to_python
from saruman.peaceful import hostile_type
from saruman.trees import _gateway, tree_approach
from saruman.world_actions import interaction_manager, on_client

TOOLS = frozenset(
    f"minecraft:{material}_{tool}"
    for material in ("wooden", "stone", "iron", "golden", "diamond", "netherite")
    for tool in ("sword", "axe", "pickaxe")
)
_last_attack = 0.0


def _sweep_clear(gateway, target, game):
    return not any(
        other["uuid"] != target["uuid"]
        and not permitted(other, (game or {}).get("combat_threats", {}))
        and math.dist(other["position"], target["position"]) <= 4
        for _, other in living_entities(gateway)
    )


def _entities(gateway, game=None):
    if game is not None:
        yield from (
            (entity, target)
            for entity, target in living_entities(gateway)
            if permitted(target, game.get("combat_threats", {}))
        )
        return
    mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
    registry = gateway.jvm.net.minecraft.class_7923.field_41177
    for entity in mod.getEntityTracker().getAllTrackedEntities():
        kind = str(registry.method_10221(entity.method_5864()))
        if not hostile_type(kind) or not entity.method_5805():
            continue
        yield (
            entity,
            {
                "uuid": str(entity.method_5845()),
                "type": kind,
                "position": [
                    float(entity.method_23317()),
                    float(entity.method_23318()),
                    float(entity.method_23321()),
                ],
                "health": float(entity.method_6032()),
                "wet": bool(entity.method_5799()),
                "submerged": bool(entity.method_5869()),
            },
        )


def scan_hostiles(radius=16, game=None):
    if type(radius) is not int or not 1 <= radius <= 24:
        raise ValueError("radius must be 1..24")
    gateway = _gateway()
    try:
        entry = gateway.entry_point
        if not entry.inGame():
            return {"status": "disconnected", "targets": []}
        state = _java_to_python(entry.getGameState()).get("self", {})
        if float(state.get("hp", state.get("health", 0))) <= 0:
            return {"status": "waiting_for_respawn"}
        position = [float(n) for n in state["pos"].split(",")]
        targets = []
        for entity, target in _entities(gateway, game):
            target["distance"] = math.dist(position, target["position"])
            if target["distance"] <= radius:
                target["visible"] = bool(
                    gateway.jvm.adris.altoclef.AltoClef.getInstance()
                    .getPlayer()
                    .method_6057(entity)
                )
                targets.append(target)
        return {
            "status": "observed",
            "targets": sorted(targets, key=lambda t: t["distance"]),
        }
    finally:
        gateway.close()


def attack_hostile_at(uuid, game=None, *, emergency=False):
    """One exact attack. Sword swings require clearance from noncombatants."""
    result = _attack_hostile_at(uuid, game, emergency=emergency)
    if game is not None:
        game["combat_attack"] = {**result, "target_uuid": uuid, "at": time.time()}
    return result


def _attack_hostile_at(uuid, game=None, *, emergency=False):
    global _last_attack
    if game is not None:
        result = scepter.cast(game, "attack", uuid)
        if result["status"] not in (
            "mod_unavailable",
            "missing_scepter",
            "insufficient_experience",
            "protected_entity_near_spell",
            "out_of_reach",
            "unknown_spell",
            "tool_unavailable",
        ):
            return result
    with ExitStack() as cleanup:
        gateway = _gateway()
        cleanup.callback(gateway.close)
        entry = gateway.entry_point
        if not entry.inGame():
            return {"status": "disconnected"}
        if entry.hasActiveTask():
            return {"status": "waiting_for_game_task"}
        state = _java_to_python(entry.getGameState()).get("self", {})
        if float(state.get("hp", state.get("health", 0))) <= 0:
            return {"status": "waiting_for_respawn"}
        if not emergency and (
            float(state.get("hp", state.get("health", 0))) < 10
            or int(state.get("food", 0)) < 8
        ):
            return {
                "status": "unsafe_health",
                "hint": "Eat and recover before hunting.",
            }
        match = next(
            ((e, t) for e, t in _entities(gateway, game) if t["uuid"] == uuid), None
        )
        if match is None:
            return {"status": "target_lost", "killed": False}
        entity, target = match
        player = gateway.jvm.adris.altoclef.AltoClef.getInstance().getPlayer()
        if float(player.method_5739(entity)) > 2.8 or not player.method_6057(entity):
            return {"status": "out_of_reach", "target": target}
        inventory = _java_to_python(entry.getInventoryFull())
        if not inventory.get("ok"):
            return {"status": "inventory_unavailable"}
        weapons = sorted(
            (
                s
                for s in inventory.get("slots", [])
                if s.get("item") in TOOLS
                and s.get("count", 0) > 0
                and 0 <= s.get("index", -1) < 36
            ),
            key=lambda s: (
                not s["item"].endswith("_sword"),
                -next(
                    (
                        i
                        for i, m in enumerate(
                            (
                                "wooden",
                                "golden",
                                "stone",
                                "iron",
                                "diamond",
                                "netherite",
                            )
                        )
                        if m in s["item"]
                    ),
                    0,
                ),
            ),
        )
        weapon = next(
            (
                s["item"]
                for s in weapons
                if s.get("item") in TOOLS
                and s.get("count", 0) > 0
                and 0 <= s.get("index", -1) < 36
            ),
            None,
        )
        if (
            weapon
            and weapon.endswith("_sword")
            and not _sweep_clear(gateway, target, game)
        ):
            alternative = next(
                (s["item"] for s in weapons if not s["item"].endswith("_sword")), None
            )
            if alternative:
                weapon = alternative
            elif emergency:
                weapon = None  # A verified empty hand cannot sweep a bystander.
            else:
                return {"status": "protected_entity_near_sword", "target": target}
        if not weapon and not emergency:
            return {
                "status": "missing_safe_weapon",
                "hint": "Obtain a vanilla sword; an axe or pickaxe can be used meanwhile.",
            }
        if not entry.closeOpenScreen():
            return {"status": "screen_open"}
        if weapon:
            select_inventory_item(entry, cleanup, inventory, weapon)
        else:
            # Defend immediately while the idle equipment planner obtains a sword.
            occupied = {
                s["index"] for s in inventory.get("slots", []) if s.get("count", 0) > 0
            }
            empty = next((i for i in range(9) if i not in occupied), None)
            if empty is None:
                return {"status": "missing_safe_weapon"}
            cleanup.callback(entry.selectHotbar, inventory["selectedHotbar"])
            if not entry.selectHotbar(empty):
                return {"status": "equipment_changed"}
            equipped = _java_to_python(entry.getInventoryFull())
            if equipped.get("selectedHotbar") != empty or any(
                s.get("index") == empty and s.get("count", 0) > 0
                for s in equipped.get("slots", [])
            ):
                return {"status": "equipment_changed"}
            weapon = "unarmed"
        # Capture the actual entity object, not a name/crosshair that can switch to a villager.
        if not entity.method_5805() or not permitted(
            target, (game or {}).get("combat_threats", {})
        ):
            return {"status": "target_lost", "killed": False}
        if time.monotonic() - _last_attack < 1:
            return {"status": "waiting_for_cooldown", "target": target}
        # Recheck sweep clearance immediately before the exact attack dispatch.
        if weapon.endswith("_sword") and not _sweep_clear(gateway, target, game):
            return {"status": "protected_entity_near_sword", "target": target}
        before = float(entity.method_6032())
        _last_attack = time.monotonic()
        entry.lookAt(
            target["position"][0], float(entity.method_23320()), target["position"][2]
        )
        on_client(
            gateway,
            interaction_manager(gateway),
            "net.minecraft.class_636",
            "method_2918",
            ("net.minecraft.class_1657", "net.minecraft.class_1297"),
            (player, entity),
        )
        on_client(
            gateway,
            player,
            "net.minecraft.class_1309",
            "method_6104",
            ("net.minecraft.class_1268",),
            (gateway.jvm.net.minecraft.class_1268.field_5808,),
        )
        time.sleep(0.15)
        after = float(entity.method_6032())
        killed = after <= 0
        return {
            "status": "killed" if killed else "attacked",
            "target": target,
            "health_before": before,
            "health_after": after,
            "damage_confirmed": after < before,
            "killed": killed,
        }


def hunt_step(uuid=None, failed_goals=(), game=None):
    scan = scan_hostiles(game=game) if game is not None else scan_hostiles()
    if scan["status"] != "observed":
        return scan
    target = next(
        (
            t
            for t in scan["targets"]
            if (uuid is None or t["uuid"] == uuid)
            and not (game is not None and watched(game, t))
        ),
        None,
    )
    if target is None:
        return {
            "status": "no_target" if uuid is None else "target_lost",
            "killed": False,
        }
    gateway = _gateway()
    try:
        state = _java_to_python(gateway.entry_point.getGameState()).get("self", {})
        if (
            float(state.get("hp", state.get("health", 0))) < 10
            or int(state.get("food", 0)) < 8
        ):
            return {"status": "unsafe_health"}
    finally:
        gateway.close()
    if game is not None or target["distance"] <= 2.8:
        attack = (
            attack_hostile_at(target["uuid"], game)
            if game is not None
            else attack_hostile_at(target["uuid"])
        )
        if attack.get("status") != "out_of_reach":
            return attack
    if game is not None:
        # The bridge owns closing/defense. Hobbies must not recreate a deferred
        # chase through ordinary agent_action while the reflex releases its lock.
        return {
            "status": "no_target",
            "target": target,
            "reason": "Observed target is out of reach; bounded bridge defense owns approaches",
        }
    if target.get("wet") or target.get("submerged"):
        return {
            "status": "blocked",
            "reason": "Attack from shore only",
            "target": target,
        }
    approach = tree_approach(
        [math.floor(v) for v in target["position"]], failed_goals, allow_partial=True
    )
    if not approach:
        return {
            "status": "blocked",
            "target": target,
            "reason": "No observed approach to hostile",
        }
    return {
        "status": "needs_move",
        "target": target,
        "next_action": {
            "tool": "agent_action",
            "arguments": {"action": approach["command"]},
        },
    }


def _end_defense(game, navigation, status, reason=None):
    previous = dict(game.get("self_defense", {}).items())
    if previous.get("approaching") and navigation is not None:
        gateway = _gateway()
        try:
            if gateway.entry_point.hasActiveTask():
                if not previous.get("stop_sent"):
                    previous.update(stop_sent=True, ending=status, reason=reason)
                    game["self_defense"] = {**previous, "active": True}
                    navigation.interrupt_for_combat(gateway.entry_point, game)
                return {"status": "waiting_for_task_stop", "reason": reason}
        finally:
            gateway.close()
    game["self_defense"] = {
        **previous,
        "active": False,
        "approaching": False,
        "ending": None,
        "status": status,
        "reason": reason,
    }
    return {"status": status, "reason": reason}


def defend_step(game, navigation=None, *, before_stop=None):
    """Preempt work for nearby hostiles or a confirmed attacker, then close and fight."""
    if game.get("survival", {}).get("active") or game.get("alive") is False:
        return {"status": "waiting_for_survival"}
    previous = dict(game.get("self_defense", {}).items())
    if previous.get("ending"):
        return _end_defense(
            game, navigation, previous["ending"], previous.get("reason")
        )
    threats = game.get("combat_threats", {})
    current = {
        uuid for uuid, proof in threats.items() if proof.get("until", 0) > time.time()
    }
    scan = scan_hostiles(radius=24, game=game)
    if scan["status"] != "observed":
        return scan
    candidates = [
        t
        for t in scan["targets"]
        if permitted(t, threats)
        and (not watched(game, t) or _watched_spell_ready(game, t))
        and (
            t["uuid"] in current
            and t["distance"] <= (12 if t["type"] == "minecraft:player" else 24)
            or t["distance"] <= (12 if t["type"] == "minecraft:player" else 16)
        )
    ]
    target = min(
        candidates,
        key=lambda t: (
            t["uuid"] not in current,
            t["uuid"] != previous.get("target", {}).get("uuid"),
            t["distance"],
        ),
        default=None,
    )
    if target is None:
        return _end_defense(game, navigation, "no_threat")
    navigation_deferred = watched(game, target)
    if navigation_deferred:
        watch = dict(game.get("combat_watch", {}).items())
        watch[target["uuid"]] = {
            **dict(watch[target["uuid"]].items()),
            "next_cast_check": time.time() + 2,
        }
        game["combat_watch"] = watch
    defense = previous
    if (
        defense.get("active")
        and defense.get("target", {}).get("uuid") != target["uuid"]
    ):
        defense = {
            "active": True,
            "target": target,
            "stop_sent": False,
            "protect_attempted": False,
            "approaching": defense.get("approaching", False),
            "approach_at": 0,
            "anchor": defense.get("anchor"),
            "interruption_id": defense.get("interruption_id"),
        }
    if not defense.get("active"):
        defense = {
            "active": True,
            "target": target,
            "status": "defending",
            "stop_sent": False,
            "interruption_id": time.time_ns(),
            "anchor": current_position(game),
        }
    defense["target"] = target
    defense.setdefault("started_at", time.time())
    game["self_defense"] = defense
    if defense.get("approaching"):
        reason = (
            "Target entered water"
            if (target.get("wet") or target.get("submerged"))
            and not defense.get("shore_approach")
            else pursuit_limit(defense, target, current_position(game))
        )
        if reason:
            watch_target(game, target, reason)
            return _end_defense(game, navigation, "watching_target", reason)
        game["self_defense"] = defense
    gateway = _gateway()
    try:
        if gateway.entry_point.hasActiveTask():
            if (
                defense.get("approaching")
                and (
                    target["distance"] > max(2.7, _spell_range(game) - 0.25)
                    or not target.get("visible", True)
                )
                and time.time() - defense.get("approach_at", 0) < 3
            ):
                return {"status": "approaching_threat", "target": target}
            was_approaching = defense.pop("approaching", False)
            if was_approaching:
                defense["stop_sent"] = False
            if not defense["stop_sent"]:
                if before_stop is not None and not was_approaching:
                    try:
                        before_stop(gateway, game)
                    except (
                        Py4JError,
                        OSError,
                        ValueError,
                        TypeError,
                        RuntimeError,
                    ) as error:
                        game["combat_checkpoint"] = {
                            "status": "unavailable",
                            "reason": type(error).__name__,
                        }
                defense["stop_sent"] = True
                game["self_defense"] = defense
                saved = game.get("tree_task", {})
                if saved.get("status") == "active":
                    deferred = deepcopy(list(game.get("deferred_goals", [])))
                    if not any(
                        row["task"].get("id") == saved.get("id") for row in deferred
                    ):
                        deferred.append(
                            {
                                "task": deepcopy(dict(saved.items())),
                                "search": deepcopy(
                                    dict(game.get("tree_search", {}).items())
                                ),
                            }
                        )
                    game["deferred_goals"] = deferred
                    game["tree_task"] = {
                        **dict(saved.items()),
                        "status": "paused",
                        "pause_reason": "Self defense",
                    }
                if navigation is not None:
                    navigation.interrupt_for_combat(gateway.entry_point, game)
                else:
                    gateway.entry_point.stopPathing()
            return {"status": "stopping_for_defense", "target": target}
        if (
            defense.get("approaching")
            and time.time() - defense.get("approach_at", 0) < 0.75
        ):
            return {"status": "waiting_for_game_task", "target": target}
        defense["approaching"] = False
        # A previous stop completed; the next owned approach can be interrupted once.
        defense["stop_sent"] = False
    finally:
        gateway.close()
    if not defense.get("protect_attempted") and target["distance"] <= 2.8:
        defense["protect_attempted"] = True
        game["self_defense"] = defense
        protection = scepter.cast(game, "protect")
        if protection.get("status") == "cast_requested":
            return protection
    result = attack_hostile_at(target["uuid"], game, emergency=True)
    if navigation_deferred and result.get("status") not in (
        "cast_requested",
        "killed",
        "attacked",
        "waiting_for_cooldown",
    ):
        return _end_defense(game, navigation, "watching_target", result.get("status"))
    if (
        result.get("status") in ("out_of_reach", "protected_entity_near_sword")
        and navigation is not None
    ):
        if (target.get("wet") or target.get("submerged")) and _spell_range(game) <= 2.8:
            watch_target(
                game, target, "Water target is not reachable from this dry position"
            )
            return _end_defense(
                game,
                navigation,
                "watching_target",
                "Attack from shore only; do not enter water after the target",
            )
        position = current_position(game)
        if not defense.get("anchor"):
            defense["anchor"] = position
        defense.setdefault("pursuit_started_at", time.time())
        if position:
            defense.setdefault("waypoints", [[math.floor(v) for v in position]])
        reason = pursuit_limit(defense, target, position)
        if reason:
            watch_target(game, target, reason)
            return _end_defense(game, navigation, "watching_target", reason)
        failures = list(game.get("navigation", {}).get("recent_failures", []))
        approach = tree_approach(
            [math.floor(v) for v in target["position"]],
            failures,
            allow_partial=True,
            previous_waypoints=defense.get("waypoints", []),
            one_step=True,
        )
        if approach:
            gateway = _gateway()
            try:
                navigation.send(
                    gateway.entry_point, approach["command"], game, purpose="combat"
                )
            except ValueError as error:
                watch_target(game, target, str(error))
                return _end_defense(game, navigation, "watching_target", str(error))
            finally:
                gateway.close()
            defense.update(
                approaching=True,
                approach_at=time.time(),
                shore_approach=bool(target.get("wet") or target.get("submerged")),
            )
            if approach.get("standing_position"):
                defense["waypoints"] = (
                    defense.get("waypoints", []) + [approach["standing_position"]]
                )[-16:]
            result = {"status": "approaching_threat", "target": target}
        else:
            watch_target(game, target, "No untried observed dry approach")
            return _end_defense(
                game, navigation, "watching_target", "No untried observed dry approach"
            )
    game["self_defense"] = {
        **defense,
        "status": result["status"],
        "active": result["status"]
        in (
            "cast_requested",
            "attacked",
            "waiting_for_cooldown",
            "waiting_for_game_task",
            "stopping_for_defense",
            "approaching_threat",
            "out_of_reach",
            "protected_entity_near_sword",
            "missing_safe_weapon",
        ),
    }
    return result


def _spell_range(game):
    if game.get("scepter_enabled") is False:
        return 0
    return max(
        (
            scepter.SPELLS.get(row.get("attack"), {}).get("range", 0)
            for row in game.get("scepter_supply", {}).get("scepters", [])
            if row.get("enough_experience")
        ),
        default=0,
    )


def _watched_spell_ready(game, target):
    """Route backoff must not prevent firing from shore when a target is in range."""
    if not target.get("visible") or game.get("scepter_enabled") is False:
        return False
    saved = game.get("combat_watch", {}).get(target["uuid"], {})
    if saved.get("next_cast_check", 0) > time.time():
        return False
    return target["distance"] <= _spell_range(game)
