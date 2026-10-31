"""Cached status-specific speech; authored lore is never evidence of game progress."""

import json
import random
import time
from pathlib import Path

CATALOGUE = (
    Path(__file__).resolve().parents[2]
    / "resources/Characters/Saruman/action_speech.json"
)


def seed_records():
    data = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    records = []
    for event, phrases in data["phrases"].items():
        for index, text in enumerate(phrases):
            records.append(
                {
                    "id": f"{event}.{index + 1}",
                    "kind": "phrase",
                    "event": event,
                    "text": text,
                    "enabled": True,
                }
            )
    for index, entry in enumerate(data["lore"]):
        text = entry["text"] if isinstance(entry, dict) else entry
        topic = entry.get("topic") if isinstance(entry, dict) else None
        records.append(
            {
                "id": f"lore.{index + 1}",
                "kind": "authored_backstory",
                "event": "lore." + topic if topic else "lore",
                "text": text,
                "enabled": True,
            }
        )
    return records


def backstory_prompt(records=None, limit=24):
    """A balanced, static selection for the cached persona, without another LLM call."""
    groups = {}
    for row in seed_records() if records is None else records:
        if row.get("kind") == "authored_backstory" and row.get("enabled", True):
            groups.setdefault(row.get("event", "lore"), []).append(row["text"])
    chosen = []
    while any(groups.values()) and len(chosen) < limit:
        for values in groups.values():
            if values and len(chosen) < limit:
                chosen.append(values.pop(0))
    if not chosen:
        return ""
    return (
        "\n\n## Authorized character prehistory\n"
        "These are fictional personal memories of this Saruman, not Tolkien canon, "
        "current-server events or gameplay evidence. Keep their details consistent. "
        "Use a brief relevant detail occasionally; do not recite this list or append "
        "a memory to every reply. Player questions and actual observations come first.\n"
        + "\n".join("- " + text for text in chosen)
    )


class ActionSpeech:
    def __init__(self, records=None, rng=None, clock=time.monotonic):
        self.rng = rng or random.Random()
        self.clock = clock
        self.recent = []
        self.last_at = float("-inf")
        self.last_event = None
        self.count = 0
        self.next_lore_at = clock() + 60
        self.source = "local_fallback"
        self.replace(seed_records() if records is None else records)

    def replace(self, records):
        self.records = [
            dict(r) for r in records if r.get("enabled", True) and r.get("text")
        ]

    def snapshot(self):
        by_id = {r["id"]: r for r in self.records}
        return {
            "source": self.source,
            "phrases": sum(r["kind"] == "phrase" for r in self.records),
            "authored_backstory": sum(
                r["kind"] == "authored_backstory" for r in self.records
            ),
            "recent": [by_id[key] for key in self.recent[-6:] if key in by_id],
            "note": "Authored prehistory is roleplay, never evidence of events in the current world.",
        }

    def _choose(self, kind, event):
        events = {event} if isinstance(event, str) else set(event)
        rows = [r for r in self.records if r["kind"] == kind and r["event"] in events]
        choices = [r for r in rows if r["id"] not in self.recent]
        # Exhausted small categories restart only after their least recent entry.
        if not choices and rows:
            choices = [min(rows, key=lambda r: self.recent.index(r["id"]))]
        if not choices:
            return None
        chosen = self.rng.choice(choices)
        if chosen["id"] in self.recent:
            self.recent.remove(chosen["id"])
        self.recent = (self.recent + [chosen["id"]])[-32:]
        return chosen["text"]

    def say(self, status, activity=None):
        event = (
            "plant_sapling" if status == "planted" and activity == "trees" else status
        )
        battle = {"attacked", "killed"}
        urgent = event in battle and self.last_event not in battle
        now = self.clock()
        if now - self.last_at < (12 if event in battle else 30) and not urgent:
            return None
        text = self._choose("phrase", event)
        if not text:
            return None
        self.last_at = now
        self.last_event = event
        self.count += 1
        # Relevance and a separate time budget, rather than every fifth action.
        topic = {
            "planted": "provisions",
            "plant_sapling": "trees",
            "harvested": "provisions",
            "tilled": "provisions",
            "needs_move": "travel",
        }.get(event)
        if topic and now >= self.next_lore_at:
            self.next_lore_at = now + 30
            if self.rng.random() < 0.45:
                lore = self._choose("authored_backstory", ("lore." + topic, "lore"))
                if lore:
                    text += " " + lore
                    self.next_lore_at = now + self.rng.uniform(90, 180)
        return text
