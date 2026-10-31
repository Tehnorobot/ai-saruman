"""Small, topical fictional memories for speech, never game observations."""

from collections import deque

from saruman.action_speech import seed_records

TOPICS = {
    "trees": ("дерев", "сажен", "дуб", "берёз", "берез", "сад", "рощ"),
    "books": ("книг", "знани", "чита", "учён", "учен", "мудр", "библиот"),
    "travel": ("пойд", "иди", "ходи", "пут", "дорог", "гуля", "следуй"),
    "teaching": ("учени", "ошиб", "объясни", "научи"),
    "politics": ("договор", "друг", "спор", "власть", "совет"),
    "craft": ("посох", "маги", "сдела", "ремесл", "мастер"),
    "provisions": ("хлеб", "ед", "голод", "урож", "припас"),
    "solitude": ("один", "тишин", "скуч", "настроен", "молч", "себе", "прошл"),
}


class PersonaContext:
    def __init__(self, records=None):
        self.records = [
            dict(row)
            for row in (seed_records() if records is None else records)
            if row.get("kind") == "authored_backstory"
            and row.get("enabled", True)
            and row.get("text")
        ]
        self.recent = deque(maxlen=12)

    def select(self, event):
        text = str(event.get("original_request", "")).lower()
        goals = event.get("facts", {}).get("goals", [])
        topics = {
            topic for topic, words in TOPICS.items() if any(w in text for w in words)
        }
        if any(g.get("kind") == "trees" for g in goals):
            topics.add("trees")
        preferred = [
            r
            for r in self.records
            if r.get("event", "").removeprefix("lore.") in topics
        ]
        pool = preferred or self.records
        available = [r for r in pool if r["text"] not in self.recent]
        chosen = (available or pool)[:2]
        for row in chosen:
            self.recent.append(row["text"])
        return {
            "kind": "authored_character_backstory",
            "boundary": "Fictional personal past, not Tolkien canon or evidence about this Minecraft world. Never claim these happened with current players.",
            "suggestions": [r["text"] for r in chosen],
            "delivery": "Use one concrete detail when it helps this reply; weave it into your own thought. Do not list cards, quote a database, or force a memory into every response.",
        }
