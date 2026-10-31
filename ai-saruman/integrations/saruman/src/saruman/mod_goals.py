"""Cross-process durable intentions. Acceptance is never reported as completion."""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TERMINAL = {"completed", "failed", "cancelled", "uncertain"}
LEGACY_MOD_TOOLS = frozenset(
    {
        "grow_tree",
        "manage_trees",
        "plant_sapling",
        "apply_bone_meal",
        "find_tree_planting_spots",
        "get_minecraft_procedure",
        "tend_trees",
        "hunt_hostiles",
        "attack_hostile",
        "defend_self",
        "ensure_scepter",
        "cast_scepter",
        "farm_crops",
        "explore_nearby",
        "eat_food",
    }
)


def using_mods(game):
    return game.get("automation_mode") == "mods"


class GoalStore:
    def __init__(self, path=None):
        self.path = Path(
            path or os.getenv("SARUMAN_GOALS_DB", ROOT / "data/automation.sqlite")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, request TEXT UNIQUE, source TEXT, priority INTEGER, state TEXT, created REAL, updated REAL, body TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, body TEXT)"
            )

    @contextmanager
    def connection(self):
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            yield db

    def submit(self, kind, *, source, request_id=None, count=1, command=None):
        if source not in {"player", "operator", "planner"}:
            raise ValueError("Unknown goal source")
        if kind not in {"trees", "command"} or source == "planner" and kind != "trees":
            raise ValueError("Planner may only submit native mod skills")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= 100
        ):
            raise ValueError("count must be an integer from 1 to 100")
        if kind == "command" and (
            not isinstance(command, str)
            or not command.startswith(("@", "/"))
            or any(c in command for c in ("\n", "\r", ";"))
        ):
            raise ValueError("Expected one Minecraft command")
        now, goal_id = time.time(), uuid.uuid4().hex
        body = {
            "id": goal_id,
            "kind": kind,
            "source": source,
            "count": count,
            "completed": 0,
            "command": command,
            "run_id": None,
            "world": None,
            "state": "queued",
            "reason": None,
            "created": now,
            "updated": now,
            "context_id": request_id.split(":", 1)[0] if request_id else None,
        }
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO goals VALUES (?,?,?,?,?,?,?,?)",
                (
                    goal_id,
                    request_id or goal_id,
                    source,
                    30 if source == "planner" else 10,
                    "queued",
                    now,
                    now,
                    json.dumps(body),
                ),
            )
            row = db.execute(
                "SELECT body FROM goals WHERE request=?", (request_id or goal_id,)
            ).fetchone()
        return json.loads(row[0])

    def list(self, active=False, limit=100):
        with self.connection() as db:
            rows = db.execute(
                "SELECT body FROM goals "
                + (
                    "WHERE state NOT IN ('completed','failed','cancelled','uncertain') "
                    if active
                    else ""
                )
                + (
                    "ORDER BY priority, created LIMIT ?"
                    if active
                    else "ORDER BY CASE WHEN state IN ('completed','failed','cancelled','uncertain') THEN 1 ELSE 0 END, priority, CASE WHEN state IN ('completed','failed','cancelled','uncertain') THEN -updated ELSE created END LIMIT ?"
                ),
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, goal, **changes):
        # Cancellation wins over a delayed observation from the bridge.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM goals WHERE id=?", (goal["id"],)
            ).fetchone()
            body = json.loads(row[0])
            if (
                body["state"] in {"cancelled", "cancel_requested"}
                and changes.get("state") != "cancelled"
            ):
                return body
            body.update(changes, updated=time.time())
            db.execute(
                "UPDATE goals SET state=?,updated=?,body=? WHERE id=?",
                (body["state"], body["updated"], json.dumps(body), body["id"]),
            )
        return body

    def cancel(self, goal_id=None, source=None):
        for goal in self.list(active=True, limit=-1):
            if (goal_id is None or goal["id"] == goal_id) and (
                source is None or goal["source"] == source
            ):
                self.update(
                    goal,
                    state="cancel_requested",
                    reason="Cancelled by operator/player",
                )

    def set_meta(self, key, value):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value))
            )

    def meta(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT body FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def lease(self, owner, now, ttl=5):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM meta WHERE key='runtime_lease'"
            ).fetchone()
            previous = json.loads(row[0]) if row else {}
            if previous.get("until", 0) > now and previous.get("owner") != owner:
                return False
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('runtime_lease',?)",
                (json.dumps({"owner": owner, "until": now + ttl}),),
            )
            return True


def player_context(game):
    value = game.get("mod_player_context", {})
    return dict(value.items())


def submit_command(game, command, request_token=None):
    context = player_context(game)
    if not context.get("id") or context.get("until", 0) < time.time():
        return {
            "status": "rejected",
            "reason": "Commands require a current player/operator request",
        }
    if command.lstrip("@").strip() in {"stop", "cancel"}:
        GoalStore().cancel()
        return {"status": "cancel_requested"}
    from saruman.peaceful import guard_command

    guard_command(command)
    result = GoalStore().submit(
        "command",
        source=context.get("source", "player"),
        command=command,
        request_id=f"{context['id']}:command:{request_token or command}",
    )
    return {"status": "accepted", "goal": result}
