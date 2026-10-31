"""Self supply only through server-advertised commands, with observed confirmation."""

import re

from py4j.protocol import Py4JError

STAFF = "magicalscepter:magical_scepter"
CONFIRM_SECONDS = 4
RETRY_SECONDS = 60


def command_capabilities(gateway):
    try:
        network = gateway.jvm.net.minecraft.class_310.method_1551().method_1562()
        if network is None:
            return {"give": None, "experience": None, "verified": False}
        root = network.method_2886().getRoot()
        return {
            "give": next(
                (
                    name
                    for name in ("give", "minecraft:give")
                    if root.getChild(name) is not None
                ),
                None,
            ),
            "experience": next(
                (
                    name
                    for name in (
                        "experience",
                        "xp",
                        "minecraft:experience",
                        "minecraft:xp",
                    )
                    if root.getChild(name) is not None
                ),
                None,
            ),
            "verified": True,
        }
    except (Py4JError, OSError, ValueError, TypeError, RuntimeError):
        return {"give": None, "experience": None, "verified": False}


def _sword(inventory):
    return any(
        0 <= row.get("index", -1) < 36
        and row.get("count", 0) > 0
        and row.get("item", "").startswith("minecraft:")
        and row.get("item", "").endswith("_sword")
        for row in inventory.get("slots", [])
    )


def prepare(gateway, game, inventory, staff_rows, magic_available, now):
    if not inventory.get("ok"):
        return {"status": "inventory_unavailable", "preflight_required": False}
    capabilities = command_capabilities(gateway)
    username = str(gateway.entry_point.getUsername())
    world = int(
        gateway.jvm.java.lang.System.identityHashCode(
            gateway.jvm.adris.altoclef.AltoClef.getInstance().getWorld()
        )
    )
    scope = {"world": world, "username": username}
    previous = dict(game.get("equipment_supply", {}).items())
    requests = (
        dict(previous.get("requests", {}).items())
        if previous.get("scope") == scope
        else {}
    )
    full = bool(capabilities["give"] and capabilities["experience"])
    sword = _sword(inventory)
    shield = any(
        row.get("item") == "minecraft:shield"
        and row.get("count", 0) > 0
        and (0 <= row.get("index", -1) < 36 or row.get("index") == 40)
        for row in inventory.get("slots", [])
    )
    needed = {"sword": sword}
    if full:
        needed["shield"] = shield
        if magic_available:
            needed["staff"] = bool(staff_rows)
    cost = min((max(0, int(row["experience_cost"])) for row in staff_rows), default=4)
    reserve = min(10000, max(100, cost * 20))
    minimum = min(10000, max(20, cost * 5))
    points = None
    if full and magic_available:
        player = gateway.jvm.adris.altoclef.AltoClef.getInstance().getPlayer()
        points = int(
            gateway.jvm.io.github.pistonpoek.magicalscepter.util.PlayerExperience.getTotalExperience(
                player
            )
        )
        needed["experience"] = bool(player.method_56992()) or (
            points >= minimum
            and (not staff_rows or any(row["enough_experience"] for row in staff_rows))
        )
    state = {
        "scope": scope,
        "mode": "command_kit" if full else "sword_only",
        "capabilities": capabilities,
        "magic_available": magic_available,
        "observed": needed,
        "xp_points": points,
        "xp_minimum": minimum,
        "xp_reserve": reserve,
        "requests": requests,
        "verified_at": now,
    }
    # Publish reservation before every dispatch, including unknown transport results.
    for key, present in needed.items():
        if present and key in requests:
            requests[key] = {
                **requests[key],
                "status": "confirmed",
                "completion_confirmed": True,
            }
    commands = {}
    if capabilities["give"] and re.fullmatch(r"[A-Za-z0-9_-]{1,16}", username):
        prefix = f"/{capabilities['give']} {username} "
        commands = {
            "sword": prefix + "minecraft:iron_sword 1",
            "shield": prefix + "minecraft:shield 1",
            "staff": prefix
            + STAFF
            + '[magicalscepter:scepter_contents="magicalscepter:magical"] 1',
        }
        if "experience" in needed:
            commands["experience"] = (
                f"/{capabilities['experience']} add {username} {max(1, reserve - points)} points"
            )
    for key, present in needed.items():
        if present or key not in commands:
            continue
        request = requests.get(key, {})
        if request.get("status") != "confirmed" and request.get("retry_at", 0) > now:
            request["status"] = (
                "pending"
                if not request.get("transport_error")
                and now - request["at"] < CONFIRM_SECONDS
                else "unconfirmed"
            )
            requests[key] = request
            continue
        requests[key] = {
            "status": "pending",
            "at": now,
            "retry_at": now + RETRY_SECONDS,
            "command": commands[key],
            "completion_confirmed": False,
        }
        game["equipment_supply"] = state
        try:
            gateway.entry_point.ChatMessage(commands[key])
        except (Py4JError, OSError, ValueError, TypeError, RuntimeError) as error:
            requests[key].update(
                status="unconfirmed", transport_error=type(error).__name__
            )
            game["equipment_supply"] = state
            break
    ready = all(needed.values())
    pending = any(
        not present
        and key in commands
        and requests.get(key, {}).get("status") == "pending"
        for key, present in needed.items()
    )
    state.update(
        status="ready"
        if ready
        else "preparing"
        if pending
        else "unconfirmed"
        if commands
        else "needs_sword",
        ready=ready,
        preflight_required=pending,
    )
    game["equipment_supply"] = state
    if "experience" in needed:
        request = requests.get("experience", {})
        game["scepter_experience"] = {
            **request,
            "status": "ready"
            if needed["experience"]
            else "xp_pending"
            if request.get("status") == "pending"
            else "xp_unconfirmed",
            "points_observed": points,
            "points_requested": max(0, reserve - points),
            "completion_confirmed": needed["experience"],
        }
    else:
        game["scepter_experience"] = {
            "status": "not_required",
            "reason": "Sword-only policy or server has no scepter mod",
        }
    result = {
        "status": "ready"
        if full and ready
        else "sword_ready"
        if ready
        else "equipment_preparing"
        if pending
        else "equipment_unconfirmed"
        if commands
        else "needs_sword",
        "mode": state["mode"],
        "preflight_required": pending,
        "magic_available": magic_available,
        "observed": needed,
        "command_capabilities": capabilities,
        "completion_confirmed": ready,
    }
    if not sword and not pending:
        result["next_action"] = {
            "tool": "obtain_item",
            "arguments": {"item": "wooden_sword", "count": 1},
        }
    game["scepter_supply"] = {**result, "scepters": staff_rows, "verified_at": now}
    return result
