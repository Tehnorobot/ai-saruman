"""Validated role profiles and durable request accounting. Never stores API keys."""

import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLE_INFO = {
    "lab_chat": ("Чат Lab и память", "Ответы в лаборатории; извлечение фактов идёт в том же запросе.", False),
    "persona": ("Персонаж · речь и решения", "Общение, голосовые реплики и инициатива. Нужны текст и соблюдение схемы.", False),
    "action": ("Игровые действия", "Выбор инструментов и аргументов. Нужны точные решения и структурированный ответ.", False),
    "telegram": ("Telegram · тексты постов", "Посты из подтверждённых событий. Нужны хороший русский и точность фактов.", False),
    "telegram_reply": ("Telegram · диалоги", "Ответы людям и память собеседника. Нужны текст, хороший русский и соблюдение границ приватности.", False),
    "admin": ("Lab · административные команды", "Выбор разрешённой операции по вашей просьбе. Нужны точные аргументы и структурированный ответ; изображения не требуются.", False),
    "legacy_core": ("Старый режим · основной агент", "Только legacy; общий агент и вспомогательный контекст инструментов.", True),
    "legacy_planner": ("Старый режим · планировщик", "Только legacy; планирование игровых задач.", True),
    "legacy_reaction": ("Старый режим · реакции", "Только legacy; реакции на события.", True),
    "legacy_chat": ("Старый режим · чат", "Только legacy; обработка диалогов.", True),
    "legacy_browser": ("Старый режим · браузер", "Только при вызове браузерного агента.", True),
    "tool_dialogue": ("Инструмент · оценка диалога", "Вспомогательный вызов старого режима; не запускается сам по себе.", True),
    "tool_nickname": ("Инструмент · анализ имени", "Вспомогательный вызов старого режима; короткая текстовая задача.", True),
    "tool_vision": ("Инструмент · анализ изображения", "Только при вызове screenshot. Модель обязательно должна принимать изображения.", True),
}
ROLES = ("persona","action","telegram","lab_chat","telegram_reply","admin") + tuple(k for k, v in ROLE_INFO.items() if v[2])
# Initial catalogue copied from the owner's Lab snapshot, 2026-10-24.
# Account availability is checked by the provider on actual use, never by opening UI.
CODEX = (
    "auto",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
)
LITELLM = (
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "Qwen/Qwen3-Next-80B-A3B-Instruct-FP8",
    "google/gemma-4-26B-A4B-it",
    "zai-org/GLM-5.3-Flash",
    "Qwen/Qwen3.8-27B",
)
DSLAB = ("claude-sonnet-5-5", "claude-opus-5-5")


def catalogue(path=None):
    path = path or os.getenv("SARUMAN_ROLE_DB", ROOT / "data/role-models.sqlite")
    codex = {
        model: ["default", "low", "medium", "high", "xhigh", "max"]
        + ([] if model.endswith("luna") or model == "auto" else ["ultra"])
        for model in CODEX
    }
    if path and Path(path).exists():
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_catalogue'").fetchone():
                for model, efforts in db.execute("SELECT model,efforts FROM model_catalogue"):
                    codex[model] = json.loads(efforts)
    return {
        "codex": codex,
        "minimax": {"MiniMax-M3.1-Flash": ["default", "low", "medium", "high", "xhigh", "max"]},
        "litellm": {m: ["default"] for m in LITELLM},
        "dslab": {
            m: ["default", "low", "medium", "high", "xhigh", "max"] for m in DSLAB
        },
    }


def default_profile():
    model = os.getenv("CORE_LLM_MODEL_NAME", "codex/auto")
    if model.startswith("codex/"):
        model = model.split("/", 1)[1] or "auto"
    else:
        model = "auto"
    if model not in CODEX:
        model = "auto"
    effort = os.getenv("CODEX_REASONING_EFFORT") or "default"
    if effort not in catalogue()["codex"][model]:
        effort = "default"
    # New roles inherit the existing subscription; never enable paid API implicitly.
    return {
        "provider": "codex",
        "model": model,
        "reasoning": effort,
        "timeout": 180,
        "max_output": 2048,
        "max_calls": 200,
        "token_budget": 1000000,
    }


def validate_profile(value, options=None):
    fields = set(default_profile())
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Неверные поля профиля роли")
    provider, model, effort = (
        value[name] for name in ("provider", "model", "reasoning")
    )
    options = options or catalogue()
    if (
        not all(isinstance(v, str) for v in (provider, model, effort))
        or provider not in options
        or model not in options[provider]
    ):
        raise ValueError("Выберите провайдера и модель из каталога")
    if effort not in options[provider][model]:
        raise ValueError("Этот reasoning не поддерживается выбранной моделью")
    for name, lower, upper in (
        ("timeout", 5, None),
        ("max_output", 256, None),
        ("max_calls", 1, None),
        ("token_budget", 1000, None),
    ):
        if type(value[name]) is not int or value[name] < lower or (upper and value[name] > upper):
            raise ValueError(f"Недопустимый лимит {name}")
    return dict(value)


def read_profiles(path=None):
    """Read-only doctor support, including before the first UI startup."""
    path = Path(path or os.getenv("SARUMAN_ROLE_DB", ROOT / "data/role-models.sqlite"))
    result = {role: {**default_profile(), "revision": 0} for role in ROLES}
    if not path.exists():
        return result
    options = catalogue(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for role, revision, body in db.execute(
            "SELECT role,revision,body FROM profiles"
        ):
            if role not in ROLES:
                raise ValueError("Неизвестная сохранённая роль")
            result[role] = {**validate_profile(json.loads(body), options), "revision": revision}
    return result


def pid_alive(pid):
    """Read-only process check; os.kill(pid, 0) is unsafe on Windows."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of death.
        try:
            code = wintypes.DWORD()
            return (
                not kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                or code.value == 259
            )
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RoleStore:
    def __init__(self, path=None):
        self.path = Path(
            path or os.getenv("SARUMAN_ROLE_DB", ROOT / "data/role-models.sqlite")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS profiles (role TEXT PRIMARY KEY, revision INTEGER, body TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, session TEXT, role TEXT, provider TEXT, model TEXT, revision INTEGER, state TEXT, started REAL, ended REAL, usage TEXT, reason TEXT, reserve INTEGER)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, updated REAL, body TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, pid INTEGER)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS model_catalogue (model TEXT PRIMARY KEY, efforts TEXT)")
            # Preserve the old Telegram choice once; future persona edits are independent.
            db.execute("INSERT OR IGNORE INTO profiles SELECT 'telegram',revision,body FROM profiles WHERE role='persona'")
            db.execute("INSERT OR IGNORE INTO profiles VALUES ('telegram',0,?)", (json.dumps(default_profile()),))

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def profiles(self):
        return read_profiles(self.path)

    def save(self, role, profile, *, paused, expected_revision=None):
        if role not in ROLES or not paused:
            raise ValueError(
                "Для изменения модели нажмите Stop и дождитесь завершения запросов"
            )
        profile = validate_profile(profile, catalogue(self.path))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM requests WHERE state='running' AND role=? LIMIT 1", (role,)
            ).fetchone():
                raise ValueError(
                    "Ещё выполняется запрос модели; дождитесь его завершения"
                )
            old = db.execute(
                "SELECT revision FROM profiles WHERE role=?", (role,)
            ).fetchone()
            if expected_revision is not None and expected_revision != (old[0] if old else 0):
                raise ValueError("Настройки уже изменены в другом окне. Обновите страницу.")
            revision = (old[0] if old else 0) + 1
            db.execute(
                "INSERT OR REPLACE INTO profiles VALUES (?,?,?)",
                (role, revision, json.dumps(profile)),
            )
        return revision

    def update_catalogue(self, models):
        with self.connect() as db:
            for item in models:
                efforts = item.get("reasoning", ["default"])
                db.execute("INSERT OR REPLACE INTO model_catalogue VALUES (?,?)",
                           (item["id"], json.dumps(list(dict.fromkeys(["default", *efforts])))))

    def begin(self, role, session, profile, estimate):
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM profiles WHERE role=?", (role,)).fetchone()
            if profile["revision"] != (current[0] if current else 0):
                raise RuntimeError("Настройки роли изменились до начала запроса. Повторите действие.")
            db.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?,?)", (session, os.getpid())
            )
            rows = db.execute(
                "SELECT state,usage,reserve FROM requests WHERE session=? AND role=?",
                (session, role),
            ).fetchall()
            if len(rows) >= profile["max_calls"]:
                raise RuntimeError("Исчерпан лимит запросов роли за запуск")
            consumed = 0
            for row in rows:
                usage = json.loads(row["usage"] or "{}")
                consumed += usage.get("total_tokens", row["reserve"])
            if consumed + estimate > profile["token_budget"]:
                raise RuntimeError(
                    "Недостаточно остатка бюджета роли для этого запроса"
                )
            db.execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    session,
                    role,
                    profile["provider"],
                    profile["model"],
                    profile["revision"],
                    "running",
                    time.time(),
                    None,
                    "{}",
                    None,
                    estimate,
                ),
            )
        return identifier

    def finish(self, identifier, usage=None, reason=None, model=None):
        with self.connect() as db:
            db.execute(
                "UPDATE requests SET state=?,ended=?,usage=?,reason=?,model=COALESCE(?,model) WHERE id=?",
                (
                    "error" if reason else "completed",
                    time.time(),
                    json.dumps(usage or {}),
                    reason,
                    model,
                    identifier,
                ),
            )

    def abandon_session(self, session):
        with self.connect() as db:
            db.execute(
                "UPDATE requests SET state='unknown',ended=?,reason='Runtime stopped before provider outcome' WHERE session=? AND state='running'",
                (time.time(), session),
            )

    def recover(self):
        """Called once at supervisor initialization after the old runtime has ended."""
        with self.connect() as db:
            sessions = db.execute(
                "SELECT DISTINCT r.session,s.pid FROM requests r LEFT JOIN sessions s ON s.id=r.session WHERE r.state='running'"
            ).fetchall()
            for session, pid in sessions:
                if pid and pid_alive(pid):
                    continue
                db.execute(
                    "UPDATE requests SET state='unknown',ended=?,reason='Previous runtime ended; outcome unknown' WHERE session=? AND state='running'",
                    (time.time(), session),
                )

    def history(self, limit=40):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,session,role,provider,model,revision,state,started,ended,usage,reason FROM requests ORDER BY started DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**dict(row), "usage": json.loads(row["usage"])} for row in rows]

    def totals(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT role,provider,COUNT(*) calls,SUM(COALESCE(json_extract(usage,'$.total_tokens'),0)) tokens,SUM(CASE WHEN json_extract(usage,'$.total_tokens') IS NULL THEN 1 ELSE 0 END) unknown FROM requests GROUP BY role,provider"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_intent(self, intent):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO intents VALUES (?,?,?)",
                (intent["id"], time.time(), json.dumps(intent, ensure_ascii=False)),
            )

    def intents(self):
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM intents ORDER BY updated DESC LIMIT 100"
                )
            ]
