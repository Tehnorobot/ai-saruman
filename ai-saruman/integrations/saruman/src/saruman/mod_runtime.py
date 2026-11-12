"""Independent native-skill dispatcher. No model calls, movement loops or combat AI."""

import os
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
        self.next_plan = 0
        self.last_error = None
        self.owner = uuid.uuid4().hex
        self.was_enabled = False

    def change(self, goal, **changes):
        result = self.store.update(goal, **changes)
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
        if not self.store.lease(self.owner, now):
            return
        try:
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
            manual_paused = bool(enabled and self.was_enabled and snapshot.get("manual_stop_latched"))
            active = bool(enabled and not manual_paused)
            if snapshot["defense_enabled"] != active:
                self.call("defense", snapshot, enabled=active)
            if snapshot.get("manual_stop"):
                self.store.cancel(reason="Native task stop")
                self.next_plan = now + 10
            self.call(
                "maintain", snapshot, ack_manual_stop=bool(snapshot.get("manual_stop"))
            )
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
            }
        except Exception as error:  # noqa: BLE001 - boundary to an optional JVM plugin
            # A lost response is NOT permission to repeat a world action with a new id.
            self.last_error = f"{type(error).__name__}: {error}"
            game["mod_runtime"] = {
                "status": "unavailable",
                "error": self.last_error,
                "updated": now,
            }
        game["mod_goals"] = self.store.list(limit=40)

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
                    continue
                if goal["run_id"]:
                    self.call("cancel", snapshot, run_id=goal["run_id"])
                if goal["kind"] == "command" and goal["state"] == "cancel_requested":
                    self.call("stop_command", snapshot, run_id=goal["run_id"])
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
                if game.get("alive") is False:
                    self.change(
                        goal,
                        state="failed",
                        reason="Player died; confirmed progress retained, current run will not resume",
                    )
                    continue
                if game.get("alive") is not True:
                    return
                if busy:
                    self.store.update(goal, state="interrupted", saw_defense=True)
                    continue
                if goal["kind"] == "trees":
                    if tree.get("run_id") != goal["run_id"]:
                        if goal["state"] == "starting":
                            response = self.call(
                                "tree_start", snapshot, request_id=goal["run_id"]
                            )
                            if response["status"] == "started":
                                self.store.update(goal, state="running")
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
                            self.next_plan = now + 10
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
                            self.next_plan = now + 30
                    elif now > goal.get("deadline", now + 1):
                        self.call("cancel", snapshot, run_id=goal["run_id"])
                        self.change(
                            goal, state="failed", reason="Native run deadline exceeded"
                        )
                else:
                    if goal.get("saw_defense"):
                        # Generic commands may have side effects; never replay blindly.
                        self.change(
                            goal,
                            state="uncertain",
                            reason="Interrupted by defense; inspect state before issuing the next player command",
                        )
                    elif (
                        not snapshot["native_busy"]
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
            or game.get("alive") is not True
        ):
            return
        goals = self.store.list(active=True)
        if any(
            g["state"]
            in {"starting", "running", "interrupted", "resuming", "cancel_requested"}
            for g in goals
        ):
            return
        if snapshot["native_busy"]:
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
            if (
                not replying
                and not player_waiting
                and now >= self.next_plan
                and os.getenv("SARUMAN_AUTONOMY_ENABLED", "true").lower()
                in {"true", "1", "yes"}
            ):
                weights = load_weights(activities=catalog())
                tree_enabled = (
                    game.get("mod_tool_controls", {}).get("grow_trees", True)
                    if tools is None
                    else "grow_trees" in tools
                    and not tools["grow_trees"].get("disabled", False)
                )
                if weights.get("trees", 0) > 0 and tree_enabled:
                    self.store.submit(
                        "trees",
                        source="planner",
                        request_id=f"planner:{uuid.uuid4().hex}",
                    )
                self.next_plan = now + 10
            return
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

    def close(self):
        if not self.store.lease(self.owner, self.clock()):
            return
        snapshot = self.adapter.call("snapshot")
        if snapshot.get("status") == "ready":
            self.call("defense", snapshot, enabled=False)
            self.store.cancel()
            for goal in self.store.list(active=True):
                if goal["run_id"] and goal["kind"] == "trees":
                    self.call("cancel", snapshot, run_id=goal["run_id"])
                elif goal["run_id"] and goal["kind"] == "command":
                    self.call("stop_command", snapshot, run_id=goal["run_id"])
        self.store.lease(self.owner, self.clock(), ttl=0)
