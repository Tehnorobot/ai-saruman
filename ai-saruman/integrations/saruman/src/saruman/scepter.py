"""Magical Scepter 1.2: inspect real components, acquire once, cast through native input."""

import math
import time
from contextlib import ExitStack

from saruman.combat_policy import living_entities, permitted, spell_clear
from saruman.fertilize import select_inventory_item
from saruman.minecraft import _java_to_python
from saruman.trees import _gateway
from saruman.world_actions import on_client, sync_selected_slot

ITEM = "magicalscepter:magical_scepter"
BLANK = "magicalscepter:scepter"
MATERIALS = (BLANK, "minecraft:lapis_lazuli", "minecraft:brown_mushroom")
# Attack/protect spell ids are read from each actual stack, not invented item ids.
SPELLS = {}
for name, reach, cooldown, safety in (
    ("magical_attack", 8, 30, "ray"),
    ("magical_resistance", 0, 30, "self"),
    ("blaze_fire_charges", 24, 40, "projectile"),
    ("blaze_fire_resistance", 0, 40, "self"),
    ("breeze_wind_charge", 24, 24, "projectile"),
    ("breeze_jump", 0, 24, "movement"),
    ("dragon_fireball", 24, 80, "projectile"),
    ("dragon_growl", 8, 80, "area"),
    ("evoker_fang_line", 20, 40, "area"),
    ("evoker_fang_circle", 3, 40, "area"),
    ("ghast_fireball", 24, 40, "projectile"),
    ("ghast_regeneration", 0, 40, "self"),
    ("guardian_beam", 15, 30, "ray"),
    ("guardian_haste", 0, 30, "self"),
    ("shulker_bullet", 24, 30, "projectile"),
    ("shulker_teleport", 0, 30, "movement"),
    ("warden_sonic_boom", 20, 60, "ray"),
    ("warden_stability", 0, 40, "self"),
    ("wither_skull", 24, 40, "projectile"),
    ("wither_repulsion", 0, 40, "area"),
):
    SPELLS["magicalscepter:" + name] = {
        "range": reach,
        "cooldown_ticks": cooldown,
        "safety": safety,
    }


def _holder_id(optional):
    if optional.isEmpty():
        return None
    key = optional.get().method_40230()
    return str(key.get().method_29177()) if key.isPresent() else None


def describe(gateway, inventory):
    if not inventory.get("ok"):
        raise RuntimeError("Cannot verify scepter inventory")
    mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
    player = mod.getPlayer()
    component_type = gateway.jvm.io.github.pistonpoek.magicalscepter.component.ScepterContentsComponent
    rows = []
    for slot in inventory.get("slots", []):
        if (
            slot.get("item") != ITEM
            or slot.get("count", 0) <= 0
            or not 0 <= slot.get("index", -1) < 36
        ):
            continue
        stack = player.method_31548().method_5438(slot["index"])
        component = component_type.get(stack).orElse(component_type.DEFAULT)
        rows.append(
            {
                "index": slot["index"],
                "item": ITEM,
                "attack": _holder_id(component.getAttackSpell()),
                "protect": _holder_id(component.getProtectSpell()),
                "experience_cost": int(component.getExperienceCost()),
                "enough_experience": bool(player.method_56992())
                or bool(component.hasEnoughExperience(player)),
                "cooldown": bool(player.method_7357().method_7904(stack)),
                "damage": int(stack.method_7919()),
                "max_damage": int(stack.method_7936()),
            }
        )
    return rows


def available(gateway):
    jvm = gateway.jvm
    if not jvm.net.fabricmc.loader.api.FabricLoader.getInstance().isModLoaded(
        "magicalscepter"
    ):
        return False
    if not gateway.entry_point.inGame():
        return False
    # A local Fabric mod alone says nothing about server support. Fabric advertises
    # supported play payloads on each connection (also in an integrated world).
    return bool(
        jvm.net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking.canSend(
            jvm.io.github.pistonpoek.magicalscepter.network.packet.AttackItemPayload.ID
        )
    )


def sword_fallback(game, inventory, now):
    if not inventory.get("ok"):
        return {"status": "inventory_unavailable", "magic_available": False}
    swords = [
        s
        for s in inventory.get("slots", [])
        if s.get("count", 0) > 0
        and 0 <= s.get("index", -1) < 36
        and s.get("item", "").startswith("minecraft:")
        and s.get("item", "").endswith("_sword")
    ]
    result = {
        "status": "sword_ready" if swords else "needs_sword",
        "magic_available": False,
        "reason": "Server does not support Magical Scepter; spells unavailable",
        "verified_at": now,
    }
    game["scepter_supply"] = result
    if not swords:
        result = {
            **result,
            "next_action": {
                "tool": "obtain_item",
                "arguments": {"item": "wooden_sword", "count": 1},
            },
        }
    return result


def status(game):
    gateway = _gateway()
    try:
        if not gateway.entry_point.inGame():
            return {"status": "disconnected"}
        if not available(gateway):
            return {
                "status": "mod_unavailable",
                "magic_available": False,
                "fallback": "sword",
                "equipment_supply": dict(game.get("equipment_supply", {}).items()),
            }
        rows = describe(
            gateway, _java_to_python(gateway.entry_point.getInventoryFull())
        )
        return {
            "status": "observed",
            "scepters": rows,
            "experience_supply": dict(game.get("scepter_experience", {}).items()),
            "last_attempt": dict(game.get("scepter_attempt", {}).items()),
            "equipment_supply": dict(game.get("equipment_supply", {}).items()),
            "supply": dict(game.get("scepter_supply", {}).items()),
            "spell_catalog": SPELLS,
            "combat": dict(game.get("scepter_combat", {}).items()),
        }
    finally:
        gateway.close()


def _craft(gateway, target_count=1):
    """Actual custom shapeless recipe in the ordinary 2x2 inventory grid."""
    jvm = gateway.jvm
    slots = gateway.new_array(jvm.adris.altoclef.util.ItemTarget, 4)
    registry = jvm.net.minecraft.class_7923.field_41178

    def item(identifier):
        holder = registry.method_10223(
            jvm.net.minecraft.class_2960.method_60654(identifier)
        )
        if holder.isEmpty():
            raise ValueError("Recipe item is not registered: " + identifier)
        return holder.get().comp_349()

    for index, identifier in enumerate(MATERIALS):
        slots[index] = jvm.adris.altoclef.util.ItemTarget(item(identifier), 1)
    slots[3] = jvm.adris.altoclef.util.ItemTarget.EMPTY
    recipe = jvm.adris.altoclef.util.CraftingRecipe.newShapedRecipe(slots, 1)
    target = jvm.adris.altoclef.util.RecipeTarget(item(ITEM), target_count, recipe)
    task = jvm.adris.altoclef.tasks.CraftGenericManuallyTask(target)
    on_client(
        gateway,
        jvm.adris.altoclef.AltoClef.getInstance(),
        "adris.altoclef.AltoClef",
        "runUserTask",
        ("adris.altoclef.tasksystem.Task",),
        (task,),
    )
    return int(jvm.java.lang.System.identityHashCode(task))


def ensure(game, now=None, *, allow_crafting=True):
    """Prepare the permitted kit before work; no-command policy needs only a sword.

    allow_crafting is retained for callers from earlier runtimes. Automatic staff
    crafting/material collection is replaced by the operator's sword-only fallback.
    """
    from saruman.equipment_supply import prepare

    now = time.time() if now is None else now
    if game.get("survival", {}).get("active") or game.get("alive") is False:
        return {"status": "waiting_for_survival", "preflight_required": False}
    if game.get("self_defense", {}).get("active"):
        return {"status": "waiting_for_self_defense", "preflight_required": False}
    gateway = _gateway()
    try:
        entry = gateway.entry_point
        if not entry.inGame():
            return {"status": "disconnected", "preflight_required": False}
        player = gateway.jvm.adris.altoclef.AltoClef.getInstance().getPlayer()
        if player is None or float(player.method_6032()) <= 0:
            return {"status": "waiting_for_survival", "preflight_required": False}
        inventory = _java_to_python(entry.getInventoryFull())
        magic = available(gateway)
        rows = describe(gateway, inventory) if magic and inventory.get("ok") else []
        usable = [
            row for row in rows if row["attack"] in SPELLS or row["protect"] in SPELLS
        ]
        return prepare(gateway, game, inventory, usable, magic, now)
    finally:
        gateway.close()


def cast(game, action="attack", uuid=None, slot=None):
    result = _cast(game, action, uuid, slot)
    game["scepter_attempt"] = {
        **result,
        "action": action,
        "target_uuid": uuid,
        "at": time.time(),
    }
    return result


def _cast(game, action="attack", uuid=None, slot=None):
    if game.get("scepter_enabled") is False:
        return {"status": "tool_unavailable", "tool": "cast_scepter"}
    if action not in ("attack", "protect"):
        raise ValueError("action must be attack or protect")
    if action == "attack" and not uuid:
        raise ValueError("An observed permitted target UUID is required")
    if game.get("alive") is False or game.get("survival", {}).get("active"):
        return {"status": "waiting_for_survival"}
    with ExitStack() as cleanup:
        gateway = _gateway()
        cleanup.callback(gateway.close)
        entry, jvm = gateway.entry_point, gateway.jvm
        if not available(gateway):
            return {"status": "mod_unavailable"}
        pending = game.get("scepter_combat", {})
        if pending.get("unknown"):
            return {
                "status": "interaction_unconfirmed",
                "reason": "Previous cast outcome is unknown; inspect before another cast",
            }
        if pending.get("next_at", 0) > time.time():
            return {"status": "waiting_for_cooldown"}
        if not entry.inGame():
            return {"status": "disconnected"}
        if entry.hasActiveTask():
            return {"status": "waiting_for_game_task"}
        player = jvm.adris.altoclef.AltoClef.getInstance().getPlayer()
        if float(player.method_6032()) <= 0:
            return {"status": "waiting_for_respawn"}
        inventory = _java_to_python(entry.getInventoryFull())
        rows = describe(gateway, inventory)
        entities = list(living_entities(gateway))
        threats = game.get("combat_threats", {})
        match = next(
            ((entity, row) for entity, row in entities if row["uuid"] == uuid), None
        )
        if action == "attack" and (match is None or not permitted(match[1], threats)):
            return {"status": "target_forbidden_or_lost", "killed": False}
        origin = [
            float(player.method_23317()),
            float(player.method_23320()),
            float(player.method_23321()),
        ]
        target = (
            [
                match[1]["position"][0],
                float(match[0].method_23320()),
                match[1]["position"][2],
            ]
            if match
            else origin
        )
        candidates = []
        reasons = set()
        for row in rows:
            if slot is not None and row["index"] != slot:
                continue
            spell = SPELLS.get(row[action])
            if spell is None:
                reasons.add("unknown_spell")
                continue
            if row["cooldown"]:
                reasons.add("waiting_for_cooldown")
                continue
            if not row["enough_experience"]:
                reasons.add("insufficient_experience")
                continue
            if action == "attack" and (
                math.dist(origin, target) > spell["range"]
                or not player.method_6057(match[0])
            ):
                reasons.add("out_of_reach")
                continue
            if not spell_clear(spell, origin, target, entities, threats):
                reasons.add("protected_entity_near_spell")
                continue
            candidates.append(row)
        if not candidates:
            return {
                "status": next(
                    (
                        s
                        for s in (
                            "protected_entity_near_spell",
                            "insufficient_experience",
                            "waiting_for_cooldown",
                            "out_of_reach",
                            "unknown_spell",
                        )
                        if s in reasons
                    ),
                    "missing_scepter",
                ),
            }
        chosen = min(
            candidates,
            key=lambda s: (
                SPELLS[s[action]]["safety"] not in ("ray", "self"),
                s["index"],
            ),
        )
        if not entry.closeOpenScreen():
            return {"status": "screen_open"}
        select_inventory_item(entry, cleanup, inventory, ITEM, index=chosen["index"])
        equipped = _java_to_python(entry.getInventoryFull())
        selected = next(
            (
                s
                for s in describe(gateway, equipped)
                if s["index"] == equipped.get("selectedHotbar")
            ),
            None,
        )
        if (
            selected is None
            or selected[action] != chosen[action]
            or selected["cooldown"]
            or not selected["enough_experience"]
        ):
            return {"status": "equipment_changed"}
        if action == "attack":
            if not entry.lookAt(*target):
                return {"status": "aim_unconfirmed"}
            dx, dy, dz = [b - a for a, b in zip(origin, target)]
            yaw = math.degrees(math.atan2(-dx, dz))
            pitch = -math.degrees(math.atan2(dy, math.hypot(dx, dz)))
            if (
                abs((float(entry.getYaw()) - yaw + 180) % 360 - 180) > 3
                or abs(float(entry.getPitch()) - pitch) > 3
            ):
                return {"status": "aim_unconfirmed"}
            # Recheck all entities after selection/aiming, before the only cast.
            entities = list(living_entities(gateway))
            if not any(
                row["uuid"] == uuid and permitted(row, threats) for _, row in entities
            ):
                return {"status": "target_forbidden_or_lost"}
            if not spell_clear(
                SPELLS[chosen[action]], origin, target, entities, threats
            ):
                return {"status": "protected_entity_near_spell"}
        elif not spell_clear(
            SPELLS[chosen[action]],
            origin,
            origin,
            list(living_entities(gateway)),
            threats,
        ):
            return {"status": "protected_entity_near_spell"}
        spell_id = chosen[action]
        health_before = float(match[0].method_6032()) if match else None
        if action == "attack":
            # The mod callback bypasses vanilla syncSelectedSlot. Ensure its
            # server packet sees the verified staff, rather than the old item.
            sync_selected_slot(gateway, equipped["selectedHotbar"])
        game["scepter_combat"] = {
            "spell": spell_id,
            "action": action,
            "target": uuid,
            "next_at": time.time() + SPELLS[spell_id]["cooldown_ticks"] / 20,
            "unknown": True,
        }
        if action == "attack":
            callback = (
                jvm.io.github.pistonpoek.magicalscepter.attack.ItemAttackCallback()
            )
            accepted = bool(
                on_client(
                    gateway,
                    callback,
                    "io.github.pistonpoek.magicalscepter.attack.ItemAttackCallback",
                    "onClientPlayerPreAttack",
                    ("net.minecraft.class_310", "net.minecraft.class_746", "int"),
                    (jvm.net.minecraft.class_310.method_1551(), player, 1),
                )
            )
        else:
            result = _java_to_python(entry.useHeldItem())
            accepted = (
                bool(result.get("ok"))
                and "PASS" not in str(result.get("result", ""))
                and "FAIL" not in str(result.get("result", ""))
            )
        game["scepter_combat"] = {
            **dict(game["scepter_combat"].items()),
            "unknown": False,
            "accepted": accepted,
        }
        health_after = None
        if accepted and match:
            time.sleep(0.3)  # The base ray has a native four-tick delay.
            health_after = float(match[0].method_6032())
        killed = health_after is not None and health_after <= 0
        return {
            "status": "killed"
            if killed
            else "cast_requested"
            if accepted
            else "cast_rejected",
            "spell": spell_id,
            "action": action,
            "target": match[1] if match else None,
            "health_before": health_before,
            "health_after": health_after,
            "damage_confirmed": health_after is not None
            and health_after < health_before,
            "killed": killed,
            "equipment_slot": equipped["selectedHotbar"],
        }
