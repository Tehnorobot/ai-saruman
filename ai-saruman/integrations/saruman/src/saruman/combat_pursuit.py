"""Bounded pursuit and a short watch list; never treats a deferred target as dead."""

import math
import time

PURSUIT_SECONDS = 24
STALL_SECONDS = 8
LEASH_BLOCKS = 12
WATCH_SECONDS = 30
CONTACT_BLOCKS = 2.8


def current_position(game):
    try:
        values = [float(v) for v in game.get("position", "").split(",")]
        return (
            values
            if len(values) == 3 and all(math.isfinite(v) for v in values)
            else None
        )
    except (ValueError, AttributeError):
        return None


def watch_target(game, target, reason, now=None):
    now = time.time() if now is None else now
    watch = {
        key: dict(value.items())
        for key, value in game.get("combat_watch", {}).items()
        if value.get("until", 0) > now
    }
    watch[target["uuid"]] = {
        "until": now + WATCH_SECONDS,
        "at": now,
        "position": list(target["position"]),
        "type": target.get("type", "hostile"),
        "reason": reason,
        "damage_at": game.get("combat_threats", {})
        .get(target["uuid"], {})
        .get("at", 0),
    }
    game["combat_watch"] = dict(list(watch.items())[-32:])


def watched(game, target, now=None):
    now = time.time() if now is None else now
    saved = game.get("combat_watch", {}).get(target["uuid"], {})
    if saved.get("until", 0) <= now or (
        target["distance"] <= CONTACT_BLOCKS and target.get("visible", True)
    ):
        return False
    proof = game.get("combat_threats", {}).get(target["uuid"], {})
    if (
        proof.get("source") == "native_damage"
        and proof.get("until", 0) > now
        and proof.get("at", 0) > saved.get("damage_at", 0)
    ):
        return False
    # A target that came out of water or moved substantially merits a fresh look.
    return (
        bool(target.get("wet") or target.get("submerged"))
        or math.dist(target["position"], saved["position"]) < 3
    )


def pursuit_limit(defense, target, position, now=None):
    now = time.time() if now is None else now
    if (
        position
        and defense.get("anchor")
        and math.dist(position, defense["anchor"]) > LEASH_BLOCKS
    ):
        return "Pursuit exceeded the 12-block leash"
    if now - defense.get("pursuit_started_at", now) >= PURSUIT_SECONDS:
        return "Pursuit exceeded 24 seconds"
    distance = target["distance"]
    if distance < defense.get("best_distance", math.inf) - 0.5:
        defense["best_distance"] = distance
        defense["progress_at"] = now
    if now - defense.get("progress_at", now) >= STALL_SECONDS:
        return "No net approach for 8 seconds"
    return None
