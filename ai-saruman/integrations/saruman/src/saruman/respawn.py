"""Observe death and request a bounded native respawn independently of the LLM."""

import math
import time
from copy import deepcopy
from uuid import uuid4

from py4j.protocol import Py4JError

from saruman.minecraft import _java_to_python


class RespawnController:
    POLL_SECONDS = 0.5
    DEATH_DELAY = 2
    RETRY_SECONDS = 5
    MAX_ATTEMPTS = 3
    RETRY_BATCH_SECONDS = 60

    def __init__(self, hardcore, clock=time.monotonic):
        self.hardcore, self.clock = hardcore, clock
        self.next_poll = 0
        self.ready = False
        self.self_state = {}
        self.death = None
        self.attempts = 0
        self.next_attempt = 0
        self.stopped = False
        self.stop_attempted = False
        self.was_enabled = False
        self.retry_batch_at = None

    def _publish(self, game, status, **fields):
        previous = game.get("respawn", {})
        value = {
            "status": status,
            "attempts": self.attempts,
            **(self.death or {}),
            **fields,
        }
        if dict(previous.items()) != value:
            game["respawn"] = value
            print(f"[RESPAWN] {status} (attempts={self.attempts})")

    def tick(self, entry, game, navigation, *, enabled):
        now = self.clock()
        if now < self.next_poll:
            return self.ready
        self.next_poll = now + self.POLL_SECONDS
        self.ready = False
        try:
            snapshot = _java_to_python(entry.getGameState())
            self.self_state = (
                snapshot.get("self", {}) if isinstance(snapshot, dict) else {}
            )
            if not isinstance(self.self_state, dict):
                self.self_state = {}
            hp = self.self_state.get("hp")
            if (
                isinstance(hp, bool)
                or not isinstance(hp, (float, int))
                or not math.isfinite(hp)
            ):
                game["alive"] = None
                self._publish(
                    game, "unavailable", reason="No current player health observation"
                )
                return False
            if hp > 0:
                if self.death is not None:
                    # A queued stop is not confirmation that the old task ended.
                    if not self.stopped:
                        if not enabled:
                            if entry.hasActiveTask():
                                self._publish(game, "paused")
                                return False
                            navigation.after_death(game)
                            self.stopped = True
                        elif not self._stop_old_task(entry, game, navigation):
                            self._publish(game, "stopping_old_task")
                            return False
                    if entry.hasActiveTask():
                        game["alive"] = False
                        self._publish(game, "stopping_old_task")
                        return False
                    self._publish(
                        game, "respawned", position=self.self_state.get("pos")
                    )
                    self.death = None
                elif game.get("respawn", {}).get("status") != "respawned":
                    self._publish(game, "alive")
                game["alive"] = True
                self.ready = True
                return True

            game["alive"] = False
            if self.death is None:
                game["combat_threats"] = {}
                game["self_defense"] = {}
                game["scepter_combat"] = {}
                self.death = {
                    "death_id": uuid4().hex,
                    "death_position": self.self_state.get("pos"),
                    "dimension": self.self_state.get("dimension"),
                }
                self.attempts, self.stopped = 0, False
                self.stop_attempted = False
                self.retry_batch_at = None
                self.next_attempt = now + self.DEATH_DELAY
                task = game.get("tree_task", {})
                if task.get("status") == "active":
                    saved = deepcopy(dict(task.items()))
                    saved.update(
                        status="paused",
                        pause_reason="Player died; verify inventory and world before resuming",
                    )
                    game["tree_task"] = saved
                self._publish(game, "dead")
            if not enabled:
                self.was_enabled = False
                self._publish(game, "paused")
                return False
            if not self.was_enabled:
                self.attempts = 0
                self.retry_batch_at = None
            self.was_enabled = True
            if not self.stopped:
                # A dead player's scheduler may never report idle. Attempt one
                # cancellation, but do not make respawning depend on its success.
                try:
                    self._stop_old_task(entry, game, navigation)
                except (Py4JError, OSError, ValueError, TypeError) as error:
                    self._publish(game, "dead", stop_error=type(error).__name__)
            if self.hardcore():
                self._publish(
                    game,
                    "blocked",
                    reason="Hardcore world does not allow normal respawn",
                )
                return False
            if now < self.next_attempt:
                self._publish(game, "respawn_requested" if self.attempts else "dead")
                return False
            if self.attempts >= self.MAX_ATTEMPTS:
                if self.retry_batch_at is None:
                    self.retry_batch_at = now + self.RETRY_BATCH_SECONDS
                if now < self.retry_batch_at:
                    self._publish(
                        game,
                        "retry_wait",
                        retry_in=round(self.retry_batch_at - now),
                        reason="No living player observed; retry after cooldown",
                    )
                    return False
                self.attempts, self.retry_batch_at = 0, None
            self.attempts += 1
            self.next_attempt = now + self.RETRY_SECONDS
            accepted = bool(
                entry.respawnPlayer()
            )  # This API dispatches on the client thread.
            self._publish(game, "respawn_requested", accepted=accepted)
        except (Py4JError, OSError, ValueError, TypeError) as error:
            game["alive"] = None
            self._publish(
                game,
                "unavailable",
                reason=f"Minecraft observation/request failed: {type(error).__name__}",
            )
        return False

    def _stop_old_task(self, entry, game, navigation):
        if not self.stop_attempted:
            self.stop_attempted = True  # Never replay a stop with an unknown result.
            navigation.after_death(game)
            result = _java_to_python(entry.stopPathing())
            self.stopped = isinstance(result, dict) and bool(result.get("ok"))
        if not self.stopped:
            self.stopped = not entry.hasActiveTask()
        return self.stopped
