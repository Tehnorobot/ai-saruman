"""Combat targets come from types and recent native damage evidence, never chat claims."""

import math
import time

from saruman.peaceful import hostile_type

REACTIVE = {
    "minecraft:player",
    "minecraft:enderman",
    "minecraft:spider",
    "minecraft:piglin",
    "minecraft:zombified_piglin",
}
THREAT_SECONDS = 20
PROTECTED_TYPES = {
    "minecraft:" + name
    for name in (
        "villager",
        "wandering_trader",
        "iron_golem",
        "snow_golem",
        "cow",
        "pig",
        "sheep",
        "chicken",
        "wolf",
        "cat",
        "horse",
        "donkey",
        "mule",
        "llama",
        "trader_llama",
    )
}


def permitted(target, threats=None, now=None):
    if target.get("protected_native") or target["type"] in PROTECTED_TYPES:
        return False
    if hostile_type(target["type"]):
        return True
    if target.get("native_hostile") and target["type"] not in REACTIVE:
        return True
    if target["type"] not in REACTIVE:
        return False
    proof = (threats or {}).get(target["uuid"], {})
    return proof.get("source") == "native_damage" and proof.get("until", 0) > (
        time.time() if now is None else now
    )


def living_entities(gateway):
    mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
    living = gateway.jvm.java.lang.Class.forName("net.minecraft.class_1309")
    hostile = gateway.jvm.java.lang.Class.forName("net.minecraft.class_1588")
    animal = gateway.jvm.java.lang.Class.forName("net.minecraft.class_1429")
    registry = gateway.jvm.net.minecraft.class_7923.field_41177
    own = str(mod.getPlayer().method_5845())
    for entity in mod.getEntityTracker().getAllTrackedEntities():
        if not living.isInstance(entity) or not entity.method_5805():
            continue
        uuid = str(entity.method_5845())
        if uuid == own:
            continue
        yield (
            entity,
            {
                "uuid": uuid,
                "type": str(registry.method_10221(entity.method_5864())),
                "position": [
                    float(entity.method_23317()),
                    float(entity.method_23318()),
                    float(entity.method_23321()),
                ],
                "health": float(entity.method_6032()),
                "wet": bool(entity.method_5799()),
                "submerged": bool(entity.method_5869()),
                "native_hostile": bool(hostile.isInstance(entity)),
                "protected_native": bool(animal.isInstance(entity)),
            },
        )


def record_native_damage(gateway, game, event, now=None):
    """Only the bridge's native onDamageConfirmed callback may supply this event."""
    now = time.time() if now is None else now
    amount = event.get("amount", 0)
    if (
        not isinstance(amount, (int, float))
        or not math.isfinite(amount)
        or amount <= 0
        or event.get("victim") != gateway.entry_point.getUsername()
    ):
        return False
    mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
    player = mod.getPlayer()
    if player is None:
        return False
    name = event.get("attacker", "")
    if not name or name == "unknown":
        return False
    attacker = player.method_6065()
    if attacker is not None and str(attacker.method_5477().getString()) != name:
        return False  # A stale cached attacker is not evidence for this hit.
    if attacker is None:
        # UnionClef confirms player damage by name. Resolve that exact current
        # player object rather than accepting a name or request supplied in chat.
        attacker = gateway.entry_point.getEntity(name)
        if attacker is None:
            return False
        kind = str(
            gateway.jvm.net.minecraft.class_7923.field_41177.method_10221(
                attacker.method_5864()
            )
        )
        if kind != "minecraft:player":
            return False
    uuid = str(attacker.method_5845())
    threats = {
        k: dict(v.items())
        for k, v in game.get("combat_threats", {}).items()
        if v.get("until", 0) > now
    }
    threats[uuid] = {
        "source": "native_damage",
        "at": now,
        "until": now + THREAT_SECONDS,
    }
    game["combat_threats"] = threats
    return True


def distance_to_segment(p, start, end):
    delta = [b - a for a, b in zip(start, end)]
    length2 = sum(d * d for d in delta)
    fraction = (
        max(0, min(1, sum((x - a) * d for x, a, d in zip(p, start, delta)) / length2))
        if length2
        else 0
    )
    return math.dist(p, [a + fraction * d for a, d in zip(start, delta)])


def spell_clear(spell, origin, target, entities, threats=None):
    """Conservative clearance; unknown creatures are protected too."""
    if spell["safety"] in ("self", "movement"):
        return True
    for _, other in entities:
        if permitted(other, threats):
            continue
        p = [other["position"][0], other["position"][1] + 0.9, other["position"][2]]
        if spell["safety"] != "ray":
            # Delayed projectiles, clouds, reflection and fangs have uncertain
            # future impacts. Do not launch while any noncombatant is tracked.
            return False
        if distance_to_segment(p, origin, target) <= 2.5:
            return False
    return True
