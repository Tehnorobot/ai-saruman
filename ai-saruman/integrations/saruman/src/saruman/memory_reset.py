"""Schedule a fresh game memory; erase it before any runtime workers start."""

import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESET_FILE = ROOT / "data/game-memory-reset.json"
_LOCK = threading.RLock()


def reset_pending(path=None):
    return Path(path or RESET_FILE).exists()


def request_reset(path=None):
    """Only record the operator's request. No live databases are changed here."""
    marker = Path(path or RESET_FILE)
    with _LOCK:
        if marker.is_symlink():
            raise ValueError("Файл запроса сброса не должен быть ссылкой")
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            with marker.open("x", encoding="utf-8") as handle:
                json.dump(
                    {"version": 1, "phase": "pending", "requested_at": time.time()},
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            pass  # Repeated clicks do not replace an existing request.


def cancel_reset(path=None):
    with _LOCK:
        marker = Path(path or RESET_FILE)
        if marker.is_symlink():
            raise ValueError("Файл запроса сброса не должен быть ссылкой")
        if marker.exists():
            request = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(request, dict) and request.get("phase") == "applying":
                raise ValueError(
                    "Сброс уже начат; нужно завершить его повторным запуском runtime"
                )
        marker.unlink(missing_ok=True)


def _sqlite_path(kind):
    variable, filename = {
        "roles": ("SARUMAN_ROLE_DB", "role-models.sqlite"),
        "goals": ("SARUMAN_GOALS_DB", "automation.sqlite"),
    }[kind]
    return Path(os.getenv(variable, ROOT / "data" / filename))


def _check_other_runtime(client, role_path):
    from data_flow.redis_backend import make_key
    from saruman.role_settings import pid_alive

    raw = client.hget(make_key("dict:env"), "runtime_pid")
    pid = json.loads(raw) if raw else None
    pids = {pid} if isinstance(pid, int) and not isinstance(pid, bool) else set()
    if role_path.exists():
        with sqlite3.connect(role_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            tables = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if {"sessions", "requests"} <= tables:
                pids.update(
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT s.pid FROM sessions s JOIN requests r ON r.session=s.id "
                        "WHERE r.state='running'"
                    )
                )
    if any(pid and pid != os.getpid() and pid_alive(pid) for pid in pids):
        raise RuntimeError(
            "Закройте другой Python runtime Сарумана перед сбросом памяти"
        )


def _clear_postgres(engine):
    from sqlalchemy import delete, func, inspect, select, update

    from data_flow.db.models.mind_variable import MindVariable
    from data_flow.db.models.user import User
    from data_flow.db.models.user_dialog import UserDialog
    from data_flow.db.models.user_nickname import UserNickname

    counts = {}
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        # Delete children first, retaining schema, authored stories and voice lines.
        for model in (UserDialog, UserNickname, User):
            if model.__tablename__ in tables:
                counts[model.__tablename__] = connection.scalar(
                    select(func.count()).select_from(model)
                )
                connection.execute(delete(model))
        if MindVariable.__tablename__ in tables:
            connection.execute(update(MindVariable).values(mood=0.0))
    return counts


def _clear_sqlite(path, tables):
    if not path.exists():
        return {}
    counts = {}
    with sqlite3.connect(
        path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5
    ) as db:
        db.execute("BEGIN IMMEDIATE")
        available = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in tables:  # Fixed names supplied by this module, never user SQL.
            if table in available:
                counts[table] = db.execute(f'DELETE FROM "{table}"').rowcount
    return counts


def apply_pending_reset(*, path=None, engine=None, redis_client=None):
    """Startup only, before RedisCtxSwarm/checkpoint restore and collectors.

    Cross-store cleanup is idempotent. On failure the request remains and startup
    must stop; a retry cannot run old memories in a partly cleared environment.
    """
    marker = Path(path or RESET_FILE)
    with _LOCK:
        if not marker.exists():
            return None
        if marker.is_symlink():
            raise ValueError("Файл запроса сброса не должен быть ссылкой")
        request = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or request.get("version") != 1:
            raise ValueError("Некорректный запрос сброса памяти; запуск остановлен")

        from data_flow.redis_backend import get_redis, make_key

        client = redis_client if redis_client is not None else get_redis()
        client.ping()
        role_path, goal_path = _sqlite_path("roles"), _sqlite_path("goals")
        _check_other_runtime(client, role_path)
        owned_engine = engine is None
        if owned_engine:
            from sqlalchemy import create_engine

            from data_flow.database import DB_URI

            engine = create_engine(DB_URI)
        try:
            # Once any deletion can start, cancellation can no longer undo it.
            request["phase"] = "applying"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=marker.parent,
                    prefix=".memory-reset-",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    json.dump(request, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(marker)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            counts = _clear_postgres(engine)
            counts.update(_clear_sqlite(role_path, ("intents",)))
            counts.update(_clear_sqlite(goal_path, ("goals", "meta")))
            # This character's namespace only; never FLUSHDB or another app's data.
            removed, batch = 0, []
            for key in client.scan_iter(match=make_key("*"), count=200):
                batch.append(key)
                if len(batch) >= 128:
                    removed += client.delete(*batch)
                    batch = []
            if batch:
                removed += client.delete(*batch)
            counts["redis_keys"] = removed
            marker.unlink()  # Retain the request until every store has succeeded.
            return counts
        finally:
            if owned_engine:
                engine.dispose()


class MemoryResetPanel:
    def __init__(self, context, path=None):
        self.context, self.path = context, path

    def status(self):
        if reset_pending(self.path):
            return (
                "Сброс запланирован. Закройте Python runtime через Ctrl+C и запустите заново. "
                "Память будет очищена до запуска персонажа. Start до перезапуска недоступен."
            )
        return "Сброс не запланирован."

    def schedule(self):
        game, enabled = self.context()
        if enabled:
            return "Сначала нажмите Stop."
        if game.get("mod_runtime", {}).get("native_busy"):
            return "Дождитесь остановки текущей игровой задачи после Stop."
        try:
            request_reset(self.path)
        except (OSError, ValueError):
            return "Не удалось сохранить запрос сброса памяти."
        return self.status()

    def cancel(self):
        try:
            cancel_reset(self.path)
        except (OSError, ValueError):
            return "Не удалось отменить запрос сброса памяти."
        return self.status()


def add_memory_reset_panel(context):
    import gradio as gr

    panel = MemoryResetPanel(context)
    with gr.Accordion("Игровая память · начать заново", open=False):
        gr.Markdown(
            "Сброс удалит знакомства, диалоги, отношения, настроение, сохранённые места "
            "и игровые задачи. Личность, авторские истории, голоса, модели и учёт токенов "
            "сохранятся. Нажмите Stop, запланируйте сброс и полностью перезапустите Python."
        )
        status = gr.Textbox(
            value=panel.status(), label="Сброс памяти", interactive=False
        )
        reset = gr.Button("Сбросить игровую память при перезапуске", variant="stop")
        cancel = gr.Button("Отменить сброс")
        reset.click(panel.schedule, outputs=[status], concurrency_limit=1)
        cancel.click(panel.cancel, outputs=[status], concurrency_limit=1)
        gr.Timer(2).tick(panel.status, outputs=[status], queue=False)
    return panel
