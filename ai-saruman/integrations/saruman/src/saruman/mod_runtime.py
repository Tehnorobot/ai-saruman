"""Independent native-skill dispatcher. No model calls, movement loops or combat AI."""

import os
import random
import re
import time
import uuid

from saruman.action_speech import ActionSpeech
from saruman.autonomy_settings import catalog, load_weights
from saruman.mod_goals import GoalStore, player_context, public_chat_text
from saruman.activity import emit
from saruman.automation_extensions import PRIORITY, managed_directory, native_catalog, read_json
from saruman.managed import atomic_json


class AutomationRuntime:
    def __init__(
        self,
        adapter,
        store=None,
        clock=time.time,
        notify=None,
        server_send=None,
        supply=None,
        announce=None,
        event_sink=None,
        rng=None,
        tick_clock=None,
    ):
        self.adapter, self.store, self.clock = adapter, store or GoalStore(), clock
        # Polling uses elapsed time. Wall time remains useful in journals and messages.
        self.tick_clock = tick_clock or (time.monotonic if clock is time.time else clock)
        self.notify = notify or (lambda goal: None)
        self.server_send = server_send
        self.supply = supply
        self.announce = announce or (lambda text, emotion: None)
        self.speech = ActionSpeech()
        self.event_sink = event_sink
        self.event_error = None
        self.speech_catalogue_loaded = False
        self.last_plant = None
        self.next_tick = 0
        self.last_error = None
        self.owner = uuid.uuid4().hex
        self.was_enabled = False
        self.start_token = self.store.meta("native_start_token")
        self.rng = rng or random.Random()
        self.idle_since = None
        self.last_failed_activity = None
        self.activity = {"phase": "starting", "reason": "Проверка игрового состояния"}
        self.last_control = None
        self.extension_directory = managed_directory()
        self.default_tasks = [{**task, "extension": item["id"]} for item in native_catalog() for task in item.get("tasks", [])
                              if task["mode"] == "random"]
        self.extension_tasks = self.default_tasks
        self.extension_flags = {}
        self.extension_revision = None
        self.replan_revision = None
        self.adapter_hold = False
        self.adapter_resume_pending = False
        self.offer = None
        self.replan = False
        self.activity_weights = {}
        self.adapter_owner = {}
        self.operator_adapter = None
        self.operator_adapter_target = None
        self.handoff = None
        self.request_source = None
        self.hunting_until = None

    def reload_extensions(self, snapshot):
        settings = read_json(self.extension_directory / "extensions.json") if self.extension_directory else {}
        self.extension_flags = settings.get("native", {})
        self.extension_tasks = settings.get("tasks", self.default_tasks)
        self.adapter_hold = settings.get("adapter_hold", False)
        self.adapter_resume_pending = settings.get("adapter_resume_pending", False)
        self.adapter_owner = settings.get("adapter_owner") or {}
        self.operator_adapter = settings.get("operator_adapter")
        self.operator_adapter_target = settings.get("operator_adapter_target")
        self.activity_weights = settings.get("weights", {})
        revision = settings.get("revision")
        if revision != self.extension_revision:
            self.extension_revision = revision
            self.discard_offer()
        replan = settings.get("replan")
        if replan != self.replan_revision:
            self.replan = self.replan_revision is not None
            self.replan_revision = replan
        if self.offer and not any(task.get("offer") == self.offer["id"] for task in self.extension_tasks):
            self.discard_offer()
        self.extension_flags = {**self.extension_flags, **{
            key: False for key, present in snapshot.get("extensions", {}).items() if not present}}

    def discard_offer(self):
        if self.offer:
            atomic_json(self.extension_directory / "extension-offer.json", {})
            self.offer = None

    def requested_source(self, game, snapshot, now):
        sources = [g["source"] for g in self.store.list(active=True)
                   if g["kind"] not in {"chat", "telegram"} and g["state"] != "cancel_requested"
                   and g["world"] in {None, snapshot["world"]}]
        context = player_context(game)
        if context.get("replying") and context.get("until", 0) > now:
            sources.append(context.get("source", "player"))
        elif game.get("mod_player_pending_until", 0) > now:
            sources.append(game.get("mod_pending_source") or "player")
        if self.extension_directory:
            command = read_json(self.extension_directory / "command.json")
            directive = command.get("directive") or {}
            identifier = directive.get("id")
            if identifier and command.get("active") and now < directive.get("priority_until", 0):
                receipt = read_json(self.extension_directory / "results" / (identifier + ".json"))
                terminal = {"completed", "failed", "cancelled", "uncertain", "error", "refused", "clarify", "conversation"}
                if receipt.get("status") not in terminal and receipt.get("request_state") not in {"finished", "error", "superseded", "cancelled"}:
                    sources.append("operator")
        return max(sources, key=lambda source: PRIORITY.get(source, 0), default=None)

    def hold(self, phase, reason):
        self.discard_offer()
        self.idle_since = None
        self.activity = {"phase": phase, "reason": reason}

    def idle(self, game, snapshot, tools):
        now = self.clock()
        if self.replan or self.idle_since is None:
            self.idle_since = now - 20 if self.replan else now
        self.replan = False
        remaining = max(0, 20 - (now - self.idle_since))
        self.activity = {"phase": "idle", "reason": "Выбор занятия после 20 секунд бездействия",
                         "idle_seconds": now-self.idle_since, "next_in": remaining}
        if remaining or os.getenv("SARUMAN_AUTONOMY_ENABLED", "true").lower() not in {"true", "1", "yes"}:
            return
        weights = load_weights(activities=catalog())
        players = [name for name in snapshot.get("players", [])
                   if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_]{1,16}", name)]
        choices = {}
        for task in self.extension_tasks:
            name, tool = task["id"], task.get("tool")
            if not self.extension_flags.get(task.get("extension"), True):
                continue
            if tool and (not game.get("mod_tool_controls", {}).get(tool, True)
                         or tools is not None and (tool not in tools or tools[tool].get("disabled", False))):
                continue
            if task.get("weight") and name not in self.activity_weights and not weights.get(task["weight"], 0):
                continue
            if name == "hunt" and not snapshot.get("defense_modes"):
                continue
            if task.get("target") == "player" and not players:
                continue
            weight = self.activity_weights.get(name, task.get("default_weight", 1))
            if weight > 0:
                choices[name] = {**task, "selection_weight": weight}
        if not choices:
            self.activity["reason"] = "Нет доступных фоновых занятий"
            return
        if len(choices) > 1 and self.last_failed_activity in choices:
            choices.pop(self.last_failed_activity)
        weights = [task["selection_weight"] for task in choices.values()]
        kind = ("adapter:" + self.offer["adapter"] if self.offer else
                self.rng.choice(list(choices)) if len(set(weights)) == 1 else
                self.rng.choices(list(choices), weights=weights, k=1)[0])
        if kind == "hunt":
            result = self.call("defense", snapshot, enabled=True, mode="SEARCH", suppress_native=False)
            if result.get("status") == "applied":
                self.hunting_until = now + 20
                self.hold("hunt", "Поиск враждебного моба")
            return
        if kind.startswith("adapter:"):
            if not self.extension_directory or kind not in choices:
                self.discard_offer()
                return
            self.offer = {"adapter": kind.split(":", 1)[1], "id": choices[kind]["offer"],
                          "updated": now, "revision": self.extension_revision,
                          "world": snapshot["world"],
                          "worker_id": os.getenv("SARUMAN_LAB_WORKER_ID")}
            atomic_json(self.extension_directory / "extension-offer.json", self.offer)
            self.activity = {"phase": "queued", "reason": choices[kind]["title"]}
            return
        self.store.submit(kind, source="planner", request_id=f"planner:{uuid.uuid4().hex}",
                          target=self.rng.choice(players) if choices[kind].get("target") == "player" else None)
        self.hold("queued", "Выбрана посадка дерева" if kind == "trees" else "Выбрано следование за игроком")

    def change(self, goal, **changes):
        result = self.store.update(goal, **changes)
        if result["state"] in {"completed", "failed", "cancelled", "uncertain"}:
            self.idle_since = self.clock()
            if result["source"] == "planner" and result["state"] in {"failed", "uncertain"}:
                # Prefer another available hobby after a failure, without an
                # extra cooldown or delaying a player's accepted request.
                self.last_failed_activity = result["kind"]
        if self.event_sink and goal["kind"] != "chat":
            try:
                self.event_sink(goal, result)
                self.event_error = None
            except Exception as error:  # telemetry failure must not stop a game action
                self.event_error = type(error).__name__
        if (
            result["state"] in {"completed", "failed", "uncertain"}
            and goal["source"] != "planner"
        ):
            self.notify(result)
        return result

    def call(self, operation, snapshot, **args):
        if not self.lease(self.clock()):
            raise RuntimeError("Native dispatcher lease was transferred")
        result = self.adapter.call(operation, world=snapshot["world"], **args)
        if result.get("status") == "error":
            raise RuntimeError(result.get("reason"))
        return result

    def halt(self, snapshot):
        result = self.call("halt", snapshot)
        if result.get("status") != "halted":
            raise RuntimeError("Native stop unconfirmed: " + str(result.get("reason") or result.get("status")))

    def lease(self, now, *, ttl=5):
        steady_now = self.tick_clock() if self.tick_clock is not self.clock else None
        return self.store.lease(self.owner, now, ttl=ttl, steady_now=steady_now)

    def tick(self, game, *, enabled, tools=None):
        tick = self.tick_clock()
        if tick < self.next_tick:
            return
        self.next_tick = tick + 0.25
        now = self.clock()
        try:
            if not self.lease(now):
                return
            if not self.speech_catalogue_loaded:
                records = game.get("action_voice_records")
                if records is not None:
                    self.speech.replace([dict(row.items()) for row in records])
                    self.speech.source = "postgresql"
                    self.speech_catalogue_loaded = True
            snapshot = self.adapter.call("snapshot")
            if snapshot.get("status") != "ready":
                raise RuntimeError(snapshot.get("reason", "Native mods unavailable"))
            self.reload_extensions(snapshot)
            if not enabled and (self.was_enabled or snapshot.get("native_defense_task")):
                # Stop Lab must also halt independent native chains. Ordinary
                # player-goal preemption below still cancels only its exact root.
                self.store.cancel(reason="Automation stopped")
                self.halt(snapshot)
                snapshot = self.adapter.call("snapshot")
                if snapshot.get("status") != "ready":
                    raise RuntimeError(snapshot.get("reason", "Native stop unconfirmed"))
                if snapshot.get("runner_active") is True:
                    raise RuntimeError("Native stop unconfirmed: runner is still active")
            # The addon latches an explicit native stop. Polling must not rearm it.
            # A new Stop -> Start is an explicit operator request to resume.
            reset = snapshot.get("stop_kind") == "reset"
            alive = snapshot.get("player_alive", game.get("alive")) is True
            start_token = game.get("native_start_token")
            explicit_start = bool(enabled and start_token is not None and start_token != self.start_token)
            manual_paused = bool(enabled and self.was_enabled and snapshot.get("manual_stop_latched") and not reset and not explicit_start)
            active = bool(enabled and not manual_paused and alive and snapshot["in_world"])
            self.request_source = self.requested_source(game, snapshot, now)
            adapter_priority = PRIORITY.get(self.adapter_owner.get("source"), 0)
            priority = max(PRIORITY.get(self.request_source, 0), adapter_priority,
                           PRIORITY["operator"] if self.operator_adapter else 0)
            suppress = active and priority >= PRIORITY["player"]
            defense_active = active and self.extension_flags.get("auto_defense", True) and not suppress
            if (not defense_active or priority > PRIORITY["planner"] or self.adapter_hold
                    or self.hunting_until is not None and now >= self.hunting_until):
                self.hunting_until = None
            mode = ("SEARCH" if self.hunting_until is not None else "SELF_DEFENSE") if active else snapshot.get("defense_mode")
            release_stop = bool(explicit_start and active and snapshot.get("manual_stop_latched"))
            if snapshot.get("defense_modes") or self.extension_directory:
                if (snapshot["defense_enabled"] != defense_active or snapshot.get("defense_mode", mode) != mode
                        or snapshot.get("native_suppressed", False) != suppress or release_stop):
                    response = self.call("defense", snapshot, enabled=defense_active, suppress_native=suppress,
                                         **({"mode": mode} if mode and snapshot.get("defense_modes") else {}),
                                         **({"resume": True} if release_stop else {}))
                    if response.get("status") != "applied":
                        raise RuntimeError(response.get("reason") or "Minecraft не подтвердил смену режима")
                    snapshot = self.adapter.call("snapshot")
                    if snapshot.get("status") != "ready":
                        raise RuntimeError(snapshot.get("reason", "Control switch not confirmed"))
                    if release_stop and snapshot.get("manual_stop_latched"):
                        raise RuntimeError("Minecraft ещё не снял ручную паузу")
            elif snapshot["defense_enabled"] != defense_active and not suppress:
                self.call("defense", snapshot, enabled=defense_active)
            if snapshot.get("manual_stop") and not reset and not explicit_start:
                self.store.cancel(reason="Native task stop")
            self.call(
                "maintain", snapshot, ack_manual_stop=bool(snapshot.get("manual_stop"))
            )
            if explicit_start and active:
                self.store.set_meta("native_start_token", start_token)
                self.start_token = start_token
            if not enabled and self.was_enabled:
                self.store.cancel(reason="Automation stopped")
            self.was_enabled = enabled
            self.reconcile(game, snapshot, active, tools)
            if defense_active and self.supply is not None:
                self.supply.tick(game, snapshot)
            tree = snapshot.get("tree", {})
            marker = (tree.get("run_id"), tree.get("site"))
            if (
                enabled
                and tree.get("planted")
                and not tree.get("isFinished")
                and marker != self.last_plant
            ):
                self.last_plant = marker
                speech = self.speech.say("planted", "trees")
                if speech:
                    self.announce(speech, "neutral")
                    game["mod_commentary"] = self.speech.snapshot()
            self.last_error = None
            game["mod_runtime"] = {
                **snapshot,
                "updated": now,
                "status": "paused" if manual_paused else "running" if enabled else "stopped",
                "activity": self.activity,
                "request_source": self.request_source,
                "adapter_grant": self.handoff,
                "hunt_active": self.hunting_until is not None,
            }
            control = {key:snapshot.get(key) for key in ('world','in_world','player_alive','defense_busy','native_busy','stop_kind','scheduler_busy','scheduler_owner')}
            control.update(phase=self.activity.get('phase'), reason=self.activity.get('reason'))
            if control != self.last_control:
                emit('minecraft.control', status=self.activity.get('phase','observed'), details={'control':control,'snapshot':snapshot})
                self.last_control = control
            game["mod_goals"] = self.store.list(limit=40)
        except Exception as error:  # noqa: BLE001 - boundary to an optional JVM plugin
            self.handoff = None
            # A lost response is NOT permission to repeat a world action with a new id.
            self.last_error = f"{type(error).__name__}: {error}"
            self.hold("unavailable", "Ожидание восстановления игрового API")
            game["mod_runtime"] = {
                "status": "unavailable",
                "error": self.last_error,
                "updated": now,
            }

    def deliver_chat(self, snapshot, enabled):
        """Deliver public text once, independently of physical control ownership."""
        for goal in self.store.list(active=True):
            if goal["kind"] != "chat":
                continue
            if goal["state"] == "cancel_requested":
                self.change(goal, state="cancelled", reason=goal.get("reason") or "Cancelled")
                continue
            if goal["world"] != snapshot["world"]:
                self.change(goal, state="uncertain", reason="World changed; message was not resent")
                continue
            if goal["state"] != "queued":
                self.change(goal, state="uncertain", reason="Chat delivery was interrupted; no automatic resend")
                continue
            if not enabled or not snapshot["in_world"]:
                continue
            try:
                text = public_chat_text(goal.get("message"))
            except ValueError as error:
                self.change(goal, state="failed", reason=str(error))
                continue
            if self.server_send is None:
                self.change(goal, state="failed", reason="Minecraft chat transport unavailable")
                continue
            # Reserve before the side effect. Neither a crash nor a lost API
            # response is permission to publish the same message a second time.
            reserved = self.store.update(goal, state="starting", sent_at=self.clock())
            if reserved["state"] != "starting":
                continue
            try:
                if not self.lease(self.clock()):
                    self.change(reserved, state="uncertain", reason="Chat dispatcher ownership changed")
                    continue
                self.server_send(text)
            except Exception as error:
                self.change(reserved, state="uncertain", reason="Chat delivery unconfirmed: " + type(error).__name__)
            else:
                self.change(reserved, state="completed", completed=1,
                    reason="Сообщение передано игровому клиенту; чтение другими игроками не проверялось",
                    delivery="client_call_returned")

    def reconcile(self, game, snapshot, enabled, tools):
        now, world = self.clock(), snapshot["world"]
        self.handoff = None
        self.deliver_chat(snapshot, enabled)
        goals = [goal for goal in self.store.list(active=True) if goal["kind"] not in {"chat", "telegram"}]
        defense_busy = bool(snapshot['defense_busy'])
        scheduler_busy = bool(snapshot.get('scheduler_busy'))
        busy = defense_busy or scheduler_busy
        owner = 'Защита' if defense_busy else 'UnionClef: '+str(snapshot.get('scheduler_owner') or 'приоритетная задача')
        tree = snapshot.get("tree", {})
        context = player_context(game)
        replying = bool(
            context.get("replying")
            and context.get("until", 0) > now
            or game.get("mod_player_pending_until", 0) > now
        )
        player_waiting = replying or any(g["source"] != "planner" for g in goals)
        requester = PRIORITY.get(self.request_source, 0)
        if self.operator_adapter:
            requester = PRIORITY["operator"]
        if replying:
            self.hold("player", "Обращение игрока: собственное занятие прерывается")
        for goal in goals:
            if goal["world"] and goal["world"] != world:
                self.change(
                    goal,
                    # Cancellation is already final intent. No command can be
                    # stopped in the old world; acknowledge it locally instead
                    # of requesting a transition the store correctly rejects.
                    state="cancelled"
                    if goal["state"] == "cancel_requested"
                    else "uncertain",
                    reason="World changed; confirmed progress retained, no automatic replay",
                )
                continue
            disabled = ((goal["kind"] == "trees" and not self.extension_flags.get("treegrow", True))
                        or (goal["kind"] == "follow" and not self.extension_flags.get("follow", True)))
            if disabled and goal["state"] != "cancel_requested":
                goal = self.store.update(goal, state="cancel_requested", reason="Компонент выключен или недоступен")
            if (
                goal["state"] == "cancel_requested"
                or goal["source"] == "planner"
                and (player_waiting or self.replan)
                or PRIORITY[goal["source"]] < requester and goal["state"] != "queued"
                or self.operator_adapter and goal["state"] != "queued"
            ):
                if busy:
                    self.hold("defense" if defense_busy else "recovery", owner+" удерживает управление; поручение игрока сохранено")
                    continue
                if goal["run_id"]:
                    response = self.call("cancel" if goal["kind"] == "trees" else "stop_command", snapshot,
                                         run_id=goal.get("native_command_run") or goal["run_id"])
                    if response.get("status") == "busy":
                        self.hold("defense", "Ожидание освобождения управления для остановки занятия")
                        continue
                completed = goal["completed"]
                if (
                    goal["kind"] == "trees"
                    and goal["run_id"] == tree.get("run_id")
                    and tree.get("isFinished")
                    and tree.get("success")
                ):
                    completed += 1
                self.change(
                    goal,
                    state="cancelled",
                    completed=completed,
                    reason=(goal.get("reason") or "Stopped")
                    if goal["state"] == "cancel_requested"
                    else "Player priority",
                )
                continue
            if goal["state"] in {"running", "starting", "interrupted", "resuming"}:
                self.hold("working", "Посадка деревьев" if goal["kind"] == "trees" else "Следование за игроком" if goal["kind"] == "follow" else "Выполнение поручения игрока")
                self.activity.update(goal_id=goal["id"], source=goal["source"], completed=goal["completed"], count=goal["count"])
                if snapshot.get("player_alive", game.get("alive")) is False or snapshot.get("stop_kind") == "reset":
                    self.change(
                        goal,
                        state="failed",
                        reason="Player died; confirmed progress retained, current run will not resume",
                    )
                    continue
                if snapshot.get("player_alive", game.get("alive")) is not True:
                    return
                if busy:
                    self.store.update(goal, state="interrupted", saw_defense=bool(goal.get('saw_defense') or defense_busy),
                                      interrupted_by=owner)
                    self.hold("defense" if defense_busy else "recovery", owner+" удерживает управление; текущая попытка сохранена")
                    continue
                if goal["kind"] == "trees":
                    if tree.get("run_id") != goal["run_id"]:
                        if goal["state"] == "starting":
                            response = self.call(
                                "tree_start", snapshot, request_id=goal["run_id"]
                            )
                            if response["status"] == "started":
                                self.store.update(goal, state="running")
                            elif response["status"] != "busy":
                                self.change(goal, state="failed", reason=response.get("reason"))
                            elif now > goal.get("deadline", now+1):
                                self.change(goal, state="uncertain", reason="Native start not confirmed; inspect the retained receipt")
                        else:
                            self.change(
                                goal,
                                state="uncertain",
                                reason="Owned native run lost; no replacement planting",
                            )
                        return
                    if goal["state"] == "resuming":
                        response = (
                            self.call(
                                "tree_resume",
                                snapshot,
                                run_id=goal["run_id"],
                                request_id=goal["pending_resume"],
                            )
                            if tree.get("isFinished")
                            else {"status": "resumed"}
                        )
                        if response["status"] == "resumed":
                            self.store.update(goal, state="running", saw_defense=False)
                        elif response["status"] != "busy":
                            self.change(
                                goal, state="failed", reason=response.get("reason")
                            )
                        return
                    if tree.get("isFinished"):
                        if tree.get("success"):
                            count = goal["completed"] + 1
                            self.change(
                                goal,
                                completed=count,
                                run_id=None,
                                state="completed"
                                if count == goal["count"]
                                else "queued",
                                reason=tree.get("result"),
                            )
                        elif (
                            not tree.get("expired")
                            and tree.get("outcome") == "cancelled"
                            and bool(tree.get("interruption"))
                            and str(tree.get("result", "")).startswith(
                                "Отменено: задача UnionClef прервана"
                            )
                            and tree.get('resumable',True) is True
                        ):
                            resumes = goal.get("resumes", 0) + 1
                            pending = f"resume:{goal['run_id']}:{resumes}"
                            goal = self.store.update(
                                goal,
                                state="resuming",
                                resumes=resumes,
                                pending_resume=pending,
                            )
                            response = self.call(
                                "tree_resume",
                                snapshot,
                                run_id=goal["run_id"],
                                request_id=pending,
                            )
                            if response["status"] == "resumed":
                                self.store.update(
                                    goal,
                                    state="running",
                                    saw_defense=False,
                                    resumes=resumes,
                                )
                            elif response["status"] != "busy":
                                self.change(
                                    goal, state="failed", reason=response.get("reason")
                                )
                        else:
                            self.change(goal, state="failed", reason=tree.get("result"))
                    elif now > goal.get("deadline", now + 1):
                        self.call("cancel", snapshot, run_id=goal["run_id"])
                        self.change(
                            goal, state="failed", reason="Native run deadline exceeded"
                        )
                elif goal["kind"] == "follow":
                    command = snapshot.get("command", {})
                    if goal["target"] not in snapshot.get("players", []):
                        response = self.call("stop_command", snapshot, run_id=goal.get("native_command_run") or goal["run_id"])
                        if response.get("status") != "busy":
                            self.change(goal, state="failed", reason="Игрок больше недоступен для следования")
                    elif goal["state"] == "starting":
                        # Same receipt identity after a lost response. Never leave
                        # a delivered follow in starting forever or send a new ID.
                        response = self.call("command", snapshot, request_id=goal["run_id"], command=goal["command"])
                        if response["status"] == "sent":
                            self.store.update(goal, state="running")
                        elif response["status"] != "busy":
                            self.change(goal, state="failed", reason=response.get("reason"))
                        elif now > goal.get("deadline", now+1):
                            self.change(goal, state="uncertain", reason="Начало следования не подтверждено")
                    elif command.get("run_id") == (goal.get("native_command_run") or goal["run_id"]) and command.get("finished"):
                        self.change(goal, state="completed", reason="UnionClef завершил следование")
                    elif goal.get("saw_defense"):
                        # Following is a movement-only skill. It may be restarted
                        # after defense; side-effecting generic commands may not.
                        native_run = f"{goal['run_id']}:follow:{goal.get('resumes', 0)+1}"
                        goal = self.store.update(goal, native_command_run=native_run)
                        response = self.call("command", snapshot, request_id=native_run, command=goal["command"])
                        if response["status"] == "sent":
                            self.store.update(goal, state="running", saw_defense=False, resumes=goal.get("resumes", 0)+1, native_command_run=native_run)
                        elif response["status"] != "busy":
                            self.change(goal, state="failed", reason=response.get("reason"))
                    elif not snapshot["native_busy"] or command and (not command.get("current") or command.get("stopped")):
                        self.change(goal, state="failed", reason="UnionClef прекратил следование")
                else:
                    command = snapshot.get("command", {})
                    command_matches = command.get("run_id") == goal["run_id"]
                    if goal.get("saw_defense"):
                        # Generic commands may have side effects; never replay blindly.
                        self.change(
                            goal,
                            state="uncertain",
                            reason="Interrupted by defense; inspect state before issuing the next player command",
                        )
                    elif command_matches and command.get("stopped") and not command.get("finished"):
                        self.change(goal, state="failed", reason="UnionClef остановил поручение до завершения")
                    elif command and not command_matches and now-goal.get("sent_at", now) >= 1:
                        self.change(goal, state="uncertain", reason="Квитанция текущего поручения не найдена; автоматического повтора нет")
                    elif (
                        (not snapshot["native_busy"] or command_matches and command.get("finished"))
                        and now - goal.get("sent_at", now) >= 1
                    ):
                        self.change(
                            goal,
                            state="completed",
                            reason="Native task idle; requested outcome still requires observation",
                        )
                    elif now > goal.get("deadline", now + 1):
                        self.call("stop_command", snapshot, run_id=goal["run_id"])
                        self.change(
                            goal,
                            state="failed",
                            reason="Player command deadline exceeded",
                        )
                return
        if (
            not enabled
            or busy
            or not snapshot["in_world"]
            or snapshot.get("player_alive", game.get("alive")) is not True
        ):
            self.hold("recovery" if scheduler_busy and not defense_busy else "waiting", owner+" удерживает управление" if busy else "Ожидание возрождения" if snapshot.get("player_alive", game.get("alive")) is not True else "Игровые действия приостановлены")
            return
        goals = [g for g in self.store.list(active=True) if g['kind'] not in {'chat','telegram'}]
        if any(
            g["state"]
            in {"starting", "running", "interrupted", "resuming", "cancel_requested"}
            for g in goals
        ):
            return
        if snapshot["native_busy"]:
            self.hold("native", "UnionClef выполняет задачу; проверка её завершения")
            return  # Never cancel a task we did not start.
        if self.operator_adapter:
            if self.adapter_hold and self.adapter_owner.get("id") != self.operator_adapter_target:
                self.hold("native", "Ожидание освобождения управления другим адаптером")
                return
            self.handoff = self.operator_adapter
            self.hold("native", "Управление передано поручению адаптера из Lab")
            return
        if self.adapter_hold:
            self.hold("native", "Управление удерживает адаптер")
            return
        if self.hunting_until is not None:
            self.hold("hunt", "Поиск враждебного моба")
            return
        queued = next(
            (
                g
                for g in goals
                if g["state"] == "queued" and PRIORITY[g["source"]] >= requester
                and (not replying or g["source"] != "planner")
            ),
            None,
        )
        if queued is None:
            if not replying and not player_waiting and not self.adapter_resume_pending:
                self.idle(game, snapshot, tools)
            return
        self.hold("starting", "Запуск посадки дерева" if queued["kind"] == "trees" else "Запуск следования" if queued["kind"] == "follow" else "Запуск поручения игрока")
        run = f"{queued['id']}:{queued['completed']}"
        # Persist the identity BEFORE delivery, so a timeout can be reconciled.
        queued = self.store.update(
            queued,
            state="starting",
            world=world,
            run_id=run,
            deadline=now + 310,
            sent_at=now,
        )
        if queued["kind"] == "trees":
            response = self.call("tree_start", snapshot, request_id=run)
        else:
            command = queued["command"]
            if command.startswith("/"):
                if self.server_send is None:
                    self.change(
                        queued,
                        state="failed",
                        reason="Server command transport unavailable",
                    )
                    return
                # Persist unknown outcome before network delivery. Never retry a slash
                # command automatically: many server commands are not idempotent.
                self.change(
                    queued,
                    state="uncertain",
                    reason="Server command sent; verify actual outcome",
                )
                self.server_send(command)
                return
            response = self.call("command", snapshot, request_id=run, command=command)
        if response["status"] in {"started", "sent"}:
            self.store.update(queued, state="running")
        elif response["status"] == "busy":
            self.store.update(queued, state="queued", run_id=None)
        else:
            self.change(queued, state="failed", reason=response.get("reason"))

    def close(self, *, stop=True):
        if not self.lease(self.clock()):
            return
        try:
            if not stop:
                return  # Reconnect retains the JVM receipts and durable intentions.
            self.store.cancel(reason="Automation stopped")
            snapshot = self.adapter.call("snapshot")
            if snapshot.get("status") == "ready":
                self.halt(snapshot)
        finally:
            self.lease(self.clock(), ttl=0)
