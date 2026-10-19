"""ActivityTracker — sliding-window rate tracking for event classification.

Classifies current activity as NO_ACTIVITY / LOW_ACTIVITY / HIGH_ACTIVITY
based on event rates per category over a 1-minute window.
Thread-safe under CPython GIL (deque ops + float assignment are atomic).
"""

import time
from collections import deque
from typing import Literal

from data_schema.chat_structures import CtxEventBase

ActivityLevel = Literal["NO_ACTIVITY", "LOW_ACTIVITY", "HIGH_ACTIVITY"]

# Flood thresholds (events per minute)
PLAYER_CHAT_FLOOD = 10
SERVER_MSG_FLOOD = 10
DAMAGE_FLOOD = 20

# Idle thresholds (seconds)
IDLE_THRESHOLD = 120.0
IDLE_SELF_ACTIVATE = 150.0  # 120 + 30s grace


class ActivityTracker:
    WINDOW = 60.0  # sliding window in seconds

    def __init__(self):
        self._player_chat_ts: deque = deque()
        self._server_msg_ts: deque = deque()
        self._damage_ts: deque = deque()
        self._last_any_event: float = time.time()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, event: CtxEventBase) -> None:
        """Record an incoming event. Skip self-generated events."""
        if event.additional_fields.get("self", False):
            return

        now = time.time()
        self._last_any_event = now

        if event.type == "chat":
            if event.user:
                self._player_chat_ts.append(now)
            else:
                self._server_msg_ts.append(now)
        elif event.type == "mc_damaged":
            self._damage_ts.append(now)
        # death/kill are rare — no rate tracking needed

    def reset_idle(self) -> None:
        """Reset idle timer (e.g. after self-activation)."""
        self._last_any_event = time.time()

    # ------------------------------------------------------------------
    # Rates
    # ------------------------------------------------------------------

    @staticmethod
    def _prune(q: deque) -> int:
        cutoff = time.time() - ActivityTracker.WINDOW
        while q and q[0] < cutoff:
            q.popleft()
        return len(q)

    @property
    def player_chat_rate(self) -> int:
        return self._prune(self._player_chat_ts)

    @property
    def server_msg_rate(self) -> int:
        return self._prune(self._server_msg_ts)

    @property
    def damage_rate(self) -> int:
        return self._prune(self._damage_ts)

    @property
    def idle_seconds(self) -> float:
        return time.time() - self._last_any_event

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    @property
    def level(self) -> ActivityLevel:
        if self.idle_seconds >= IDLE_THRESHOLD:
            return "NO_ACTIVITY"
        if self.player_chat_rate > PLAYER_CHAT_FLOOD:
            return "HIGH_ACTIVITY"
        return "LOW_ACTIVITY"

    def is_flood(self, category: str) -> bool:
        if category == "player_chat":
            return self.player_chat_rate > PLAYER_CHAT_FLOOD
        if category == "server_msg":
            return self.server_msg_rate > SERVER_MSG_FLOOD
        if category == "damage":
            return self.damage_rate > DAMAGE_FLOOD
        return False

    # ------------------------------------------------------------------
    # Prompt context
    # ------------------------------------------------------------------

    def to_prompt_string(self) -> str:
        lvl = self.level
        parts = [f"Activity: {lvl}"]
        if lvl == "NO_ACTIVITY":
            parts.append(f"idle {self.idle_seconds:.0f}s")
        else:
            if self.player_chat_rate > 0:
                parts.append(f"chat:{self.player_chat_rate}/min")
            if self.server_msg_rate > 0:
                parts.append(f"server:{self.server_msg_rate}/min")
            if self.damage_rate > 0:
                parts.append(f"dmg:{self.damage_rate}/min")
        return " | ".join(parts)
