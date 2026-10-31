"""Progress and bounded cancellation for explicitly dispatched native @get work."""

import math
import re
import time

from py4j.protocol import Py4JError

from saruman.minecraft import _java_to_python


def read_user_task(gateway):
    """Read the user root, which survives scheduler preemption by safety chains."""
    try:
        root = (
            gateway.jvm.adris.altoclef.AltoClef.getInstance()
            .getUserTaskChain()
            .getCurrentTask()
        )
        if root is None:
            return {}
        return {
            "id": int(gateway.jvm.java.lang.System.identityHashCode(root)),
            "text": str(root),
        }
    except (Py4JError, OSError, ValueError, TypeError):
        return None  # Older/unavailable API: absence of evidence is not replacement.


class CollectionGuard:
    STALL_SECONDS = 120
    MAX_SECONDS = 900
    OWNERSHIP_GRACE_SECONDS = 30

    def __init__(self):
        self.goal = None

    def publish(self, game, status, **fields):
        game["collection"] = {
            "event_id": time.time_ns(),
            "observed_at": time.monotonic(),
            "status": status,
            "goal": self.goal,
            **fields,
        }

    def forget(self, game, **fields):
        if self.goal is not None:
            self.publish(game, "ownership_lost", **fields)
        self.goal = None

    def start(self, entry, command, game):
        match = re.fullmatch(r"@get ([a-z0-9_:]+) (\d+)", command)
        if not match:
            return False
        goal = {"item": match[1].removeprefix("minecraft:"), "count": int(match[2])}
        if goal == self.goal:
            raise ValueError("This collection is already running; do not restart it")
        entry.RunInnerCommand(command)
        self.goal = goal
        self.started = self.progress_at = time.monotonic()
        self.next_inventory = 0
        self.best_counts = {}
        self.visited = set()
        self.stopping = False
        self.root_id = None
        self.unconfirmed_at = None
        self.preempted = False
        self.publish(game, "running", elapsed=0)
        return True

    def tick(self, entry, game, position, busy, task_chain, state=None, user_task=None):
        if self.goal is None:
            return
        now = time.monotonic()
        if self.stopping:
            if not busy:
                self.publish(
                    game, "blocked", reason=self.stop_reason, native_stopped=True
                )
                self.goal = None
            elif now - self.stop_at >= 5:
                self.publish(
                    game, "stop_failed", reason=self.stop_reason, native_stopped=False
                )
                self.goal = None
            return
        if now - self.started < 2:
            return
        if not busy:
            self.publish(
                game,
                "finished",
                elapsed=round(now - self.started),
                hint="Native work ended; verify actual inventory before resuming the saved task",
            )
            self.goal = None
            return
        main = re.search(r"Main task: ([^\n]+)", task_chain)
        item_pattern = (
            r"(?<![a-z0-9_])" + re.escape(self.goal["item"]) + r"(?![a-z0-9_])"
        )
        current_matches = bool(main and re.search(item_pattern, main[1]))
        evidence = {
            "task_chain": task_chain,
            "user_task": user_task,
            "elapsed": round(now - self.started),
        }
        if user_task is not None:
            token = user_task.get("id")
            if self.root_id is not None and token is not None and token != self.root_id:
                self.forget(game, reason="User task identity changed", **evidence)
                return
            owned = token is not None and (
                token == self.root_id
                or re.search(item_pattern, user_task.get("text", ""))
            )
            if owned:
                self.root_id = token
        else:
            owned = current_matches and self.root_id is None
        if not owned:
            if self.unconfirmed_at is None:
                self.unconfirmed_at = now
            expired = (
                now - self.unconfirmed_at >= self.OWNERSHIP_GRACE_SECONDS
                or now - self.started >= self.MAX_SECONDS
            )
            self.publish(
                game,
                "ownership_unconfirmed" if expired else "observing",
                reason="Cannot confirm the user collection root; no cancellation issued",
                **evidence,
            )
            if expired:
                self.goal = None
            return
        self.unconfirmed_at = None
        if not current_matches:
            self.preempted = True
            expired = now - self.started >= self.MAX_SECONDS
            self.publish(
                game,
                "ownership_unconfirmed" if expired else "suspended",
                reason="User collection still exists, but another scheduler chain is selected; no cancellation issued",
                **evidence,
            )
            if expired:
                self.goal = None
            return
        if self.preempted:
            self.progress_at = (
                now  # Give resumed work a stall window; never reset the absolute limit.
            )
            self.preempted = False
        try:
            cell = tuple(math.floor(float(p) / 2) for p in position.split(","))
            if len(cell) == 3 and cell not in self.visited:
                self.visited.add(cell)
                self.progress_at = now
        except (ValueError, AttributeError, OverflowError):
            pass
        break_age = (state or {}).get("msSinceBreakProgress")
        if isinstance(break_age, (int, float)) and 0 <= break_age < 2000:
            self.progress_at = now
        if now >= self.next_inventory:
            try:
                inventory = _java_to_python(entry.getInventoryFull())
            except (Py4JError, OSError, ValueError, TypeError):
                inventory = {}  # Missing data is not progress; absolute/stall limits still apply.
            if inventory.get("ok"):
                counts = {}
                for slot in inventory.get("slots", []):
                    item = slot.get("item")
                    counts[item] = counts.get(item, 0) + slot.get("count", 0)
                if any(n > self.best_counts.get(item, 0) for item, n in counts.items()):
                    self.progress_at = now
                for item, n in counts.items():
                    self.best_counts[item] = max(n, self.best_counts.get(item, 0))
            self.next_inventory = now + 2
        elapsed, stalled = now - self.started, now - self.progress_at
        if elapsed < self.MAX_SECONDS and stalled < self.STALL_SECONDS:
            self.publish(
                game,
                "running",
                elapsed=round(elapsed),
                stalled_seconds=round(stalled),
                user_task=user_task,
            )
            return
        self.stop_reason = (
            "Collection reached its 15 minute limit"
            if elapsed >= self.MAX_SECONDS
            else "Collection made no new spatial/inventory/mining progress for 120 seconds"
        )
        self.stopping, self.stop_at = True, now
        self.publish(game, "stopping", reason=self.stop_reason, native_stopped=False)
        try:
            result = _java_to_python(entry.stopPathing())
            if not result.get("ok"):
                self.publish(
                    game, "stop_failed", reason=self.stop_reason, native_stopped=False
                )
                self.goal = None
        except (Py4JError, OSError, ValueError, TypeError) as exc:
            self.publish(
                game,
                "stop_failed",
                reason=f"Stop result unknown: {exc}",
                native_stopped=False,
            )
            self.goal = None
