"""Observe random activities and edit their weights without restarting the agent."""

import json
import time
from datetime import datetime
from saruman.mod_goals import GoalStore, using_mods

from saruman.autonomy_settings import (
    SETTINGS_PATH,
    catalog,
    load_weights,
    probabilities,
    revision,
    save_weights,
)

PHASES = {
    "equipment": "Предварительная проверка снаряжения; обычные дела ожидают подтверждения",
    "self_defense": "Защищаюсь от подтверждённого нападения",
    "survival": "Всплываю и выбираюсь из воды; обычные задачи приостановлены",
    "player_priority": "Общение с игроком или выполнение его запроса",
    "searching": "Расширяю обследованную территорию для отложенного дела",
    "player_unavailable": "Ожидаю свежего состояния игрока",
    "respawning": "Ожидаю возрождения и свежего состояния игрока",
    "disabled": "Самостоятельные занятия выключены в .env.saruman",
    "paused": "Самостоятельные занятия приостановлены",
    "disconnected": "Ожидаю подключения к миру Minecraft",
    "navigation_stopped": "Ожидаю подтверждения остановки навигации",
    "native_task": "Выполняю игровую задачу: движение или получение ресурсов",
    "procedure": "Продолжаю ранее начатую задачу",
    "working": "Выполняю выбранное занятие",
    "settings_error": "Ошибка чтения весов; новый случайный выбор приостановлен",
    "zero_weights": "Все веса равны нулю; случайные занятия выключены",
    "cooldown": "Занятия временно недоступны; ожидаю следующей попытки",
    "unavailable": "Для занятий с ненулевым весом выключены нужные инструменты. Проверьте Tool Controls.",
    "waiting": "Готовлюсь к следующему самостоятельному действию",
}
RESULTS = {
    "cast_requested": "Заклинание отправлено; урон ещё не подтверждён",
    "insufficient_experience": "Недостаточно опыта для посоха",
    "protected_entity_near_spell": "Рядом с заклинанием есть защищённое существо",
    "new_area": "Перешёл в новую область; повторно проверяю работу",
    "movement_unconfirmed": "Перемещение не подтверждено; выбираю другой вариант",
    "player_priority": "Отложено ради игрока",
    "stopping_background_task": "Предел фоновой задачи; ожидаю подтверждения остановки",
    "selected": "Выбрано",
    "no_work": "Подходящей работы поблизости нет",
    "no_target": "Враждебная цель не найдена",
    "blocked": "Преграда",
    "needs_move": "Подход к месту",
    "needs_materials": "Нужны материалы",
    "planted": "Посажено",
    "harvested": "Спелый урожай собран",
    "tilled": "Земля подготовлена",
    "attacked": "Удар по враждебной цели",
    "killed": "Гибель врага подтверждена",
    "error": "Ошибка",
    "tool_unavailable": "Нужный инструмент выключен или не зарегистрирован",
    "stopped": "Текущее действие остановлено; выбираю следующее занятие",
    "interaction_unconfirmed": "Результат взаимодействия не подтверждён",
    "waiting_for_game_task": "Ожидание игровой задачи",
    "waiting_for_cooldown": "Ожидание готовности действия",
    "waiting_for_growth": "Молодые посадки растут; выбираю другое занятие",
    "step_budget": "Достигнут предел 8 шагов; выбираю другое занятие",
    "time_budget": "Достигнут предел 45 с; выбираю другое занятие",
}

REASONS = {
    "no_seeds_or_mature_crops": "нет семян и доступного зрелого урожая",
    "young_crops": "посадки ещё не созрели",
    "missing_hoe": "нужна мотыга",
    "no_saplings": "нужен саженец",
    "unreachable": "не найден доступный подход",
    "protected": "действие запрещено правилами места",
    "planting_rule": "Minecraft не разрешает посадку в выбранных клетках",
    "no_water": "для новой грядки не найдена вода",
    "no_crop_site": "нет свободной подходящей грядки",
    "no_tree_site": "не найдены подходящая почва, место для кроны или подход",
    "no_safe_frontier": "рядом не осталось непроверенных безопасных шагов",
    "search_budget_exhausted": "исчерпан текущий этап поиска",
}


class AutonomyPanel:
    def __init__(self, context, settings_path=SETTINGS_PATH, *, mode=None):
        self.context, self.settings_path = context, settings_path
        # Layout is created before MineBridge connects. Telemetry must not switch
        # row schemas underneath an already constructed Gradio component.
        self.mode = mode or context()[0].get("automation_mode") or "legacy"
        self.activities = catalog()

    def settings(self):
        try:
            return load_weights(self.settings_path, self.activities), None
        except (OSError, ValueError, KeyError, TypeError) as error:
            return {a["id"]: float(a["weight"]) for a in self.activities}, str(error)

    def apply(self, *values):
        try:
            weights = save_weights(
                dict(zip((a["id"] for a in self.activities), values)),
                self.settings_path,
                self.activities,
            )
        except (OSError, ValueError, TypeError) as error:
            return f"Не сохранено: {error}"
        percentages = probabilities(self.activities, weights)
        rendered = "; ".join(
            f"{a['label']}: {a['probability']:.1f}%" for a in percentages
        )
        return (
            f"Сохранено ({revision(weights)}). {rendered}. "
            "Веса применятся к следующему выбору; текущее действие завершится."
        )

    def view(self):
        game, running = self.context()
        if self.mode == "mods":
            return self.native_view(game, running)
        state = dict(game.get("autonomy", {}).items())
        weights, settings_error = self.settings()
        selected = state.get("last_selection") or {}
        labels = {a["id"]: a.get("label", a["id"]) for a in self.activities}
        current = labels.get(state.get("activity"))
        status = PHASES.get(state.get("phase"), "Ожидаю первого шага планировщика")
        if not running:
            status = "Агент остановлен. Нажмите Start для самостоятельной жизни."
        lines = [f"**Сейчас:** {status}"]
        if state.get("runtime"):
            lines.append(f"**Планировщик:** {state['runtime']}")
        equipment = state.get("equipment_result") or {}
        if equipment:
            lines.append(
                f"**Подготовка оружия:** {equipment.get('status')} · повтор проверки через {state.get('equipment_retry_seconds', 0)} с"
            )
        if game.get("bridge_runtime"):
            lines.append(f"**Версия моста:** {game['bridge_runtime']}")
        elif running and game.get("mc_started"):
            lines.append(
                "Мост ещё не подтвердил новую защиту и возрождение: полностью перезапустите Python, затем нажмите Start."
            )
        supply = game.get("scepter_supply", {})
        if game.get("combat_runtime"):
            lines.append(f"**Магия:** {game['combat_runtime']}")
        if game.get("combat_pursuit_runtime"):
            lines.append(f"**Преследование:** {game['combat_pursuit_runtime']}")
        defense = game.get("self_defense", {})
        if defense.get("target") and (
            defense.get("active") or defense.get("status") == "watching_target"
        ):
            from saruman.combat_display import describe_combat

            lines.append(f"**Боевая цель:** {describe_combat(game)}")
        attempt = game.get("scepter_attempt", {})
        if attempt and time.time() - attempt.get("at", 0) < 30:
            lines.append(
                f"**Последняя попытка магии:** {attempt.get('status')} · {attempt.get('spell', '')}"
            )
        if defense.get("status") in (
            "watching_target",
            "water_interrupted",
            "waiting_for_task_stop",
        ):
            lines.append(
                f"**Бой:** {defense.get('status')} · {defense.get('reason', '')}"
            )
        watch = {
            key: row
            for key, row in game.get("combat_watch", {}).items()
            if row.get("until", 0) > time.time()
        }
        if watch:
            lines.append(
                "**Цели под наблюдением:** "
                + "; ".join(
                    f"{row.get('type', key)}: {row.get('reason')}"
                    for key, row in watch.items()
                )
            )
        if supply:
            label = {
                "ready": "Комплект снаряжения подтверждён",
                "sword_ready": "Меч готов; режим подготовки только меча",
                "needs_sword": "Нужно получить обычный меч",
                "equipment_preparing": "Выдача запрошена; проверяю инвентарь и опыт",
                "equipment_unconfirmed": "Выдача не подтверждена; другие дела разрешены",
                "give_pending": "Проверяю выдачу посоха",
                "craft_pending": "Изготавливаю посох; ожидаю результат",
                "needs_materials": "Для посоха нужны материалы: "
                + ", ".join(supply.get("missing", [])),
            }.get(supply.get("status"), supply.get("status"))
            lines.append(f"**Основное оружие:** {label}")
            if (
                supply.get("mode") == "command_kit"
                and supply.get("scepters")
                and not any(s.get("enough_experience") for s in supply["scepters"])
            ):
                lines.append(
                    "Для заклинаний пока не хватает опыта; проверяю выдачу запаса очков командой."
                )
        xp = game.get("scepter_experience", {})
        kit = game.get("equipment_supply", {})
        if kit:
            capabilities = kit.get("capabilities", {})
            lines.append(
                f"**Снабжение:** {kit.get('mode')} · give: {bool(capabilities.get('give'))} · experience: {bool(capabilities.get('experience'))}"
            )
        if xp and xp.get("status") != "not_required":
            lines.append(
                f"**Опыт посоха:** {xp.get('status')} · наблюдаю {xp.get('points_observed')} · запас {kit.get('xp_reserve')} очков"
            )
        priority = game.get("player_priority", {})
        if priority.get("active"):
            lines.append(
                f"**Игрок:** {priority['active']['user']} · {priority['active']['text'][:160]}"
            )
        if priority.get("queue"):
            lines.append(
                "**Ожидают ответа:** "
                + "; ".join(
                    f"{row['user']}: {row['text'][:80]}"
                    for row in priority["queue"][:5]
                )
            )
        pending = state.get("queue", [])
        if pending:
            lines.append(
                "**Очередь фоновых дел:**\n"
                + "\n".join(
                    f"{i}. {row['label']}"
                    + (
                        f" (этап до {row['search_steps']} переходов)"
                        if row.get("search_steps")
                        else ""
                    )
                    for i, row in enumerate(pending, 1)
                )
            )
        if game.get("deferred_goals"):
            lines.append(
                "**Сохранённые незавершённые цели:** "
                + "; ".join(
                    f"{row['task']['config'].get('mode')}: {row['task']['config'].get('count')} деревьев"
                    for row in game["deferred_goals"]
                )
            )
        respawn = game.get("respawn", {})
        if respawn and respawn.get("status") != "alive":
            label = {
                "dead": "Персонаж погиб; готовлю возрождение",
                "respawn_requested": "Запрос отправлен; ожидаю живого персонажа",
                "respawned": "Возрождение подтверждено",
                "paused": "Автовозрождение приостановлено",
                "blocked": "Возрождение недоступно по правилам мира",
                "failed": "Возрождение не подтверждено; требуется проверка",
                "retry_wait": "Возрождение не подтверждено; ожидаю повторной попытки",
                "unavailable": "Нет свежего состояния игрока",
                "stopping_old_task": "Отменяю действие, начатое до смерти",
            }.get(respawn.get("status"), respawn.get("status"))
            lines.append(
                f"**Возрождение:** {label} · попыток: {respawn.get('attempts', 0)}"
            )
            if respawn.get("retry_in") is not None:
                lines.append(f"Повтор возрождения через {respawn['retry_in']} с.")
        survival = game.get("survival", {})
        if survival.get("active") or survival.get("status") == "swimming":
            label = {
                "swimming": "Плыву с контролем дыхания; задача продолжается",
                "waiting_for_task_stop": "На берегу; жду остановки прежнего маршрута",
                "surfacing": "Всплываю",
                "afloat": "Держусь на поверхности; ищу берег",
                "reaching_shore": "Выбираюсь на проверенный берег",
                "unavailable": "Не удалось обновить защиту от утопления",
            }.get(survival.get("status"), survival.get("status"))
            lines.append(
                f"**Самосохранение:** {label} · воздух: {survival.get('air', '?')}/{survival.get('max_air', '?')}"
            )
        if running and (current or state.get("focus")):
            lines.append(f"**Занятие:** {state.get('focus') or current}")
        if selected:
            lines.append(
                f"**Последний выбор из очереди:** {selected['label']} · "
                f"шанс при формировании очереди {selected['probability']:.1f}% · "
                f"{RESULTS.get(selected.get('status'), selected.get('status', '—'))}"
            )
            if selected.get("reason_code") in REASONS:
                lines.append(
                    "**Причина последнего результата:** "
                    + REASONS[selected["reason_code"]]
                )
        if state.get("paused") and state.get("pause_reason"):
            lines.append(state["pause_reason"])
        issues = state.get("recent_issues", [])
        if issues:
            issue = issues[-1]
            result = issue.get("result", {})
            label = labels.get(issue.get("activity"), "Сохранённая задача")
            reason = (
                result.get("reason")
                or result.get("detail")
                or result.get("hint")
                or RESULTS.get(result.get("status"), result.get("status"))
            )
            lines.append(
                f"**Последняя проблема занятия «{label}»:** {str(reason)[:500]}"
            )
        if settings_error or state.get("settings_error"):
            lines.append(
                f"Ошибка настроек: {settings_error or state['settings_error']}"
            )
        if state and state.get("settings_revision") != revision(weights):
            lines.append(
                "Новые веса сохранены; планировщик ещё не подтвердил применение."
            )
        age = max(0, time.time() - state.get("updated_at", time.time()))
        if running and state and age > 15:
            lines.append(
                f"Последнее обновление планировщика {age:.0f} с назад; возможен ответ модели или долгий вызов."
            )
        # UI time advances even while the chat agent is busy producing a reply.
        cooldowns = {
            a["id"]: max(0, a.get("cooldown_seconds", 0) - age)
            for a in state.get("activities", [])
        }
        unavailable = {
            a["id"]: a["unavailable_reason"]
            for a in state.get("activities", [])
            if a.get("unavailable_reason")
        }
        rows = [
            [
                a["label"],
                a["weight"],
                round(a["probability"], 2),
                round(a["next_probability"], 2),
                a["cooldown_seconds"],
                a["unavailable_reason"]
                or ("Выключено весом" if not a["weight"] else "Доступно"),
            ]
            for a in probabilities(
                self.activities, weights, cooldowns, unavailable=unavailable
            )
        ]
        history = [
            [
                datetime.fromtimestamp(row["selected_at"]).strftime("%H:%M:%S"),
                row["label"],
                round(row["probability"], 2),
                RESULTS.get(row.get("status"), row.get("status", "—"))
                + (
                    " — " + REASONS[row["reason_code"]]
                    if row.get("reason_code") in REASONS
                    else ""
                ),
            ]
            for row in reversed(state.get("history", []))
        ]
        details = {
            "autonomy": state,
            "hasActiveTask": game.get("hasActiveTask", False),
            "task_chain": game.get("task_chain"),
            "navigation": dict(game.get("navigation", {}).items()),
            "survival": dict(game.get("survival", {}).items()),
            "scepter_supply": dict(game.get("scepter_supply", {}).items()),
            "scepter_attempt": dict(game.get("scepter_attempt", {}).items()),
            "combat_attack": dict(game.get("combat_attack", {}).items()),
            "combat_display": dict(game.get("combat_display", {}).items()),
            "equipment_supply": dict(game.get("equipment_supply", {}).items()),
            "scepter_combat": dict(game.get("scepter_combat", {}).items()),
            "self_defense": dict(game.get("self_defense", {}).items()),
            "combat_watch": dict(game.get("combat_watch", {}).items()),
            "combat_checkpoint": dict(game.get("combat_checkpoint", {}).items()),
            "combat_pursuit_runtime": game.get("combat_pursuit_runtime"),
            "combat_controls": dict(game.get("combat_controls", {}).items()),
            "respawn": dict(game.get("respawn", {}).items()),
            "bridge_runtime": game.get("bridge_runtime", "older bridge"),
            "player_priority": dict(game.get("player_priority", {}).items()),
            "work_searches": dict(game.get("work_searches", {}).items()),
            "work_search_exhausted": list(game.get("work_search_exhausted", [])),
            "work_search_retreat": game.get("work_search_retreat"),
            "deferred_goals": list(game.get("deferred_goals", [])),
        }
        return (
            "\n\n".join(lines),
            rows,
            history,
            json.dumps(details, ensure_ascii=False, indent=2, default=str),
        )

    def native_view(self, game, running):
        runtime = dict(game.get("mod_runtime", {}).items())
        goals = [dict(g.items()) for g in game.get("mod_goals", [])]
        weights, error = self.settings()
        ready = runtime.get("status") == "running"
        active = [g for g in goals if g["state"] not in {"completed", "failed", "cancelled", "uncertain"}]
        current = active[0] if active else None
        lines = ["**Режим: нативные моды; цикл работает независимо от LLM.**",
                 f"Агент: {'включён' if running else 'остановлен'}. Runtime: {runtime.get('status', 'подключение')}",
                 f"Владелец тела: {runtime.get('defense', {}).get('owner', 'неизвестен')}"]
        if current:
            lines.append(f"Текущая цель: {current['kind']} ({current['source']}), {current['state']}; подтверждено {current['completed']}/{current['count']}. ID: {current['id']}")
        else:
            lines.append("Очередь пуста. После 20 секунд простоя: посадка дерева или следование за доступным игроком.")
        if runtime.get("activity", {}).get("reason"):
            lines.append(runtime["activity"]["reason"])
        if runtime.get("error") or error:
            lines.append(f"Ошибка: {runtime.get('error') or error}")
        tree_control = game.get("mod_tool_controls", {}).get("grow_trees", True)
        rows = [
            ["Auto Defense", "Защита, бой, отступление и выживание", "По ситуации",
             "Включён" if runtime.get("defense_enabled") else "Выключен"],
            ["TreeGrow", "Выращивание деревьев", "Фоновая очередь" if weights["trees"] else "Только по запросу",
             "Готов" if ready and tree_control else "Выключен в Tool Controls" if not tree_control else "Ожидает готовности runtime"],
            ["UnionClef", "Следование за случайным доступным игроком", "После 20 секунд простоя",
             "Готов" if ready else "Ожидает готовности runtime"],
        ]
        history = [[datetime.fromtimestamp(g["created"]).strftime("%H:%M:%S"),
                    f"{g['source']}: {g['kind']} {g['completed']}/{g['count']}",
                    g["state"], g.get("reason") or ""] for g in sorted(goals, key=lambda g: g["created"], reverse=True)[:20]]
        return "\n\n".join(lines), rows, history, json.dumps({"native_runtime": runtime, "queue": goals, "supply": dict(game.get("mod_supply", {}).items())}, ensure_ascii=False, indent=2, default=str)

    def submit_trees(self, count):
        game, _ = self.context()
        if not using_mods(game):
            return "Доступно только в режиме mods"
        try:
            if not float(count).is_integer() or not 1 <= count <= 100:
                return "Введите целое количество от 1 до 100"
        except (TypeError, ValueError):
            return "Введите целое количество от 1 до 100"
        goal = GoalStore().submit("trees", source="operator", count=int(count))
        return f"Принято в очередь: {goal['id']}; результат пока не подтверждён"

    def cancel_goals(self):
        GoalStore().cancel()
        return "Остановка очереди запрошена; подтверждение видно в состоянии"

    def set_native_background(self, enabled):
        weights, error = self.settings()
        if error:
            return f"Не сохранено: {error}"
        weights["trees"] = (weights["trees"] or 2) if enabled else 0
        try:
            save_weights(weights, self.settings_path, self.activities)
        except (OSError, ValueError, TypeError) as error:
            return f"Не сохранено: {error}"
        return "Фоновые цели TreeGrow включены" if enabled else "Фоновые цели TreeGrow выключены; текущая цель сохраняется"


def add_native_panel(panel):
    import gradio as gr

    initial = panel.view()
    with gr.Accordion("Автоматизация через моды", open=True):
        gr.Markdown("**Auto Defense** реагирует на угрозы. **TreeGrow** выполняет цели выращивания деревьев. "
                    "LLM общается с игроками и принимает их просьбы; фоновые действия выполняют только моды.")
        summary = gr.Markdown(initial[0])
        modules = gr.Dataframe(headers=["Мод", "Назначение", "Запуск", "Состояние"], value=initial[1], interactive=False, label="Подключённые модовые навыки")
        background = gr.Checkbox(value=panel.settings()[0]["trees"] > 0, label="Самостоятельно выращивать деревья через TreeGrow")
        save = gr.Button("Применить фоновый режим TreeGrow")
        result = gr.Textbox(label="Настройки и очередь", interactive=False)
        save.click(panel.set_native_background, inputs=background, outputs=result)
        gr.Markdown("При одном фоновом навыке случайный выбор не нужен. "
                    "Защита работает по ситуации и не участвует в розыгрыше занятий. "
                    "Просьбы игроков имеют приоритет перед фоновыми целями.")
        with gr.Row():
            count = gr.Number(value=1, minimum=1, maximum=100, precision=0, label="Количество деревьев")
            start = gr.Button("Добавить цель TreeGrow")
            cancel = gr.Button("Отменить очередь")
        start.click(panel.submit_trees, inputs=count, outputs=result)
        cancel.click(panel.cancel_goals, outputs=result)
        history = gr.Dataframe(headers=["Время", "Источник / цель / прогресс", "Статус", "Результат"], value=initial[2], interactive=False, label="История модовых целей и команд игроков")
        details = gr.Textbox(value=initial[3], lines=12, interactive=False, label="Состояние модов и очередь")
        gr.Timer(1).tick(panel.view, outputs=[summary, modules, history, details], queue=False)
    return panel


def add_autonomy_panel(context, settings_path=SETTINGS_PATH, *, mode=None):
    import gradio as gr

    panel = AutonomyPanel(context, settings_path, mode=mode)
    if panel.mode == "mods":
        return add_native_panel(panel)
    _, error = panel.settings()
    with gr.Accordion("Самостоятельная жизнь Сарумана", open=True):
        gr.Markdown(
            "После **Start** Саруман сам выбирает занятия: задания вводить не требуется. "
            "Сообщения и запросы игроков приоритетнее фоновой очереди. "
            "**Stop** останавливает агента и его игровую задачу."
        )
        gr.Markdown("В режиме **mods** фоновые дела выполняют только установленные моды. "
                    "Сейчас планируется TreeGrow; Auto Defense включается по ситуации. "
                    "У остальных занятий шанс 0 до подключения соответствующего мода. "
                    "Ниже можно поставить цель напрямую, без LLM.")
        with gr.Row():
            tree_count = gr.Number(value=1, minimum=1, maximum=100, precision=0, label="Вырастить деревьев (дуб/берёза на выбор мода)")
            tree_start = gr.Button("Добавить TreeGrow в очередь")
            tree_cancel = gr.Button("Отменить очередь модовых задач")
        native_result = gr.Textbox(label="Очередь модов", interactive=False)
        tree_start.click(panel.submit_trees, inputs=tree_count, outputs=native_result)
        tree_cancel.click(panel.cancel_goals, outputs=native_result)
        initial = panel.view()
        summary = gr.Markdown(initial[0])
        chances = gr.Dataframe(
            headers=[
                "Занятие",
                "Вес",
                "Базовый шанс, %",
                "При новом розыгрыше, %",
                "Пауза, с",
                "Доступность инструментов",
            ],
            value=initial[1],
            interactive=False,
            label="Список самостоятельных занятий",
        )
        gr.Markdown(
            "Вероятность = вес / сумма доступных весов. Например, 40 / 20 / 20 / 20 дают "
            "40% / 20% / 20% / 20%, когда доступны все занятия. 0 исключает занятие; "
            "все нули выключают случайный выбор. Веса задают порядок готовых дел в очереди. "
            "Колонка нового розыгрыша показывает веса без уже сформированного порядка; "
            "ближайшее дело видно в очереди выше. "
            "В режиме mods поиск и исполнение выполняют сами моды; фоновый планировщик "
            "не строит цепочки команд и не обращается к LLM. "
            "Неизвестный результат взаимодействия требует проверки. Текущее действие не меняется при редактировании весов."
            " Выключенные в Tool Controls инструменты исключают зависимые занятия "
            "из следующего выбора; причина показана в таблице."
        )
        with gr.Row():
            inputs = [
                gr.Number(
                    value=lambda name=a["id"]: panel.settings()[0][name],
                    label=f"Вес: {a['label']}",
                    minimum=0,
                    maximum=1_000_000,
                )
                for a in panel.activities
            ]
        apply_button = gr.Button("Сохранить вероятности")
        saved = gr.Textbox(
            value=error or "", label="Сохранение весов", interactive=False
        )
        apply_button.click(
            panel.apply, inputs=inputs, outputs=saved, concurrency_limit=1
        )
        history = gr.Dataframe(
            headers=[
                "Выбрано в",
                "Занятие",
                "Шанс при формировании очереди, %",
                "Последний результат",
            ],
            value=initial[2],
            interactive=False,
            label="Последние 20 случайных выборов",
        )
        with gr.Accordion("Подробности текущего действия", open=False):
            details = gr.Textbox(
                value=initial[3],
                lines=12,
                interactive=False,
                label="Состояние планировщика и игры",
            )
        timer = gr.Timer(1)
        # Never refresh the editable inputs: unsaved browser edits must survive ticks.
        timer.tick(
            panel.view, outputs=[summary, chances, history, details], queue=False
        )
    return panel
