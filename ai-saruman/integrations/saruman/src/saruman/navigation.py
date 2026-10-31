"""Bound host-owned navigation and collection through the shared Minecraft bridge."""

import math
import re
import threading
import time

from py4j.protocol import Py4JError

from saruman.collection import CollectionGuard
from saruman.minecraft import _java_to_python


def goto_target(command):
    match = re.fullmatch(r"@?goto\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", command.strip())
    return tuple(map(int, match.groups())) if match else None


def check_recent_failure(target, failures):
    for failure in failures:
        if (
            failure["until"] > time.time()
            and math.dist(target, failure["target"]) <= 1.5
        ):
            raise ValueError(
                "This destination or its immediate neighbour recently failed: "
                + failure["reason"]
                + ". Choose another observed standing position; do not repeat the route."
            )


class NavigationGuard:
    """Shared by bridge sender and snapshot threads. Only owns explicit @goto."""

    STALL_SECONDS = 30
    MAX_SECONDS = 180
    COOLDOWN_SECONDS = 120

    def __init__(self, rules_factory=None):
        self.rules_factory = rules_factory
        self.lock = threading.RLock()
        self.target = None
        self.failures = []
        self.stopping = False
        self.purpose = None
        self.collection = CollectionGuard()

    def _publish(self, game, status, **fields):
        game["navigation"] = {
            "event_id": time.time_ns(),
            "status": status,
            "target": list(self.target) if self.target is not None else None,
            "recent_failures": list(self.failures),
            "purpose": self.purpose,
            **fields,
        }

    def after_death(self, game):
        """Drop navigation/collection ownership, keeping saved goals and route history."""
        with self.lock:
            self.target = None
            self.stopping = False
            self.purpose = None
            self.collection.forget(
                game, reason="Player died; old native work interrupted"
            )
            self._publish(game, "interrupted_by_death")

    def interrupt_for_survival(self, entry, game):
        self.interrupt(entry, game, "Water rescue")

    def interrupt_for_combat(self, entry, game):
        self.interrupt(entry, game, "Self defense")

    def interrupt(self, entry, game, reason):
        with self.lock:
            if self.target is not None and reason == "Water rescue":
                self._fail(game, "Route interrupted by water rescue")
            self.collection.forget(game, reason=reason + " interrupted native work")
            self.target = None
            self.stopping = False
            self.purpose = None
            self._publish(game, "stop_requested", reason=reason)
            result = _java_to_python(entry.stopPathing())
            if not isinstance(result, dict) or not result.get("ok"):
                raise RuntimeError(reason + " stop was not accepted")

    def _fail(self, game, reason, position=None, status="blocked"):
        self.failures = [f for f in self.failures if f["until"] > time.time()][-7:]
        self.failures.append(
            {
                "target": list(self.target),
                "reason": reason,
                "until": time.time() + self.COOLDOWN_SECONDS,
            }
        )
        self._publish(
            game,
            status,
            reason=reason,
            position=position,
            hint="Check inventory/terrain; use an in-reach tree site or a different safe approach. Do not restart this destination.",
        )
        self.target = None
        self.stopping = False

    def send(self, entry, command, game, *, purpose="work"):
        """Validate at dispatch, where the observed world is current; no silent Y correction."""
        with self.lock:
            target = goto_target(command)
            name = command.strip().removeprefix("@").split()[0]
            if purpose != "combat" and name not in (
                "status",
                "inventory",
                "help",
                "check_block",
                "check_player",
            ):
                game["work_command_revision"] = time.time_ns()
            if (
                name
                not in (
                    "stop",
                    "cancel",
                    "status",
                    "inventory",
                    "help",
                    "check_block",
                    "check_player",
                )
                and game.get("collection", {}).get("status")
                in ("stopping", "stop_failed")
                and entry.hasActiveTask()
            ):
                raise ValueError(
                    "Collection cancellation is unconfirmed; do not replace the running native task"
                )
            if name in ("stop", "cancel"):
                result = _java_to_python(entry.stopPathing())
                if not result.get("ok"):
                    raise RuntimeError("Native stop was not accepted")
                self.target = None
                self.stopping = False
                self.purpose = None
                self._publish(game, "stop_requested")
                self.collection.forget(game)
                return
            if target is None:
                if name == "get" and self.collection.start(entry, command, game):
                    self.target = None
                    self.purpose = "work"
                    game["navigation"] = {
                        **dict(game.get("navigation", {}).items()),
                        "purpose": "work",
                    }
                    return
                entry.RunInnerCommand(command)
                # A new task replaces ownership; status queries and settings do not.
                if name not in (
                    "status",
                    "inventory",
                    "help",
                    "set",
                    "check_block",
                    "check_player",
                ):
                    self.target = None
                    self.purpose = "work"
                    game["navigation"] = {
                        **dict(game.get("navigation", {}).items()),
                        "purpose": "work",
                    }
                    self.stopping = False
                    self.collection.forget(game)
                return
            if self.stopping:
                raise ValueError(
                    "Previous route is still stopping; wait for hasActiveTask=false"
                )
            if (
                game.get("navigation", {}).get("status")
                in ("stop_failed", "stop_requested")
                and entry.hasActiveTask()
            ):
                raise ValueError(
                    "Cancellation is not confirmed; stop the native task before starting another route"
                )
            check_recent_failure(target, self.failures)
            if target == self.target:
                raise ValueError("This route is already running; wait for its result")
            x, y, z = target
            if self.rules_factory is None:
                raise RuntimeError(
                    "Native Minecraft collision checks are not configured"
                )
            rules = self.rules_factory()
            reason = None
            if not rules.support((x, y - 1, z)):
                reason = "No ground for dry standing at the requested feet Y; water is not a search destination"
            elif not rules.free((x, y, z)) or not rules.free((x, y + 1, z)):
                reason = (
                    "Feet or head cell is obstructed; choose a free standing position"
                )
            if reason:
                # A rejected replacement must not discard ownership of an already running route.
                old_target = self.target
                self.target = target
                self._fail(game, reason, status="rejected")
                self.target = old_target
                raise ValueError(reason)
            if purpose == "combat":
                state = _java_to_python(entry.getGameState()).get("self", {})
                center = tuple(math.floor(float(v)) for v in state["pos"].split(","))
                if (
                    abs(center[0] - x) + abs(center[2] - z) != 1
                    or abs(center[1] - y) > 1
                ):
                    raise ValueError(
                        "Combat approaches require one observed adjacent dry step"
                    )
                if y > center[1] and not rules.free(
                    (center[0], center[1] + 2, center[2])
                ):
                    raise ValueError("Combat step has no upward head clearance")
                if y < center[1] and not rules.free((x, center[1] + 1, z)):
                    raise ValueError("Combat step has no downward head clearance")
            entry.RunInnerCommand(command)
            self.collection.forget(game)
            self.target = target
            self.purpose = purpose
            self.started = self.progress_at = time.monotonic()
            self.best = math.inf
            self.stopping = False
            self._publish(game, "running")

    def tick(self, entry, game, position, busy, task_chain, state=None, user_task=None):
        with self.lock:
            self.collection.tick(
                entry, game, position, busy, task_chain, state, user_task
            )
            if self.target is None:
                previous = game.get("navigation", {}).get("status")
                if not busy and previous in (
                    "stop_requested",
                    "stopping",
                    "stop_failed",
                ):
                    # stopPathing acceptance is not completion. This fresh bridge
                    # snapshot confirms idle even when stop cleared route ownership.
                    self.stopping = False
                    self._publish(
                        game,
                        "stopped",
                        position=position,
                        previous_status=previous,
                        reason="Native task is no longer active",
                    )
                return
            now = time.monotonic()
            if self.stopping:
                if not busy:
                    self._fail(game, self.stop_reason, position)
                elif now - self.stop_at >= 5:
                    self._fail(
                        game,
                        "Native cancellation is not confirmed: " + self.stop_reason,
                        position,
                        status="stop_failed",
                    )
                return
            if now - self.started < 2:  # Allow asynchronous command dispatch.
                return
            try:
                pos = tuple(float(p) for p in position.split(","))
                if len(pos) != 3 or not all(math.isfinite(p) for p in pos):
                    return
            except (ValueError, AttributeError):
                return
            goal = (self.target[0] + 0.5, self.target[1], self.target[2] + 0.5)
            if not busy:
                if (
                    math.floor(pos[0]) == self.target[0]
                    and math.floor(pos[2]) == self.target[2]
                    and abs(pos[1] - goal[1]) < 0.6
                ):
                    self._publish(game, "arrived", position=position)
                    self.target = None
                    self.purpose = None
                else:
                    self._fail(
                        game,
                        "Native task ended before reaching the requested standing cell",
                        position,
                    )
                return
            # Do not stop a task replaced through the game UI or a different controller.
            main_goal = re.search(
                r"Main task: <Getting to block [^\n]*?x=(-?\d+), y=(-?\d+), z=(-?\d+)",
                task_chain,
            )
            if not main_goal or tuple(map(int, main_goal.groups())) != self.target:
                self.target = None
                self._publish(game, "ownership_lost", position=position)
                return
            # Use horizontal distance. Vertical jumps near a goal
            # must not reset the timer; circling at equal distance does not either.
            distance = math.hypot(pos[0] - goal[0], pos[2] - goal[2])
            if distance < self.best - 0.75:
                self.best = distance
                self.progress_at = now
            if (
                now - self.progress_at < self.STALL_SECONDS
                and now - self.started < self.MAX_SECONDS
            ):
                return
            self.stop_reason = (
                "No net approach for 30 seconds (jumping/circling is not progress)"
                if now - self.progress_at >= self.STALL_SECONDS
                else "Route exceeded 180 seconds"
            )
            # Exactly one cancellation request; observe idle on later snapshots.
            self.stopping = True
            self.stop_at = now
            self._publish(game, "stopping", reason=self.stop_reason, position=position)
            try:
                result = _java_to_python(entry.stopPathing())
                if not result.get("ok"):
                    self._fail(
                        game,
                        "Native stop rejected: " + self.stop_reason,
                        position,
                        status="stop_failed",
                    )
            except (Py4JError, OSError, ValueError, TypeError) as exc:
                self._fail(
                    game,
                    "Native stop result unknown: " + str(exc),
                    position,
                    status="stop_failed",
                )
