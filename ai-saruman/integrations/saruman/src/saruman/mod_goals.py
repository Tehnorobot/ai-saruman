"""Cross-process durable intentions. Acceptance is never reported as completion."""

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from saruman.role_settings import pid_alive
from saruman.activity import emit

ROOT = Path(__file__).resolve().parents[2]
TERMINAL = {"completed", "failed", "cancelled", "uncertain"}


def public_chat_text(message):
    """One literal player-chat message; never a command or a command chain."""
    if not isinstance(message, str):
        raise ValueError("Expected public chat text")
    text = message.strip()
    if (not text or text.startswith(("/", "@", "!", "$"))
            or any(ord(char) < 32 for char in message)
            or len(text.encode("utf-16-le")) // 2 > 256):
        raise ValueError("Expected one plain chat message, up to 256 UTF-16 units, without commands")
    return text
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
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, request TEXT UNIQUE, source TEXT, priority INTEGER, state TEXT, created REAL, updated REAL, body TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, body TEXT)"
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def submit(self, kind, *, source, request_id=None, count=1, command=None, target=None, message=None, world=None, topic=None, include_photo=False):
        if source not in {"player", "operator", "planner"}:
            raise ValueError("Unknown goal source")
        if kind not in {"trees", "command", "follow", "chat", "telegram"} or source == "planner" and kind not in {"trees", "follow"}:
            raise ValueError("Planner may only submit native mod skills")
        if kind == "chat":
            message = public_chat_text(message)
            if count != 1 or not isinstance(world, str) or not world:
                raise ValueError("A public chat message requires one message and a current world")
        if kind == 'telegram' and (not isinstance(topic, str) or not topic.strip()
                or type(include_photo) is not bool or not isinstance(world, str) or not world):
            raise ValueError('A Telegram request requires a public topic, photo flag and current world')
        if kind == "follow":
            if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,16}", target):
                raise ValueError("Expected an exact Minecraft player name")
            command = f"@follow {target}"
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
            "target": target,
            "run_id": None,
            "world": None,
            "state": "queued",
            "reason": None,
            "created": now,
            "updated": now,
            "context_id": request_id.split(":", 1)[0] if request_id else None,
        }
        if kind == "chat":
            body.update(message=message, world=world)
        if kind == 'telegram':
            body.update(topic=topic.strip(), include_photo=include_photo, world=world)
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
        result = json.loads(row[0])
        emit('goal.queued', status=result['state'], trace=result.get('context_id'), details={'goal': result}, key='goal:'+result['id'])
        return result

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
            previous = {field: body.get(field) for field in ('state', 'completed', 'reason')}
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
        if any(previous[field] != body.get(field) for field in previous):
            emit('goal.result', status=body['state'], trace=body.get('context_id'), details={'goal': body})
        return body

    def cancel(self, goal_id=None, source=None, *, reason="Cancelled by operator/player"):
        for goal in self.list(limit=-1):
            if goal["state"] in {"completed", "failed", "cancelled"}:
                continue
            if goal['kind'] == 'telegram' and goal_id is None:
                continue  # Pausing a game body does not cancel an authorized publication.
            if (goal_id is None or goal["id"] == goal_id) and (
                source is None or goal["source"] == source
            ):
                self.update(
                    goal,
                    state="cancel_requested",
                    reason=reason,
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
                # Wall-clock rollback must not leave a dead dispatcher holding
                # the queue for hours. Reuse the read-only process check; retain
                # legacy/unknown owners and live owners until their lease expires.
                pid = previous.get("pid")
                if type(pid) is not int or pid <= 0 or pid_alive(pid):
                    return False
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('runtime_lease',?)",
                (json.dumps({"owner": owner, "pid": os.getpid(), "until": now + ttl}),),
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
    follow = re.fullmatch(r"@follow\s+([A-Za-z0-9_]{1,16})", command)
    result = GoalStore().submit(
        "follow" if follow else "command",
        source=context.get("source", "player"),
        command=command,
        target=follow[1] if follow else None,
        request_id=f"{context['id']}:command:{request_token or command}",
    )
    return {"status": "accepted", "goal": result}
