"""Private Lab adapters, discovered from trusted local Python files."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import secrets
import sys
from threading import Event, RLock, Thread
from typing import Protocol
import uuid

from .roles import LabRoleProvider

PROTOCOL = "saruman.adapter/1"
NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
MODEL_BODY_LIMIT = 2 * 1024 * 1024
DEFAULT_MODEL_TIMEOUT_SECONDS = 48
JSON_RESULT = {
    "type": "object", "additionalProperties": False,
    "properties": {"json": {"type": "string"}}, "required": ["json"],
}


class Adapter(Protocol):
    def handle(self, text: str, request_id: str) -> dict: ...
    def stop(self) -> dict | None: ...


@dataclass
class Entry:
    title: str
    description: str
    agents: dict
    path: Path
    example: str
    chat_route: tuple[re.Pattern, re.Pattern | None] | None
    chat_actions: dict[str, str]
    lock: RLock
    minecraft_mods: tuple[str, ...]
    minecraft_api: str | None
    background_action: str | None
    has_objects: bool
    instance: Adapter | None = None


class AdapterContext:
    def __init__(self, registry, identifier):
        self.registry = registry
        self.id = identifier
        self.directory = registry.entries[identifier].path.parent
        self.project = registry.project
        self.data_dir = registry.data_dir / "adapters" / identifier

    def state(self):
        return self.registry.state(self.id)[1]

    def save_state(self, value):
        with self.registry.store.db() as db:
            db.execute("UPDATE adapters SET body=? WHERE id=?", (json.dumps(value), self.id))

    def record(self, kind, *, request_id, status, details, key=None):
        return self.registry.record(self.id, kind, request_id, status, details, key)

    def complete(self, agent, instructions, payload, schema, request_id, *, timeout_limit=None, client_socket=None):
        registry = self.registry
        registry.agent(self.id, agent)
        if not registry.state(self.id)[0]:
            raise RuntimeError("Адаптер выключен")
        provider = LabRoleProvider(registry.models, registry.codex,
                                   role=f"adapter:{self.id}:{agent}", schema=schema)
        result = provider.complete(instructions, json.dumps(payload, ensure_ascii=False), "", {
            "session_id": registry.session,
            "parent_id": "admin-" + request_id,
        }, timeout_limit=timeout_limit, client_socket=client_socket)
        if not registry.state(self.id)[0]:
            raise RuntimeError("Адаптер выключен во время запроса")
        return result

    def model_endpoint(self, agent, request_id):
        """A loopback chat-completions endpoint scoped to an adapter and its agent."""
        registry = self.registry
        registry.agent(self.id, agent)
        if registry.port is None:
            raise RuntimeError("HTTP-сервер Lab ещё не запущен")
        token = registry.tokens[self.id, agent]
        return f"http://127.0.0.1:{registry.port}/adapter-model/{token}/{self.id}/{agent}/{request_id}"

    def capture_object(self, key, eligible, save, *, focus=None):
        capture = self.registry.object_capture
        if capture is None:
            return
        lock = self.registry.entry(self.id).lock

        def take():
            try:
                with lock:
                    if not self.registry.state(self.id)[0] or not eligible():
                        return
                    subject = focus() if focus else None
                photo = capture(key, focus=subject) if subject else capture(key)
                with lock:
                    if self.registry.state(self.id)[0] and eligible():
                        save(photo)
            except (OSError, ValueError, RuntimeError):
                self.record("adapter.object.photo", request_id=None, status="error",
                            details={"text": "Не удалось сохранить снимок готового объекта."})

        Thread(target=take, name="lab-object-photo", daemon=True).start()


class Adapters:
    def __init__(self, project, store, data_dir=None):
        self.project, self.store = project, store
        self.data_dir = data_dir or project / ".saruman"
        self.port = None
        self.session = "adapters:" + uuid.uuid4().hex
        self.entries = {}
        self.errors = []
        self.journal = None
        self.object_capture = None
        self.observer_closed = Event()
        self.observer_thread = None
        self.observer_errors = {}
        with store.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS adapters (id TEXT PRIMARY KEY, enabled INTEGER, body TEXT)")
            for path in sorted((project / "adapters").glob("*/adapter.json")):
                identifier = path.parent.name
                try:
                    entry = self.read_entry(path)
                except (OSError, ValueError, TypeError) as error:
                    self.errors.append({"id": identifier, "detail": str(error)})
                    continue
                self.entries[identifier] = entry
                db.execute("INSERT OR IGNORE INTO adapters VALUES (?,0,'{}')", (identifier,))
        self.tokens = {(identifier, agent): secrets.token_urlsafe(32)
                       for identifier, entry in self.entries.items() for agent in entry.agents}

    @staticmethod
    def read_entry(path):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if (not NAME.fullmatch(path.parent.name) or not isinstance(spec, dict)
                or spec.get("protocol") != PROTOCOL):
            raise ValueError("Некорректный идентификатор или версия протокола адаптера")
        fields = (spec.get("title"), spec.get("description"), spec.get("example", ""))
        if not all(isinstance(value, str) for value in fields) or not fields[0].strip():
            raise ValueError("У адаптера должны быть название и текстовое описание")
        agents = spec.get("agents", {})
        if not isinstance(agents, dict) or any(
            not NAME.fullmatch(agent) or not isinstance(info, dict) or
            not all(isinstance(info.get(key), str) for key in ("label", "description"))
            for agent, info in agents.items()
        ):
            raise ValueError("Некорректные роли моделей адаптера")
        for info in agents.values():
            limit = info.get("timeout_seconds", DEFAULT_MODEL_TIMEOUT_SECONDS)
            if type(limit) is not int or (limit != 0 and not 5 <= limit <= 180):
                raise ValueError("Лимит ожидания модели адаптера должен быть 0 или от 5 до 180 секунд")
        module_path = path.with_name("adapter.py")
        if not module_path.is_file():
            raise ValueError("Отсутствует adapter.py")
        route = spec.get("chat_route")
        if route is not None:
            if (not isinstance(route, dict) or not isinstance(route.get("match"), str)
                    or not route["match"] or not isinstance(route.get("exclude", ""), str)):
                raise ValueError("Некорректный маршрут приватного чата")
            try:
                route = (re.compile(route["match"], re.IGNORECASE),
                         re.compile(route["exclude"], re.IGNORECASE) if route.get("exclude") else None)
            except re.error as error:
                raise ValueError("Некорректный шаблон маршрута приватного чата") from error
        actions = spec.get("chat_actions", {})
        if not isinstance(actions, dict) or any(
            not NAME.fullmatch(key) or not isinstance(description, str) or not description.strip()
            for key, description in actions.items()
        ):
            raise ValueError("Некорректные действия адаптера для приватного чата")
        mod_ids = spec.get("minecraft_mods", [])
        java_api = spec.get("minecraft_api")
        background = spec.get("background_action")
        objects = spec.get("objects", False)
        if (not isinstance(mod_ids, list) or any(not isinstance(mod, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", mod) for mod in mod_ids)
                or java_api is not None and (not isinstance(java_api, str) or not mod_ids
                                             or not re.fullmatch(r"(?:[a-zA-Z_][\w$]*\.)+[a-zA-Z_][\w$]*", java_api))
                or background is not None and (not isinstance(background, str) or background not in actions)
                or type(objects) is not bool):
            raise ValueError("Некорректные зависимости Minecraft или фоновое действие адаптера")
        return Entry(fields[0], fields[1], agents, module_path, fields[2], route, actions,
                     RLock(), tuple(mod_ids), java_api, background, objects)

    def chat_target(self, text):
        matches = []
        for identifier, entry in self.entries.items():
            if entry.chat_route is None:
                continue
            match, exclude = entry.chat_route
            if match.search(text) and not (exclude and exclude.search(text)):
                matches.append(identifier)
        if len(matches) > 1:
            raise ValueError("Команда подходит нескольким адаптерам. Выберите адресата в чате.")
        return matches[0] if matches else ""

    def chat_catalogue(self):
        return [{"id": identifier, "title": entry.title, "description": entry.description,
                 "enabled": self.state(identifier)[0], "actions": entry.chat_actions}
                for identifier, entry in self.entries.items()]

    def bind(self, models, codex, journal):
        self.models, self.codex = models, codex
        self.journal = journal

    def record(self, identifier, kind, request_id, status, details, key=None):
        if not self.journal:
            return False
        self.journal.record(kind, status=status, actor=self.entry(identifier).title,
                            scope="game_private", trace="admin-" + request_id if request_id else "",
                            details={"adapter": identifier, **details}, key=key)
        return self.journal.last_error is None

    def start_observer(self):
        if self.observer_thread:
            return
        def loop():
            while not self.observer_closed.is_set():
                self.observe()
                self.observer_closed.wait(2)
        self.observer_thread = Thread(target=loop, name="lab-adapter-journal", daemon=True)
        self.observer_thread.start()

    def close_observer(self):
        self.observer_closed.set()
        if self.observer_thread:
            self.observer_thread.join(1)

    def observe(self):
        for identifier, entry in self.entries.items():
            state = {}
            try:
                enabled, state = self.state(identifier)
                if entry.instance is None and state.get("run_id") and entry.lock.acquire(False):
                    try:
                        self.instance(identifier)
                    finally:
                        entry.lock.release()
                observer = getattr(entry.instance, "observe", None)
                if observer:
                    observer(enabled, state)
                self.observer_errors.pop(identifier, None)
            except Exception as error:
                detail = str(error)
                if self.observer_errors.get(identifier) != detail:
                    self.record(identifier, "adapter.observer", state.get("trace_id"), "error", {"text": detail})
                    self.observer_errors[identifier] = detail

    def roles(self):
        return {f"adapter:{identifier}:{agent}": {
                    "label": info["label"], "description": info["description"],
                    "optional": False, "adapter": identifier,
                    "unlimited_api_timeout": info.get("timeout_seconds") == 0,
                }
                for identifier, entry in self.entries.items() for agent, info in entry.agents.items()}

    def entry(self, identifier):
        if not isinstance(identifier, str) or identifier not in self.entries:
            raise ValueError("Адаптер не найден")
        return self.entries[identifier]

    def agent(self, identifier, agent):
        info = self.entry(identifier).agents.get(agent)
        if info is None:
            raise ValueError("Роль модели не объявлена адаптером")
        return info

    def state(self, identifier):
        with self.store.db() as db:
            row = db.execute("SELECT enabled,body FROM adapters WHERE id=?", (identifier,)).fetchone()
        return bool(row["enabled"]), json.loads(row["body"])

    def snapshot(self):
        return {"items": [{"id": identifier, "title": entry.title, "description": entry.description,
                           "example": entry.example, "enabled": self.state(identifier)[0]}
                          for identifier, entry in self.entries.items()], "errors": self.errors}

    def minecraft_directory(self):
        for identifier in self.entries:
            directory = self.state(identifier)[1].get("minecraft_game_dir")
            if isinstance(directory, str) and Path(directory).is_absolute():
                return Path(directory).resolve()
        return None

    def instance(self, identifier):
        entry = self.entry(identifier)
        if entry.instance is None:
            name = "_saruman_adapter_" + identifier + "_" + uuid.uuid4().hex
            spec = spec_from_file_location(name, entry.path)
            module = module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
                instance = module.create(AdapterContext(self, identifier))
                if not all(callable(getattr(instance, key, None)) for key in ("handle", "stop")):
                    raise ValueError("Адаптер должен реализовать handle и stop")
                if entry.chat_actions and not callable(getattr(instance, "handle_routed", None)):
                    raise ValueError("Для объявленных действий чата нужен метод handle_routed")
                if entry.has_objects and not callable(getattr(instance, "objects", None)):
                    raise ValueError("Для каталога объектов нужен метод objects")
                entry.instance = instance
            except Exception as error:
                sys.modules.pop(name, None)
                raise RuntimeError(f"Не удалось загрузить адаптер {entry.title}: {error}") from error
        return entry.instance

    def toggle(self, identifier, enabled):
        entry = self.entry(identifier)
        if type(enabled) is not bool:
            raise ValueError("Укажите, включён ли адаптер")
        with entry.lock:
            if enabled:
                self.instance(identifier)
            with self.store.db() as db:
                db.execute("UPDATE adapters SET enabled=? WHERE id=?", (int(enabled), identifier))
            warning = None
            if not enabled and (entry.instance or self.state(identifier)[1]):
                try:
                    self.instance(identifier).stop()
                except (ValueError, OSError, RuntimeError) as error:
                    warning = str(error)
            self.record(identifier, "adapter.configuration", None, "enabled" if enabled else "disabled",
                        {"text": warning or ("Адаптер включён" if enabled else "Адаптер выключен"),
                         "stop_warning": warning})
            return {**self.snapshot(), "warning": warning}

    def handle(self, identifier, text, request_id, *, action=None, source="operator"):
        entry = self.entry(identifier)
        with entry.lock:
            if not self.state(identifier)[0]:
                raise ValueError("Включите адаптер в окне «Моды и адаптеры»")
            if action is not None and (action not in entry.chat_actions and (action or entry.chat_actions)):
                raise ValueError("Действие отсутствует в протоколе адаптера")
            instance = self.instance(identifier)
            if handle_from := getattr(instance, "handle_from", None):
                result = handle_from(text, action, request_id, source)
            else:
                result = (instance.handle_routed(text, action, request_id) if action
                          else instance.handle(text, request_id))
            if not isinstance(result, dict) or not isinstance(result.get("detail"), str):
                raise ValueError("Адаптер должен вернуть результат с текстом detail")
            if not isinstance(result.get("status", "answered"), str):
                raise ValueError("Статус адаптера должен быть строкой")
            classify = getattr(instance, "chat_action", None)
            effective_action = action or (classify(text) if classify else None)
            if (result.get("status") == "running" and effective_action not in {"status", "pause", "stop"}
                    and hasattr(instance, "owner_source")):
                instance.owner_source = source
            return {**result, "adapter": identifier, "adapter_title": entry.title}

    def objects(self, identifier):
        entry = self.entry(identifier)
        if not entry.has_objects:
            raise ValueError("У адаптера нет каталога объектов")
        with entry.lock:
            return self.instance(identifier).objects()

    def stop_all(self, *, preserve_projects=False):
        warnings = []
        for identifier, entry in self.entries.items():
            with entry.lock:
                if entry.instance is None and not self.state(identifier)[1]:
                    continue
                try:
                    instance = self.instance(identifier)
                    suspend = getattr(instance, "suspend", None) if preserve_projects else None
                    (suspend or instance.stop)()
                except (ValueError, OSError, RuntimeError) as error:
                    warnings.append(f"{entry.title}: {error}")
        return warnings

    def authenticate(self, token, identifier, agent):
        expected = self.tokens.get((identifier, agent))
        if expected is None or not secrets.compare_digest(token.encode(), expected.encode()):
            raise PermissionError("Запрос модели не подтверждён")

    def model(self, identifier, agent, request_id, body, *, client_socket=None):
        enabled, run = self.state(identifier)
        active = run.get("model_request_id")
        if not enabled or active != request_id:
            raise PermissionError("У адаптера нет активного запроса модели")
        messages = body.get("messages")
        if (body.get("model") != agent or not isinstance(messages, list) or len(messages) != 2
                or any(not isinstance(message, dict) or message.get("role") != role
                       or not isinstance(message.get("content"), str)
                       for message, role in zip(messages, ("system", "user")))):
            raise ValueError("Некорректный запрос модели адаптера")
        value = AdapterContext(self, identifier).complete(
            agent, messages[0]["content"] + "\nReturn the JSON object as a JSON-encoded string in the field json.",
            json.loads(messages[1]["content"]), JSON_RESULT, active,
            timeout_limit=self.agent(identifier, agent).get("timeout_seconds", DEFAULT_MODEL_TIMEOUT_SECONDS),
            client_socket=client_socket,
        )
        if self.state(identifier)[1].get("model_request_id") != request_id:
            raise PermissionError("Запрос адаптера уже завершён или остановлен")
        raw = value.get("json")
        if not isinstance(raw, str) or not isinstance(json.loads(raw), dict):
            raise ValueError("Модель адаптера должна вернуть JSON-объект")
        return {"choices": [{"message": {"content": raw}}]}
