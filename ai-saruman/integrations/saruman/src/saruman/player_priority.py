"""FIFO of validated player messages, ahead of autonomous background work."""

import time


def is_player_message(event):
    return (
        event.env == "minecraft"
        and event.type in ("chat", "voice")
        and bool(event.user)
        and event.user != "system"
        and not event.additional_fields.get("self", False)
        and (event.additional_fields.get("filter_results") or {}).get(
            "acceptable", True
        )
    )


class PlayerInbox:
    def __init__(self, since=0):
        self.since = since
        self.seen = set()
        self.handled = set()
        self.pending = []
        self.active = None

    def collect(self, events):
        for event in sorted(events, key=lambda event: event.processing_timestamp):
            token = event.processing_timestamp
            if (
                token > self.since
                and token not in self.seen
                and is_player_message(event)
            ):
                self.seen.add(token)
                self.pending.append(event)
        if len(self.seen) > 1000:
            recent = sorted(self.seen)[-500:]
            self.since = max(self.since, recent[0] - 1)
            self.seen = set(recent)
            self.handled.intersection_update(self.seen)

    def next(self):
        return self.pending.pop(0) if self.pending else None

    def publish(self, game, *, status=None, awaiting_stop=None):
        previous = game.get("player_priority", {})
        game["player_priority"] = {
            "status": status or previous.get("status", "waiting"),
            "active": {
                "user": self.active.user,
                "text": self.active.msg,
                "timestamp": self.active.processing_timestamp,
            }
            if self.active
            else None,
            "queue": [
                {"user": e.user, "text": e.msg, "timestamp": e.processing_timestamp}
                for e in self.pending
            ],
            "awaiting_stop": previous.get("awaiting_stop", False)
            if awaiting_stop is None
            else awaiting_stop,
            "updated_at": time.time(),
            "intent": max(self.handled, default=previous.get("intent")),
        }
