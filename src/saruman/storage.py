"""Local test conversations and measured usage, stored in SQLite."""
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
import uuid
from pathlib import Path

TABLES = ("sessions", "messages", "runs")
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, run_id TEXT);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    created_at TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER,
                    reasoning_tokens INTEGER, total_tokens INTEGER, context_window INTEGER,
                    latency_ms INTEGER, persona_input_est INTEGER, memory_input_est INTEGER,
                    history_input_est INTEGER, message_input_est INTEGER, memory_output_est INTEGER,
                    persona_sha256 TEXT, remembered INTEGER DEFAULT 0, error TEXT);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_session(self):
        session = {"id": uuid.uuid4().hex, "title": "Новый диалог", "created_at": now()}
        with self.db() as db:
            db.execute("INSERT INTO sessions VALUES (:id, :title, :created_at)", session)
        return session

    def sessions(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("""
                SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) message_count
                FROM sessions s ORDER BY created_at DESC
            """)]

    def messages(self, session_id):
        with self.db() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise ValueError("Диалог не найден.")
            return [dict(row) for row in db.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))]

    def begin(self, session_id, text, model, metrics):
        self.messages(session_id)  # Validate the session before saving anything.
        run_id = uuid.uuid4().hex
        with self.db() as db:
            db.execute("""INSERT INTO runs (id, session_id, created_at, model, status,
                       persona_input_est, memory_input_est, history_input_est,
                       message_input_est, persona_sha256)
                       VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)""",
                       (run_id, session_id, now(), model, metrics["persona_input_est"],
                        metrics["memory_input_est"], metrics["history_input_est"],
                        metrics["message_input_est"], metrics["persona_sha256"]))
            count = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]
            if not count:
                db.execute("UPDATE sessions SET title=? WHERE id=?", (text[:60], session_id))
            db.execute("INSERT INTO messages (session_id, role, content, created_at, run_id) VALUES (?, 'user', ?, ?, ?)",
                       (session_id, text, now(), run_id))
        return run_id

    def finish(self, run_id, session_id, reply, trace, remembered=0, memory_output_est=0, error=None):
        usage = trace.get("usage") or {}
        with self.db() as db:
            db.execute("""UPDATE runs SET status=?, model=?, input_tokens=?, cached_input_tokens=?,
                output_tokens=?, reasoning_tokens=?, total_tokens=?, context_window=?, latency_ms=?,
                remembered=?, memory_output_est=?, error=? WHERE id=?""",
                ("failed" if reply is None else "completed", trace["model"],
                 usage.get("inputTokens"), usage.get("cachedInputTokens"), usage.get("outputTokens"),
                 usage.get("reasoningOutputTokens"), usage.get("totalTokens"),
                 trace.get("context_window"), trace.get("latency_ms"), remembered,
                 memory_output_est, error, run_id))
            if reply is not None:
                db.execute("INSERT INTO messages (session_id, role, content, created_at, run_id) VALUES (?, 'assistant', ?, ?, ?)",
                           (session_id, reply, now(), run_id))

    def runs(self):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY created_at DESC")]

    def analytics(self):
        runs = self.runs()
        measured = [row for row in runs if row["total_tokens"] is not None]
        daily = {}
        for row in measured:
            if row["input_tokens"] is None or row["output_tokens"] is None:
                continue
            day = row["created_at"][:10]
            bucket = daily.setdefault(day, {"day": day, "input": 0, "output": 0})
            bucket["input"] += row["input_tokens"] or 0
            bucket["output"] += row["output_tokens"] or 0
        return {"runs": runs, "request_count": len(runs), "measured_count": len(measured),
                "totals": {key: sum(row[key] for row in runs if row[key] is not None)
                           if any(row[key] is not None for row in runs) else None
                           for key in USAGE_FIELDS},
                "daily": sorted(daily.values(), key=lambda row: row["day"]),
                "money": None,
                "money_note": "Подписка: провайдер не сообщает денежную цену отдельного ответа.",
                "estimate_note": "Оценки частей контекста: число символов / 4, не биллинговая токенизация."}

    def table(self, name):
        if name not in TABLES:
            raise ValueError("Неизвестная таблица.")
        with self.db() as db:
            columns = [row["name"] for row in db.execute(f"PRAGMA table_info({name})")]
            rows = [dict(row) for row in db.execute(f"SELECT * FROM {name} ORDER BY rowid DESC LIMIT 200")]
            count = db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        return {"columns": columns, "rows": rows, "total": count}
