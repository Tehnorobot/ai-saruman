"""Independent native-skill dispatcher. No model calls, movement loops or combat AI."""

import os
import random
import re
import time
import uuid

from saruman.action_speech import ActionSpeech
from saruman.autonomy_settings import catalog, load_weights
from saruman.mod_goals import GoalStore, player_context


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
    ):
        self.adapter, self.store, self.clock = adapter, store or GoalStore(), clock
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

    def hold(self, phase, reason):
        self.idle_since = None
        self.activity = {"phase": phase, "reason": reason}

    def idle(self, game, snapshot, tools):
        now = self.clock()
        if self.idle_since is None:
            self.idle_since = now
        remaining = max(0, 20 - (now - self.idle_since))
        self.activity = {"phase": "idle", "reason": "Выбор занятия после 20 секунд бездействия",
                         "idle_seconds": now-self.idle_since, "next_in": remaining}
        if remaining or os.getenv("SARUMAN_AUTONOMY_ENABLED", "true").lower() not in {"true", "1", "yes"}:
            return
        def available(name):
            return tools is None or name in tools and not tools[name].get("disabled", False)
        weights = load_weights(activities=catalog())
        choices = []
        if weights.get("trees", 0) > 0 and game.get("mod_tool_controls", {}).get("grow_trees", True) and available("grow_trees"):
            choices.append("trees")
        players = [name for name in snapshot.get("players", [])
                   if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_]{1,16}", name)]
        if players and available("agent_action"):
            choices.append("follow")
        if not choices:
            self.activity["reason"] = "Нет доступного занятия: ожидание игрока или включения посадки"
            return
        if len(choices) > 1 and self.last_failed_activity in choices:
            choices.remove(self.last_failed_activity)
        kind = self.rng.choice(choices)
        self.store.submit(kind, source="planner", request_id=f"planner:{uuid.uuid4().hex}",
                          target=self.rng.choice(players) if kind == "follow" else None)
        self.hold("queued", "Выбрана посадка дерева" if kind == "trees" else "Выбрано следование за игроком")

    def change(self, goal, **changes):
        result = self.store.update(goal, **changes)
        if result["state"] in {"completed", "failed", "cancelled", "uncertain"}:
            self.idle_since = self.clock()
            if result["source"] == "planner" and result["state"] in {"failed", "uncertain"}:
                # Prefer another available hobby after a failure, without an
                # extra cooldown or delaying a player's accepted request.
                self.last_failed_activity = result["kind"]
        if self.event_sink:
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
        if not self.store.lease(self.owner, self.clock()):
            raise RuntimeError("Native dispatcher lease was transferred")
        result = self.adapter.call(operation, world=snapshot["world"], **args)
        if result.get("status") == "error":
            raise RuntimeError(result.get("reason"))
        return result

    def tick(self, game, *, enabled, tools=None):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + 0.25
        try:
            if not self.store.lease(self.owner, now):
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
            # The addon latches an explicit native stop. Polling must not rearm it.
            # A new Stop -> Start is an explicit operator request to resume.
            reset = snapshot.get("stop_kind") == "reset"
            alive = snapshot.get("player_alive", game.get("alive")) is True
            start_token = game.get("native_start_token")
            explicit_start = bool(enabled and start_token is not None and start_token != self.start_token)
            manual_paused = bool(enabled and self.was_enabled and snapshot.get("manual_stop_latched") and not reset and not explicit_start)
            active = bool(enabled and not manual_paused and alive and snapshot["in_world"])
            if snapshot["defense_enabled"] != active:
                self.call("defense", snapshot, enabled=active)
            if snapshot.get("manual_stop") and not reset and not explicit_start:
                self.store.cancel(reason="Native task stop")
            self.call(
                "maintain", snapshot, ack_manual_stop=bool(snapshot.get("manual_stop"))
            )
            if explicit_start:
                self.store.set_meta("native_start_token", start_token)
                self.start_token = start_token
            if not enabled and self.was_enabled:
                self.store.cancel(reason="Automation stopped")
            self.was_enabled = enabled
            self.reconcile(game, snapshot, active, tools)
            if active and self.supply is not None:
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
            }
            game["mod_goals"] = self.store.list(limit=40)
        except Exception as error:  # noqa: BLE001 - boundary to an optional JVM plugin
            # A lost response is NOT permission to repeat a world action with a new id.
            self.last_error = f"{type(error).__name__}: {error}"
            self.hold("unavailable", "Ожидание восстановления игрового API")
            game["mod_runtime"] = {
                "status": "unavailable",
                "error": self.last_error,
                "updated": now,
            }

    def reconcile(self, game, snapshot, enabled, tools):
        now, world = self.clock(), snapshot["world"]
        goals = self.store.list(active=True)
        busy = snapshot["defense_busy"]
        tree = snapshot.get("tree", {})
        context = player_context(game)
        replying = bool(
            context.get("replying")
            and context.get("until", 0) > now
            or game.get("mod_player_pending_until", 0) > now
        )
        player_waiting = replying or any(g["source"] != "planner" for g in goals)
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
            if (
                goal["state"] == "cancel_requested"
                or goal["source"] == "planner"
                and player_waiting
            ):
                if busy:
                    self.hold("defense", "Защита удерживает управление; поручение игрока сохранено")
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
                    self.store.update(goal, state="interrupted", saw_defense=True)
                    self.hold("defense", "Занятие прервано защитой")
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
                            goal.get("saw_defense")
                            and not tree.get("expired")
                            and tree.get("outcome") == "cancelled"
                            and bool(tree.get("interruption"))
                            and str(tree.get("result", "")).startswith(
                                "Отменено: задача UnionClef прервана"
                            )
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
            self.hold("waiting", "Защита удерживает управление" if busy else "Ожидание возрождения" if snapshot.get("player_alive", game.get("alive")) is not True else "Игровые действия приостановлены")
            return
        goals = self.store.list(active=True)
        if any(
            g["state"]
            in {"starting", "running", "interrupted", "resuming", "cancel_requested"}
            for g in goals
        ):
            return
        if snapshot["native_busy"]:
            self.hold("native", "UnionClef выполняет задачу; проверка её завершения")
            return  # Never cancel a task we did not start.
        queued = next(
            (
                g
                for g in goals
                if g["state"] == "queued" and (not replying or g["source"] != "planner")
            ),
            None,
        )
        if queued is None:
            if not replying and not player_waiting:
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
        if not self.store.lease(self.owner, self.clock()):
            return
        try:
            if not stop:
                return  # Reconnect retains the JVM receipts and durable intentions.
            snapshot = self.adapter.call("snapshot")
            if snapshot.get("status") == "ready":
                self.call("defense", snapshot, enabled=False)
                self.store.cancel()
                for goal in self.store.list(active=True):
                    if goal["run_id"] and goal["kind"] == "trees":
                        self.call("cancel", snapshot, run_id=goal["run_id"])
                    elif goal["run_id"] and goal["kind"] in {"command", "follow"}:
                        self.call("stop_command", snapshot, run_id=goal.get("native_command_run") or goal["run_id"])
        finally:
            self.store.lease(self.owner, self.clock(), ttl=0)
