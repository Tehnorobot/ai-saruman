"""Persist Lab switches and coordinate one background job with the game worker."""
import json
import math
import os
import subprocess
import threading
import time
import uuid

from .roles import load
from .runtime import stale_native_snapshot

shared = load("automation_extensions")
atomic_json = load("managed").atomic_json
CHECK_SECONDS = 5


class Extensions:
    def __init__(self, project, runtime, adapters):
        self.project, self.runtime, self.adapters = project, runtime, adapters
        self.store = runtime.store
        self.directory = runtime.game.directory
        self.native = {item["id"]: item for item in shared.native_catalog()}
        self.lock, self.probe_lock = threading.RLock(), threading.Lock()
        self.closed = threading.Event()
        self.settings = {"native": {}, "jobs": {}, "weights": {}, "interval": CHECK_SECONDS,
                         **self.store.meta("game_extensions", {})}
        self.observed = {}
        self.checked = None
        self.revision = uuid.uuid4().hex
        self.replan = self.revision
        self.dispatching = False
        self.operator_adapter = None
        self.operator_adapter_target = None

    def background_tasks(self, items):
        tasks = [{**task, "extension": item["id"], "enabled": item["enabled"], "available": item["available"]}
                 for item in items if item["kind"] == "native"
                 for task in item.get("tasks", []) if task["mode"] == "random"]
        tasks.extend({"id": "adapter:" + item["id"], "title": item["title"],
                      "enabled": item["enabled"], "available": bool(item["available"] and item["queued"]),
                      "offer": item["queued"]["id"] if item["queued"] else None}
                     for item in items if item["kind"] == "adapter" and item["background_action"])
        for task in tasks:
            task["selection_weight"] = self.settings["weights"].get(task["id"], task.get("default_weight", 1))
        return tasks

    def availability(self, *, enabled, mods, present, problem=None):
        if not enabled:
            return "disabled", None
        if mods:
            if not self.observed:
                return "checking", None
            if not self.observed.get("connected"):
                return "client_disconnected", "Запустите Minecraft с UnionClef."
            if not present:
                return "mod_missing", "Проверьте папку mods запущенного профиля Minecraft."
        if problem:
            return problem
        if mods and not self.observed.get("world"):
            return "waiting_world", "Войдите в одиночный мир или на сервер."
        return "ready", None

    def snapshot(self):
        with self.lock:
            world = self.observed.get("world", False)
            installed = self.observed.get("installed", {})
            compatible = self.observed.get("compatible", [])
            failures = self.observed.get("failures", {})
            items = []
            for identifier, spec in self.native.items():
                enabled = self.settings["native"].get(identifier, True)
                present = any(installed.get(mod) for mod in spec["mods"])
                problem = None
                if "altoclef" not in compatible:
                    problem = ("unionclef_unavailable", failures.get("altoclef") or "Проверьте UnionClef в запущенном профиле Minecraft.")
                elif not any(mod in compatible for mod in spec["mods"]):
                    reason = next((failures[mod] for mod in spec["mods"] if mod in failures), None)
                    problem = ("incompatible", reason or "Проверьте совместимость мода с текущим клиентом Minecraft.")
                status, reason = self.availability(enabled=enabled, mods=spec["mods"], present=present, problem=problem)
                items.append({**spec, "kind": "native", "enabled": enabled, "installed": present,
                              "available": status == "ready", "availability": status, "reason": reason})
            for identifier, entry in self.adapters.entries.items():
                enabled = self.adapters.state(identifier)[0]
                present = all(installed.get(mod) for mod in entry.minecraft_mods)
                api_ready = not entry.minecraft_api or self.observed.get("apis", {}).get(entry.minecraft_api, False)
                problem = None
                if present and not api_ready:
                    jar = next((self.observed.get("jars", {}).get(mod) for mod in entry.minecraft_mods
                                if self.observed.get("jars", {}).get(mod)), None)
                    reason = (f"В клиенте загружен {jar} без требуемого API" if jar
                              else "Клиент загрузил мод без требуемого API")
                    problem = ("api_missing", reason)
                elif entry.minecraft_mods and "altoclef" not in compatible:
                    problem = ("unionclef_unavailable", failures.get("altoclef") or "Проверьте UnionClef в запущенном профиле Minecraft.")
                elif not entry.path.is_file():
                    problem = ("adapter_missing", "В папке адаптера отсутствует модуль запуска.")
                status, reason = self.availability(enabled=enabled, mods=entry.minecraft_mods, present=present, problem=problem)
                queued = self.settings["jobs"].get(identifier)
                tasks = [{"id": action, "title": title, "mode": "chat", "label": action}
                         for action, title in entry.chat_actions.items()]
                items.append({"id": identifier, "title": entry.title, "kind": "adapter", "mods": entry.minecraft_mods,
                              "enabled": enabled, "installed": present, "available": status == "ready",
                              "availability": status, "reason": reason,
                              "tasks": tasks, "background_action": entry.background_action, "queued": queued,
                              "has_objects": entry.has_objects})
            return {"items": items, "errors": self.adapters.errors, "checked": self.checked, "interval": self.settings["interval"],
                    "world": world, "connected": self.observed.get("connected", False), "revision": self.revision,
                    "background_tasks": self.background_tasks(items)}

    def refresh(self):
        with self.probe_lock:
            game = self.project / "integrations/saruman"
            python = game / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            mod_ids = {mod for spec in self.native.values() for mod in spec["mods"]}
            mod_ids.update(mod for entry in self.adapters.entries.values() for mod in entry.minecraft_mods)
            api_names = {entry.minecraft_api for entry in self.adapters.entries.values() if entry.minecraft_api}
            try:
                if not python.is_file():
                    raise OSError("Игровое окружение недоступно")
                proc = subprocess.run([str(python), "-X", "utf8", "-m", "saruman.automation_extensions"],
                                      cwd=game, env={**os.environ, "PYTHONPATH": str(game / "src")},
                                      input=json.dumps({"mods": sorted(mod_ids), "apis": sorted(api_names)}),
                                      capture_output=True, encoding="utf-8",
                                      timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                observed = json.loads(proc.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                observed = {"connected": False}
            with self.lock:
                self.observed, self.checked = observed, time.time()
                self.publish()

    def publish(self):
        with self.lock:
            self.write_state()

    def write_state(self):
        items = self.snapshot()["items"]
        path = self.directory / "extensions.json"
        native = {item["id"]: item["enabled"] for item in items if item["kind"] == "native"}
        tasks = [task for task in self.background_tasks(items)
                 if task["enabled"] and task["selection_weight"] > 0
                 and (not task["id"].startswith("adapter:") or task["available"])]
        owners = [{"id": identifier, "source": getattr(entry.instance, "owner_source", None) or "normal"}
                  for identifier, entry in self.adapters.entries.items() if getattr(entry.instance, "background_busy", False)]
        if self.dispatching and not owners:
            owners = [{"source": "planner"}]
        owner = max(owners, key=lambda row: shared.PRIORITY[row["source"]], default=None)
        value = {"native": native, "tasks": tasks, "weights": self.settings["weights"], "revision": self.revision,
                 "replan": self.replan, "adapter_hold": bool(owner and self.runtime.active), "adapter_owner": owner,
                 "operator_adapter": self.operator_adapter, "operator_adapter_target": self.operator_adapter_target,
                 "adapter_resume_pending": any(
                     getattr(entry.instance, "scheduler_paused", None) for entry in self.adapters.entries.values())}
        if value != shared.read_json(path):
            atomic_json(path, value)

    def configure(self, payload):
        action, identifier = payload.get("action"), payload.get("id")
        warning = None
        if action == "replan":
            self.refresh()
            with self.lock:
                self.replan = self.revision = uuid.uuid4().hex
                self.publish()
                return self.snapshot()
        with self.lock:
            if action == "weights":
                weights = payload.get("weights")
                known = {task["id"] for task in self.snapshot()["background_tasks"]}
                if (not isinstance(weights, dict) or set(weights) - known
                        or any(type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1
                               for weight in weights.values())):
                    raise ValueError("Вес каждого фонового занятия должен быть числом от 0 до 1")
                self.settings["weights"].update(weights)
                self.revision = uuid.uuid4().hex
                self.store.set_meta("game_extensions", self.settings)
                self.publish()
                return self.snapshot()
            if action == "interval":
                interval = payload.get("seconds")
                if type(interval) is not int or not 2 <= interval <= 120:
                    raise ValueError("Интервал проверки должен быть от 2 до 120 секунд")
                self.settings["interval"] = interval
                self.store.set_meta("game_extensions", self.settings)
                return self.snapshot()
            if not isinstance(identifier, str):
                raise ValueError("Выберите мод или адаптер")
            if action == "toggle":
                enabled = payload.get("enabled")
                if type(enabled) is not bool:
                    raise ValueError("Укажите состояние переключателя")
                if identifier in self.native:
                    if self.native[identifier].get("required"):
                        raise ValueError("UnionClef нужен для связи с клиентом Minecraft")
                    self.settings["native"][identifier] = enabled
                else:
                    warning = self.adapters.toggle(identifier, enabled).get("warning")
                    if not enabled:
                        self.settings["jobs"].pop(identifier, None)
                self.revision = uuid.uuid4().hex
            elif action in {"queue", "dequeue"}:
                entry = self.adapters.entry(identifier)
                if not entry.background_action:
                    raise ValueError("У адаптера нет фонового действия")
                if action == "queue":
                    text = payload.get("text")
                    if not isinstance(text, str) or not text.strip() or len(text) > 12000:
                        raise ValueError("Введите описание фонового поручения")
                    if not self.adapters.state(identifier)[0]:
                        raise ValueError("Сначала включите адаптер")
                    self.settings["jobs"][identifier] = {"id": uuid.uuid4().hex, "text": text.strip()}
                else:
                    self.settings["jobs"].pop(identifier, None)
                self.revision = uuid.uuid4().hex
            else:
                raise ValueError("Неизвестное действие с расширениями")
            self.store.set_meta("game_extensions", self.settings)
            self.publish()
            return {**self.snapshot(), "warning": warning}

    def operator_handle(self, identifier, text, request_id, action, *, world_session=None):
        entry = self.adapters.entry(identifier)
        game = self.runtime.game
        if world_session and (not self.runtime.active or not game.requested):
            raise RuntimeError("Игровые действия приостановлены")
        if (not self.adapters.state(identifier)[0] or not entry.minecraft_mods or not self.runtime.active
                or not game.requested or action in {"status", "pause", "stop", "objects"}):
            return self.adapters.handle(identifier, text, request_id, action=action)
        with entry.lock:
            instance = self.adapters.instance(identifier)
            classify = getattr(instance, "chat_action", None)
        if action is None and classify and classify(text) in {"status", "pause", "stop", "objects", None}:
            return self.adapters.handle(identifier, text, request_id, action=action)
        status = {}
        with self.lock:
            if self.operator_adapter:
                raise RuntimeError("Другое поручение Lab ещё получает управление")
            self.operator_adapter = request_id
            self.operator_adapter_target = identifier
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and self.runtime.active and game.requested:
                status = game.read("status.json")
                if world_session and self.fresh(status) and status.get("world_ready") and status.get("world_session") != world_session:
                    raise RuntimeError("Мир изменился во время передачи управления")
                blocker = self.handoff_blocker(identifier, status)
                if blocker:
                    raise RuntimeError(blocker)
                if (status.get("adapter_grant") == request_id and self.fresh(status)
                        and status.get("world_ready")):
                    return self.adapters.handle(identifier, text, request_id, action=action)
                self.coordinate(status, except_id=identifier)
                self.publish()
                time.sleep(.1)
            status = game.read("status.json")
            if not self.runtime.active or not game.requested:
                raise RuntimeError("Передача управления отменена: персонаж остановлен в Lab.")
            if not self.fresh(status):
                raise RuntimeError("Игровой обработчик не подтвердил готовность. Проверьте статус Minecraft в Lab.")
            if status.get("scheduler_busy") or status.get("native_busy"):
                owner = status.get("scheduler_owner") or "игровая задача UnionClef"
                raise RuntimeError(f"Управление удерживает {owner}. Повторите поручение после освобождения.")
            raise RuntimeError("Игровой обработчик не подтвердил запрос управления. Откройте журнал этой команды.")
        except RuntimeError as error:
            self.adapters.record(identifier, "adapter.control", request_id, "blocked",
                                 {"text": str(error), "owner": status.get("scheduler_owner"),
                                  "native_status": status.get("native_status"),
                                  "world_ready": status.get("world_ready")})
            raise
        finally:
            with self.lock:
                self.operator_adapter = None
                self.operator_adapter_target = None
                self.publish()

    def handoff_blocker(self, identifier, status):
        entry = self.adapters.entry(identifier)
        recent = self.checked is not None and time.time() - self.checked <= self.settings["interval"] + 5
        if recent:
            observed = self.observed
            if not observed.get("connected"):
                if not self.fresh(status) or not status.get("world_ready"):
                    return "UnionClef недоступен. Запустите клиент Minecraft с UnionClef и войдите в мир."
            else:
                missing = [mod for mod in entry.minecraft_mods if not observed.get("installed", {}).get(mod)]
                if missing:
                    return "В текущем клиенте Minecraft отсутствует мод: " + ", ".join(missing) + "."
                if entry.minecraft_api and not observed.get("apis", {}).get(entry.minecraft_api):
                    jars = ", ".join(observed.get("jars", {}).get(mod, mod) for mod in entry.minecraft_mods)
                    return f"Клиент Minecraft загрузил {jars} без API {entry.title}. Оставьте совместимый JAR и перезапустите Minecraft."
                if not observed.get("world") and not (self.fresh(status) and status.get("world_ready")):
                    return "Клиент Minecraft подключён. Войдите в мир, затем повторите поручение."
        if self.fresh(status):
            if status.get("player_alive") is False:
                return "Персонаж погиб. Возродитесь и повторите поручение."
            if status.get("native_status") == "paused":
                return "Действует ручная пауза Minecraft. Нажмите Start в Lab и повторите поручение."
            if status.get("native_status") == "unavailable":
                return "Игровой обработчик потерял связь с Minecraft. Дождитесь восстановления подключения."
        return None

    def fresh(self, status):
        game = self.runtime.game
        now = time.time()
        return (bool(game.worker_id) and status.get("worker_id") == game.worker_id
                and getattr(game, "restarting", False) is not True and not stale_native_snapshot(status, now)
                and status.get("agent_running") and 0 <= now-status.get("updated", 0) < 3)

    def coordinate(self, status, *, except_id=None):
        if not self.runtime.active or not self.runtime.game.requested or not self.fresh(status):
            return
        source = status.get("request_source")
        if self.operator_adapter:
            source = "operator"
        elif (shared.PRIORITY.get(source, 0) < shared.PRIORITY["defense"]
              and status.get("defense_busy") and not status.get("hunt_active")):
            source = "defense"
        priority = shared.PRIORITY.get(source, 0)
        for identifier, entry in self.adapters.entries.items():
            instance = entry.instance
            if identifier == except_id or instance is None or not self.adapters.state(identifier)[0]:
                continue
            own_priority = shared.PRIORITY.get(getattr(instance, "owner_source", None) or "normal", 0)
            if not entry.lock.acquire(False):
                continue
            try:
                if getattr(instance, "background_busy", False) and (priority > own_priority
                        or source == "operator" and not self.operator_adapter):
                    relinquish = getattr(instance, "yield_control", None)
                    if relinquish:
                        relinquish()
                elif (getattr(instance, "scheduler_paused", None) and not self.operator_adapter
                      and not status.get("native_busy") and not status.get("scheduler_busy")
                      and not status.get("defense_busy") and status.get("request_source") not in {"operator", "player"}):
                    instance.reclaim_control()
            except (OSError, ValueError, RuntimeError) as error:
                self.adapters.record(identifier, "adapter.control", None, "waiting", {"text": str(error)},
                                     key=f"handoff:{identifier}:{status.get('world_session')}:{error}")
            finally:
                entry.lock.release()

    def dispatch(self):
        with self.lock:
            offer = shared.read_json(self.directory / "extension-offer.json")
            identifier = offer.get("adapter")
            if not isinstance(identifier, str) or identifier not in self.adapters.entries:
                return
            job = self.settings["jobs"].get(identifier)
            if not job or job["id"] != offer.get("id"):
                return
            status = self.runtime.game.read("status.json")
            now = time.time()
            if (not self.runtime.active or not self.runtime.game.requested
                    or self.operator_adapter or status.get("request_source") in {"operator", "player"}
                    or status.get("defense_busy") or status.get("scheduler_busy") or status.get("native_busy")
                    or offer.get("revision") != self.revision or not 0 <= now-offer.get("updated", 0) < 2
                    or not self.runtime.game.worker_id or offer.get("worker_id") != self.runtime.game.worker_id
                    or status.get("worker_id") != self.runtime.game.worker_id or not status.get("agent_running")
                    or status.get("world_session") != offer.get("world") or not status.get("world_ready")
                    or not 0 <= now-status.get("updated", 0) < 3):
                return
            if not any(item["id"] == identifier and item["kind"] == "adapter" and item["available"]
                       for item in self.snapshot()["items"]):
                return
            # Claim durably before calling Minecraft. A lost reply must not start another house.
            self.settings["jobs"].pop(identifier)
            self.store.set_meta("game_extensions", self.settings)
            entry = self.adapters.entry(identifier)
            self.dispatching = True
        try:
            self.publish()
            self.adapters.record(identifier, "request.received", job["id"], "received",
                                 {"text": job["text"], "source": "planner"}, "request:admin-" + job["id"])
            result = self.adapters.handle(identifier, job["text"], job["id"], action=entry.background_action, source="planner")
        except (ValueError, OSError, RuntimeError) as error:
            result = {"status": "error", "detail": str(error)}
        finally:
            with self.lock:
                self.dispatching = False
                self.publish()
        self.adapters.record(identifier, "adapter.result", job["id"], result.get("status", "answered"),
                             {"text": result["detail"], "source": "planner", "result": result})

    def start(self):
        self.publish()

        def poll():
            while not self.closed.is_set():
                try:
                    if self.checked is None or time.time() - self.checked >= self.settings["interval"]:
                        self.refresh()
                    if not self.operator_adapter:
                        self.coordinate(self.runtime.game.read("status.json"))
                    self.dispatch()
                    self.publish()
                except (OSError, ValueError, RuntimeError) as error:
                    if self.adapters.journal:
                        self.adapters.journal.record("extension.check", status="error", actor="Lab",
                                                     scope="game_private", details={"text": str(error)})
                self.closed.wait(0.5)

        threading.Thread(target=poll, name="lab-game-extensions", daemon=True).start()
