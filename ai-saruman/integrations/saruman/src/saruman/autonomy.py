"""Idle work runs bounded registered tools, independent of chat/LLM idle timers."""

import math
import os
import random
import time
from copy import deepcopy

from saruman.action_speech import ActionSpeech
from saruman.autonomy_settings import (
    SETTINGS_PATH,
    catalog,
    load_weights,
    probabilities,
    revision,
)
from saruman.farming import FOODS
from saruman.terrain_search import search_max_moves

UNCERTAIN_RESULTS = frozenset(
    {
        "interaction_unconfirmed",
        "interaction_not_confirmed",
        "placement_not_confirmed",
        "error",
    }
)


class Autonomy:
    EQUIPMENT_SECONDS = 20
    EQUIPMENT_RETRY_SECONDS = 120

    def __init__(self, clock=time.monotonic, rng=None, settings_path=SETTINGS_PATH):
        self.clock, self.rng = clock, rng or random.Random()
        self.enabled = os.getenv("SARUMAN_AUTONOMY_ENABLED", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.activities = catalog()
        self.settings_path = settings_path
        self.weights = {a["id"]: float(a["weight"]) for a in self.activities}
        self.settings_error = None
        self.last_selection, self.last_result, self.history = None, None, []
        self.pause_reason = None
        self.focus = None
        self.due, self.job, self.paused = clock() + 8, None, False
        self.cooldowns = {}
        self.unavailable = {}
        self.available_tools = None
        self.pending = []
        self.search_limits = {}
        self.search_max_moves = search_max_moves()
        self.player_until = 0
        self.player_focus = None
        self.equipment_due = 0
        self.equipment_job = None
        self.equipment_result = None
        self.recent_issues = []
        self.speech = ActionSpeech(clock=clock)
        self._refresh_weights()

    def _refresh_weights(self):
        try:
            weights = load_weights(self.settings_path, self.activities)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.settings_error = str(error)
        else:
            if weights != self.weights:
                self.due = min(self.due, self.clock() + 1.5)
                self.pending = []
            self.weights, self.settings_error = weights, None

    def snapshot(self, game, *, procedure_pending=False):
        now = self.clock()
        if not self.enabled:
            phase = "disabled"
        elif self.paused:
            phase = "paused"
        elif game.get("survival", {}).get("active"):
            phase = "survival"
        elif game.get("self_defense", {}).get("active"):
            phase = "self_defense"
        elif self.clock() < self.player_until:
            phase = "player_priority"
        elif game.get("alive") is False:
            phase = "respawning"
        elif not game.get("ingame"):
            phase = "disconnected"
        elif "alive" in game and game.get("alive") is not True:
            phase = "player_unavailable"
        elif game.get("navigation", {}).get("status") in (
            "stopping",
            "stop_failed",
            "stop_requested",
        ):
            phase = "navigation_stopped"
        elif (self.equipment_result or {}).get("preflight_required"):
            phase = "equipment"
        elif game.get("hasActiveTask"):
            phase = "equipment" if self.equipment_job else "native_task"
        elif procedure_pending:
            phase = "procedure"
        elif self.job:
            phase = "searching" if self.job.get("kind") == "search" else "working"
        elif self.settings_error:
            phase = "settings_error"
        elif not any(self.weights.values()):
            phase = "zero_weights"
        elif not any(
            self.weights[a["id"]] > 0 and a["id"] not in self.unavailable
            for a in self.activities
        ):
            phase = "unavailable"
        elif not any(
            a["next_probability"]
            for a in probabilities(
                self.activities, self.weights, self.cooldowns, now, self.unavailable
            )
        ):
            phase = "cooldown"
        else:
            phase = "waiting"
        return {
            "runtime": "command-equipment-preflight-v1",
            "equipment_job": deepcopy(self.equipment_job),
            "equipment_result": deepcopy(self.equipment_result),
            "equipment_retry_seconds": max(0, round(self.equipment_due - now)),
            "commentary": self.speech.snapshot(),
            "enabled": self.enabled,
            "paused": self.paused,
            "phase": phase,
            "pause_reason": self.pause_reason,
            "activity": self.job["id"]
            if self.job
            else "equipment"
            if self.equipment_job
            else None,
            "steps": self.job["steps"] if self.job else 0,
            "focus": self.focus,
            "last_selection": dict(self.last_selection)
            if self.last_selection
            else None,
            "result": self.last_result,
            "recent_issues": deepcopy(self.recent_issues),
            "history": [dict(row) for row in self.history],
            "queue": [
                {
                    "id": row["id"],
                    "label": row["label"],
                    "kind": row.get("kind", "activity"),
                    "search_steps": row.get("arguments", {}).get("search_steps"),
                }
                for row in self.pending
            ],
            "player_priority": self.player_focus
            if self.clock() < self.player_until
            else None,
            "search_limits": dict(self.search_limits),
            "search_max_moves": self.search_max_moves,
            "activities": probabilities(
                self.activities, self.weights, self.cooldowns, now, self.unavailable
            ),
            "settings_revision": revision(self.weights),
            "settings_error": self.settings_error,
            "next_step_seconds": max(0, round(self.due - now, 1)),
            "updated_at": time.time(),
        }

    def directive(self, text):
        self.job = None
        self.paused = any(
            word in text.lower()
            for word in (
                "не двиг",
                "не ход",
                "без перемещ",
                "ничего не дел",
                "стой",
                "остановись",
                "жди",
                "stop",
            )
        )
        self.due = self.clock() + 8
        self.pause_reason = "Ожидание по просьбе оператора" if self.paused else None
        if self.paused:
            self.equipment_job = None
            self.equipment_due = self.clock() + self.EQUIPMENT_RETRY_SECONDS
        self.focus = None

    def pause(self):
        self.paused, self.job = True, None
        self.pause_reason = "Остановлено оператором"
        self.focus = None

    def yield_to_player(self, user, text):
        """Suspend a hobby, keeping its intent in the queue for after the conversation."""
        owned = self.job is not None or self.equipment_job is not None
        if self.equipment_job:
            self.equipment_job = None
            self.equipment_due = self.clock() + self.EQUIPMENT_RETRY_SECONDS
        if self.job:
            resumed = dict(self.job)
            resumed.pop("awaiting_move", None)
            resumed.pop("move_origin", None)
            resumed.pop("cancel_requested", None)
            self.pending.insert(0, resumed)
            if self.last_selection:
                self.last_selection["status"] = "player_priority"
        self.job = None
        self.focus = None
        self.player_focus = {"user": user, "text": text}
        self.player_until = self.clock() + 30
        return owned

    def release_player(self):
        # Leave a brief conversation window. Active player tasks/procedures also
        # block hobbies through hasActiveTask/procedure_pending, without a timeout.
        self.player_until = self.clock() + 8
        self.due = min(self.due, self.player_until)

    def yield_to_combat(self):
        self.yield_to_player("self-defense", "Защита от нападения")
        self.player_focus = None
        self.player_until = self.clock() + 2

    def _can_search(self):
        return (
            self.available_tools is not None
            and {"explore_nearby", "agent_action"} <= self.available_tools
        )

    def _schedule_search(self, job):
        if not self._can_search() or not self.weights.get(job["id"], 0):
            return
        limit = self.search_limits.setdefault(job["id"], 4)
        if job.get("kind") == "search":
            if (
                job.get("reason_code") != "search_budget_exhausted"
                or limit >= self.search_max_moves
            ):
                return
            limit = min(limit * 2, self.search_max_moves)
            self.search_limits[job["id"]] = limit
        if any(
            row["id"] == job["id"] and row.get("kind") == "search"
            for row in self.pending
        ):
            return
        label = next(a["label"] for a in self.activities if a["id"] == job["id"])
        self.pending.append(
            {
                "id": job["id"],
                "kind": "search",
                "label": "Поиск места: " + label,
                "tool": "explore_nearby",
                "required_tools": ["agent_action"],
                "arguments": {"purpose": job["id"], "search_steps": limit},
            }
        )

    def _queued_choice(self, choices, now):
        ready = {a["id"]: a for a in choices}
        self.pending = [
            row
            for row in self.pending
            if self.weights.get(row["id"], 0) > 0
            and row["id"] not in self.unavailable
            and (
                row.get("kind") == "search" and self._can_search() or row["id"] in ready
            )
        ]
        represented = {row["id"] for row in self.pending}
        remaining = [a for a in choices if a["id"] not in represented]
        while remaining:
            total = sum(self.weights[a["id"]] for a in remaining)
            selected = self.rng.choices(
                remaining, weights=[self.weights[a["id"]] for a in remaining], k=1
            )[0]
            self.pending.append(
                {**selected, "probability": 100 * self.weights[selected["id"]] / total}
            )
            remaining = [a for a in remaining if a["id"] != selected["id"]]
        return self.pending.pop(0) if self.pending else None

    def end_activity(self, reason):
        """Cancel one hobby; keep an explicit operator pause until Start/resume."""
        if self.job:
            self.cooldowns[self.job["id"]] = self.clock() + 60
            self.last_selection["status"] = "stopped"
        self.job, self.focus = None, None
        if self.equipment_job:
            self.equipment_job = None
            self.equipment_due = self.clock() + self.EQUIPMENT_RETRY_SECONDS
        self.last_result = {"status": "stopped", "reason": reason}
        self.due = self.clock() + 2

    def _record_issue(self, activity, tool, arguments, result):
        self.recent_issues = (
            self.recent_issues
            + [
                {
                    "activity": activity,
                    "tool": tool,
                    "arguments": deepcopy(arguments),
                    "result": deepcopy(result),
                    "at": time.time(),
                }
            ]
        )[-20:]

    def _block_saved_goal(self, game, saved, issue):
        """Retire this continuation without discarding its checkpoint or stopping life."""
        current = game.get("tree_task", {})
        if current.get("id") == saved.get("id") and current.get("status") == "active":
            checkpoint = deepcopy(dict(current.items()))
            checkpoint.update(
                status="blocked",
                pause_reason="Action error; verify before resuming",
                reason="Continuation not confirmed: "
                + str(
                    issue.get("reason") or issue.get("detail") or issue.get("status")
                ),
            )
            game["tree_task"] = checkpoint
            deferred = deepcopy(list(game.get("deferred_goals", [])))
            for row in deferred:
                if row.get("task", {}).get("id") == saved.get("id"):
                    row["task"] = deepcopy(checkpoint)
            game["deferred_goals"] = deferred
        self._record_issue(
            "saved_goal", "manage_trees", dict(saved.get("config", {}).items()), issue
        )
        self.focus = None
        self.due = self.clock() + 2.5

    def wait_time(self):
        return (
            max(0.5, min(8, self.due - self.clock()))
            if self.enabled and not self.paused
            else 15
        )

    def tick(self, game, execute, *, procedure_pending=False, available_tools=None):
        self._refresh_weights()
        self.available_tools = available_tools
        if (
            available_tools is not None
            and "ensure_scepter" not in available_tools
            and (self.equipment_result or {}).get("preflight_required")
        ):
            self.equipment_result = {
                "status": "tool_unavailable",
                "tool": "ensure_scepter",
            }
            self.focus = None
        unavailable = {}
        if available_tools is not None:
            for activity in self.activities:
                missing = [
                    name
                    for name in (activity["tool"], *activity.get("required_tools", []))
                    if name not in available_tools
                ]
                if missing:
                    unavailable[activity["id"]] = (
                        "Выключены или не зарегистрированы: " + ", ".join(missing)
                    )
        if unavailable != self.unavailable:
            self.due = min(self.due, self.clock() + 1.5)
        self.unavailable = unavailable
        outcome = self._tick(game, execute, procedure_pending=procedure_pending)
        if outcome is not None:
            self.last_result = outcome["result"]
        return outcome

    def _equipment_tick(self, game, execute, now):
        job = self.equipment_job
        if not job:
            return None
        if not game.get("hasActiveTask"):
            if job.get("cancel_requested") or now - job["started"] >= 2.5:
                self.equipment_job, self.focus = None, None
                self.equipment_due = now + self.EQUIPMENT_RETRY_SECONDS
                return None
            return {
                "calls": [],
                "result": {"status": "equipment_starting"},
                "speech": None,
            }
        root = game.get("native_user_task") or {}
        fresh = time.time() - game.get("native_user_task_seen_at", 0) <= 3
        expected_item = job["action"].get("arguments", {}).get("item")
        collection = game.get("collection", {})
        if (
            job.get("root_id") is None
            and fresh
            and root.get("id") is not None
            and expected_item
            and collection.get("goal", {}).get("item") == expected_item
            and collection.get("status") != "ownership_lost"
        ):
            job["root_id"] = root["id"]
        if (
            fresh
            and job.get("root_id") is not None
            and root.get("id") is not None
            and root["id"] != job["root_id"]
        ):
            self.equipment_result = {
                "status": "equipment_replaced",
                "reason": "Another native root owns the character",
            }
            self.equipment_job, self.focus = None, None
            self.equipment_due = now + self.EQUIPMENT_RETRY_SECONDS
            return None
        if job.get("cancel_requested") or now - job["started"] < self.EQUIPMENT_SECONDS:
            return None
        if not fresh or job.get("root_id") is None or root.get("id") != job["root_id"]:
            self.equipment_result = {
                "status": "equipment_ownership_unconfirmed",
                "reason": "Cannot safely cancel an unobserved or replaced native task",
            }
            self.equipment_job = None
            self.equipment_due = now + self.EQUIPMENT_RETRY_SECONDS
            return None
        if (
            self.available_tools is not None
            and "agent_action" not in self.available_tools
        ):
            self.equipment_result = {
                "status": "tool_unavailable",
                "tool": "agent_action",
            }
            return None
        job["cancel_requested"] = True
        self.equipment_due = now + self.EQUIPMENT_RETRY_SECONDS
        self.focus = "Останавливаю подготовку оружия; затем продолжу другие занятия"
        result = execute("agent_action", {"action": "@stop"})
        self.equipment_result = {"status": "stopping_equipment", "detail": result}
        return {
            "calls": [("agent_action", {"action": "@stop"}, result)],
            "result": self.equipment_result,
            "speech": None,
        }

    def _tick(self, game, execute, *, procedure_pending=False):
        now = self.clock()
        if game.get("survival", {}).get("active") or game.get("self_defense", {}).get(
            "active"
        ):
            return None
        if now < self.player_until:
            return None
        if "alive" in game and game.get("alive") is not True:
            if self.job or self.equipment_job:
                self.end_activity(
                    "Player died or is unavailable; inspect the world after respawn"
                )
            return None
        if not self.enabled or self.paused or now < self.due:
            return None
        self.due = now + 1.5
        if not game.get("ingame") or procedure_pending:
            return None
        equipment = self._equipment_tick(game, execute, now)
        if equipment is not None:
            return equipment
        if self.equipment_job and game.get("hasActiveTask"):
            return None
        if game.get("hasActiveTask"):
            if (
                self.job
                and now - self.job["started"] >= 45
                and not self.job.get("cancel_requested")
            ):
                self.job["cancel_requested"] = True
                allowed = (
                    self.available_tools is None
                    or "agent_action" in self.available_tools
                )
                stopped = (
                    execute("agent_action", {"action": "@stop"})
                    if allowed
                    else {"status": "tool_unavailable"}
                )
                self.last_selection["status"] = "stopping_background_task"
                self.due = now + 2.5
                return {
                    "calls": [("agent_action", {"action": "@stop"}, stopped)]
                    if allowed
                    else [],
                    "result": {"status": "stopping_background_task", "detail": stopped},
                    "speech": None,
                }
            return None
        navigation = game.get("navigation", {})
        if navigation.get("status") in ("stopping", "stop_failed", "stop_requested"):
            return None
        calls = []

        def call(name, arguments):
            if self.available_tools is not None and name not in self.available_tools:
                result = {
                    "status": "tool_unavailable",
                    "tool": name,
                    "detail": f"Инструмент {name} выключен или не зарегистрирован",
                }
            else:
                result = execute(name, arguments)
            calls.append((name, arguments, result))
            if isinstance(result, dict):
                return result
            queued = result is True or (
                isinstance(result, str) and result.startswith("Queued obtaining/")
            )
            return {"status": "queued" if queued else "error", "detail": str(result)}

        if (
            self.available_tools is not None
            and "ensure_scepter" in self.available_tools
            and now >= self.equipment_due
        ):
            self.focus = "Предварительная проверка оружия и запаса опыта"
            result = call("ensure_scepter", {"allow_crafting": False})
            self.equipment_result = result
            self.equipment_due = now + (
                2
                if result.get("status")
                in ("give_pending", "xp_pending", "equipment_preparing")
                else 30
            )
            if result.get("preflight_required"):
                self.equipment_due = self.due = now + 1
                return {"calls": calls, "result": result, "speech": None}
            cancellable = "agent_action" in self.available_tools
            material = result.get("next_action", {})
            if (
                cancellable
                and material.get("tool") == "obtain_item"
                and "obtain_item" in self.available_tools
                and material.get("arguments", {}).get("item") == "wooden_sword"
            ):
                queued = call("obtain_item", material["arguments"])
                self.equipment_due = now + self.EQUIPMENT_RETRY_SECONDS
                if queued.get("status") not in ("error", "tool_unavailable"):
                    self.equipment_job = {
                        "started": now,
                        "action": material,
                        "root_id": None,
                    }
                    self.due = now + 2.5
                    return {"calls": calls, "result": result, "speech": None}
                self.equipment_result = queued
            self.focus = None

        # A saved, still actionable operator goal has priority over random hobbies.
        saved = game.get("tree_task", {})
        if saved.get("status") == "active":
            self.focus = "Продолжение сохранённой задачи с деревьями"
            arguments = dict(saved["config"].items())
            arguments["new_task"] = False
            result = call("manage_trees", arguments)
            action = result.get("next_action") or {}
            if result.get("status") in ("needs_move", "needs_materials") and action.get(
                "tool"
            ) in ("agent_action", "obtain_item"):
                queued = call(action["tool"], action["arguments"])
                self.due = now + 2.5
                if queued.get("status") in ("error", "tool_unavailable"):
                    self._block_saved_goal(game, saved, queued)
            if result.get("status") in UNCERTAIN_RESULTS | {"tool_unavailable"}:
                self._block_saved_goal(game, saved, result)
            return {"calls": calls, "result": result, "speech": None}

        if self.job and (now - self.job["started"] >= 45 or self.job["steps"] >= 8):
            self.last_selection["status"] = (
                "step_budget" if self.job["steps"] >= 8 else "time_budget"
            )
            self.cooldowns[self.job["id"]] = now + 60
            self.job = None
        if self.job and self.job["id"] in self.unavailable:
            self.last_selection["status"] = "tool_unavailable"
            self.job = None
        if self.job is None:
            self.focus = None
            inventory = call("get_inventory", {})
            if inventory.get("food", 20) < 19 or (
                inventory.get("health", 20) < 18 and inventory.get("food", 20) < 20
            ):
                counts = {}
                for item in inventory.get("items", []):
                    counts[item["item"]] = counts.get(item["item"], 0) + item["count"]
                food = next(
                    (
                        item
                        for item in FOODS
                        if counts.get("minecraft:" + item, 0)
                        > (1 if item in ("carrot", "potato") else 0)
                    ),
                    None,
                )
                if food:
                    self.focus = "Еда из собственных припасов"
                    result = call("eat_food", {"item": food})
                    self.due = now + 8
                    return {
                        "calls": calls,
                        "result": result,
                        "speech": self.speech.say("eating")
                        if result.get("consumed")
                        else None,
                    }
                if counts.get("minecraft:wheat", 0) >= 3:
                    self.focus = "Приготовление хлеба для себя"
                    result = call("obtain_item", {"item": "bread", "count": 1})
                    self.due = now + 2.5
                    return {"calls": calls, "result": result, "speech": None}
            choices = [
                a
                for a in self.activities
                if self.cooldowns.get(a["id"], 0) <= now
                and self.weights[a["id"]] > 0
                and a["id"] not in self.unavailable
            ]
            if self.settings_error:
                return None
            activity = self._queued_choice(choices, now)
            if activity is None:
                waits = [
                    self.cooldowns[a["id"]]
                    for a in self.activities
                    if self.weights[a["id"]] > 0
                    and a["id"] not in self.unavailable
                    and self.cooldowns.get(a["id"], 0) > now
                ]
                self.due = min(waits) if waits else now + 8
                return {
                    "calls": calls,
                    "result": {
                        "status": "cooldown"
                        if waits
                        else "tool_unavailable"
                        if any(self.weights.values())
                        else "zero_weights"
                    },
                    "speech": None,
                }
            self.last_selection = {
                "id": activity["id"],
                "label": activity.get("label", activity["id"]),
                "selected_at": time.time(),
                "status": "selected",
                "probability": activity.get("probability", 0),
            }
            self.history.append(self.last_selection)
            self.history = self.history[-20:]
            self.job = {
                **activity,
                "arguments": dict(activity["arguments"]),
                "started": now,
                "steps": 0,
            }
        job = self.job
        if job.get("awaiting_move") and "position" in game:
            try:
                moved = (
                    math.dist(
                        tuple(float(n) for n in game["position"].split(",")),
                        job["move_origin"],
                    )
                    >= 0.5
                )
            except (ValueError, TypeError, AttributeError):
                moved = False
            if not moved:
                self.last_selection["status"] = "movement_unconfirmed"
                self.cooldowns[job["id"]] = now + 60
                self.job = None
                return {
                    "calls": calls,
                    "result": {"status": "movement_unconfirmed"},
                    "speech": None,
                }
            job.pop("awaiting_move", None)
            if job.get("kind") == "search":
                # A new observation area invalidates local negative results. Give
                # actual work a chance before another exploratory walk.
                latest = {row["id"]: row for row in self.history}
                for row in latest.values():
                    if row.get("status") in (
                        "no_work",
                        "no_target",
                        "waiting_for_growth",
                    ):
                        self.cooldowns.pop(row["id"], None)
                self.cooldowns.pop(job["id"], None)
                self.pending = [
                    row for row in self.pending if row.get("kind") != "search"
                ]
                base = next(a for a in self.activities if a["id"] == job["id"])
                self.pending.insert(0, dict(base))
                self.job = None
                self.last_selection["status"] = "new_area"
                self.due = now
                return {
                    "calls": calls,
                    "result": {"status": "new_area"},
                    "speech": None,
                }
        job["steps"] += 1
        result = call(job["tool"], job["arguments"])
        action = result.get("next_action")
        if action and result.get("status") not in UNCERTAIN_RESULTS:
            # Only these literal, separately guarded movement commands are accepted.
            if action.get("tool") == "agent_action":
                movement = call("agent_action", action["arguments"])
                if movement.get("status") in ("error", "tool_unavailable"):
                    result = {
                        "status": "blocked",
                        "reason": "Movement rejected",
                        "detail": movement.get("detail"),
                    }
                elif game.get("position"):
                    job["awaiting_move"] = True
                    job["move_origin"] = tuple(
                        float(n) for n in game["position"].split(",")
                    )
                self.due = (
                    now + 2.5
                )  # Bridge must have time to publish native task startup.
            elif action.get("tool") == "obtain_item" and (
                (
                    job["id"] == "farm"
                    and action.get("arguments", {}).get("item") == "wooden_hoe"
                )
                or (
                    job["id"] == "trees"
                    and action.get("arguments", {}).get("item")
                    in ("oak_sapling", "birch_sapling")
                )
            ):
                queued = call("obtain_item", action["arguments"])
                self.due = now + 2.5
                if queued.get("status") in ("error", "tool_unavailable"):
                    result = queued
            else:
                result = {
                    "status": "blocked",
                    "reason": "Unsupported background continuation",
                }
        status = result.get("status")
        if job["id"] == "hunt" and result.get("target", {}).get("uuid"):
            job["arguments"]["uuid"] = result["target"]["uuid"]
        continue_work = status in (
            "needs_move",
            "needs_materials",
            "waiting_for_game_task",
            "waiting_for_cooldown",
            "planted",
            "tilled",
            "harvested",
            "attacked",
            "cast_requested",
        )
        if status in UNCERTAIN_RESULTS:
            # Abandon this action. A future activity invocation scans the world
            # afresh; never replay its click/continuation or stop unrelated work.
            self._record_issue(job["id"], job["tool"], job["arguments"], result)
        self.last_selection["status"] = status
        self.last_selection["reason_code"] = result.get("reason_code")
        self.last_selection["reason"] = (
            result.get("reason") or result.get("detail") or result.get("hint")
        )
        if not continue_work:
            self.cooldowns[job["id"]] = now + 60
            if status in ("no_work", "no_target", "waiting_for_growth"):
                self._schedule_search({**job, "reason_code": result.get("reason_code")})
            self.job = None
            self.due = now + (2.5 if status in UNCERTAIN_RESULTS else 0.1)
        speech = None
        if job["steps"] == 1 or status == "killed":
            speech = self.speech.say(status, job["id"])
        return {"calls": calls, "result": result, "speech": speech}


def exploration_step(failed_goals=(), state=None, search_steps=4):
    from saruman.minecraft import _java_to_python
    from saruman.terrain_search import plan_exploration
    from saruman.trees import _gateway, _position, _walk_paths
    from saruman.world_rules import NativeWorldRules

    gateway = _gateway()
    try:
        entry = gateway.entry_point
        if not entry.inGame():
            return {"status": "disconnected"}
        if entry.hasActiveTask():
            return {"status": "waiting_for_game_task"}
        center = _position(_java_to_python(entry.getGameState())["self"]["pos"])
        observed = {
            _position(b["position"]): b["block"]
            for b in _java_to_python(entry.getBlocksAround(5))
        }
        rules = NativeWorldRules(gateway, observed, center, 5)
        paths = _walk_paths(observed, center, rules)
        state = {} if state is None else state
        if time.time() - state.get("created_at", 0) > 900:
            state.clear()
            state["created_at"] = time.time()
        if state.get("last_center") and math.dist(center, state["last_center"]) > 16:
            state["proposals"] = []
            state.pop("retreat", None)
            state.pop("full_pass_exhausted", None)
        state["last_center"] = list(center)
        # A full autonomous pass is bounded, but not a permanent ban on work.
        # Retain arrival history when starting the next pass, so it cannot simply
        # forget a failed circuit and bounce between the same two cells.
        if state.pop("full_pass_exhausted", False):
            state["proposals"] = []
            state["pass"] = state.get("pass", 1) + 1
        result = plan_exploration(paths, center, state, search_steps, failed_goals)
        if (
            result["status"] == "search_budget_exhausted"
            and search_steps == search_max_moves()
        ):
            state["full_pass_exhausted"] = True
        return {
            **result,
            "reason_code": result["status"],
            "status": "needs_move" if result.get("next_action") else "no_work",
        }
    finally:
        gateway.close()
