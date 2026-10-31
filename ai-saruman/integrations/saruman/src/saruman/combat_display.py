"""Local action-bar combat telemetry; never chat, damage or world effects."""

import time

from py4j.protocol import Py4JError

from saruman.world_actions import on_client

LABELS = {
    "cast_requested": "заклинание отправлено",
    "cast_rejected": "заклинание отклонено",
    "attacked": "удар",
    "killed": "цель погибла",
    "waiting_for_cooldown": "перезарядка",
    "approaching_threat": "сближение",
    "stopping_for_defense": "прерываю работу для боя",
    "watching_target": "подход отложен",
    "aim_unconfirmed": "наведение не подтверждено",
    "out_of_reach": "вне дальности или нет видимости",
    "insufficient_experience": "недостаточно опыта",
    "missing_scepter": "рабочий посох не найден",
    "protected_entity_near_spell": "рядом мирное существо",
    "retrying": "ошибка управления боем",
}


def describe_combat(game):
    defense = game.get("self_defense", {})
    target = defense.get("target", {})
    if not target or not (
        defense.get("active") or defense.get("status") == "watching_target"
    ):
        return ""
    result = defense.get("result", {})
    status = result.get("status", defense.get("status", ""))
    name = str(target.get("type", "mob")).split(":")[-1]
    distance = target.get("distance", 0)
    text = f"Цель: {name} [{str(target.get('uuid', ''))[:8]}] · {distance:.1f} м · {LABELS.get(status, status)}"
    if result.get("damage_confirmed"):
        text += f" · урон {result['health_before'] - result['health_after']:.1f}"
    elif status in ("cast_requested", "attacked"):
        text += " · попадание не подтверждено"
    return text


class CombatDisplay:
    def __init__(self, clock=time.monotonic):
        self.clock, self.next_at, self.shown = clock, 0, False

    def tick(self, gateway, game):
        now = self.clock()
        if now < self.next_at:
            return
        self.next_at = now + 1
        text = describe_combat(game)
        if not text and not self.shown:
            return
        try:
            jvm = gateway.jvm
            player = jvm.adris.altoclef.AltoClef.getInstance().getPlayer()
            if player is None:
                return
            on_client(
                gateway,
                player,
                "net.minecraft.class_1657",
                "method_7353",
                ("net.minecraft.class_2561", "boolean"),
                (jvm.net.minecraft.class_2561.method_43470(text), True),
            )
            self.shown = bool(text)
            game["combat_display"] = {
                "text": text,
                "status": "shown" if text else "cleared",
            }
        except (Py4JError, OSError, ValueError, TypeError, RuntimeError) as error:
            game["combat_display"] = {
                "text": text,
                "status": "unavailable",
                "error": type(error).__name__,
            }
