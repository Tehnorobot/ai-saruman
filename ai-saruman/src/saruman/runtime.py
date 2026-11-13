"""One local lifecycle owner; Telegram and the optional game process fail independently."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading
import time
import uuid

from .roles import load
from .game_connection import GameConnection

atomic_json = load("managed").atomic_json
OwnerLock = load("managed").FileOwnerLock
NATIVE_MAX_AGE = load("bridge_health").NATIVE_MAX_AGE
RUNTIME_DEFAULTS = {"minecraft": True, "filter_enabled": True}


class MinecraftProcess:
    def __init__(self, project, clock=time.time):
        self.project, self.clock = project, clock
        self.directory = project / ".saruman/runtime/minecraft"
        self.process = None
        self.worker_id = None
        self.finished_workers = set()
        self.revision = 0
        self.requested = False
        self.shutdown_requested = False
        self.filter_enabled = True
        self.applied_filter_enabled = True
        self.restarting = False
        self.directive = None
        self.retry_at = 0
        self.value = {"status": "stopped", "reason": "Minecraft не запрошен"}

    def read(self, name):
        try:
            return json.loads((self.directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def request(self, active):
        self.requested = active
        if not self.restarting:self.shutdown_requested = False
        self.revision += 1

    def shutdown(self):
        self.requested = False
        self.shutdown_requested = True
        self.revision += 1
        self.heartbeat()

    def heartbeat(self):
        if self.worker_id:
            atomic_json(self.directory / "command.json", {
                "worker_id": self.worker_id, "revision": self.revision,
                "active": self.requested and not self.restarting, "updated": self.clock(),
                "shutdown": self.shutdown_requested,
                "directive": self.directive,
            })

    def step(self):
        now, status = self.clock(), self.read("status.json")
        if self.worker_id is None:
            command = self.read("command.json")
            if (status.get("worker_id") and status.get("worker_id") not in self.finished_workers
                    and status.get("worker_id") == command.get("worker_id")
                    and 0 <= now-status.get("updated", 0)<3):
                self.worker_id = status["worker_id"]
                self.revision = max(self.revision, command.get("revision", 0)+1)
                self.applied_filter_enabled = status.get("filter_enabled", True)
        if (self.requested and self.worker_id and not self.restarting
                and self.filter_enabled != self.applied_filter_enabled):
            # An owned worker must exit before its CLI-only filter mode can change.
            self.restarting = True
            self.shutdown_requested = True
            self.revision += 1
        self.heartbeat()
        if self.process is not None and self.process.poll() is not None:
            code = self.process.returncode
            reason = status.get("reason") if status.get("worker_id") == self.worker_id else None
            self.finished_workers.add(self.worker_id)
            self.process = None
            self.worker_id = None
            if self.restarting or not self.requested:
                self.restarting = self.shutdown_requested = False
                self.retry_at = now
                self.value = {"status":"starting" if self.requested else "stopped",
                              "reason":"Применяем настройку фильтра" if self.requested else "Игровые действия остановлены"}
                return
            self.retry_at = now + 30
            self.value = {"status": "retrying", "reason": (reason or f"Игровой процесс завершился: код {code}")+". Повтор проверки через 30 с."}
            return
        live = (self.worker_id and status.get("worker_id") == self.worker_id
                and 0 <= now-status.get("updated", 0)<3)
        if self.restarting:
            if self.process is not None or live:
                self.value={"status":"restarting","reason":"Перезапуск игрового обработчика для изменения фильтра"}
                return
            # Adopted workers have no Popen handle. The runtime instance lock still protects
            # against a duplicate if a closing worker outlives its final heartbeat.
            self.finished_workers.add(self.worker_id)
            self.worker_id = None
            self.restarting = self.shutdown_requested = False
            self.retry_at = now
        if live:
            if not self.requested:
                phase = "stopped" if status.get("revision") == self.revision and not status.get("agent_running") else "stopping"
                self.value = {"status": phase, "reason": "Игровые действия остановлены" if phase=="stopped" else "Останавливаем игровые действия"}
            elif status.get("phase") != "ready":
                self.value = {"status": status.get("phase", "starting"), "reason": status.get("reason", "Запуск")}
            elif status.get("native_fresh") is False or (
                    isinstance(status.get("native_updated"), (int, float))
                    and not 0 <= now-status["native_updated"] <= NATIVE_MAX_AGE):
                self.value = {"status":"unavailable", "reason":status.get("native_error") or "Игровые данные устарели; ожидание восстановления Minecraft"}
            elif status.get("manual_stop_latched") and status.get("native_stop_kind") != "reset":
                self.value = {"status": "paused", "reason": "Игровые действия приостановлены; состояние модов требует проверки в игровом интерфейсе"}
            elif status.get('native_status')=='unavailable':
                self.value={'status':'unavailable','reason':'Нет связи с игровым API: '+(status.get('native_error') or 'ожидание восстановления адаптера')}
            elif status.get("world_ready") and status.get("player_alive") is False:
                self.value={"status":"waiting_respawn","reason":"Персонаж погиб; ожидание возрождения"}
            elif not status.get("agent_running"):
                self.value = {"status": "paused", "reason": "Игровой агент остановлен; нажмите Start для явного возобновления"}
            else:
                self.value = {"status": "running" if status.get("world_ready") else "waiting_world",
                              "reason": (status.get("native_activity", {}).get("reason") or "Персонаж в мире") if status.get("world_ready") else "Ожидание открытого мира Minecraft"}
            return
        if not self.requested:
            if self.value.get("status")=="external":
                self.value={"status":"external","reason":"Отдельный игровой процесс не управляется Lab; остановите его в его интерфейсе"}
            else:
                self.value = {"status": "stopped", "reason": "Игровые действия выключены"}
            return
        if self.process is not None:
            self.value = {"status": "starting", "reason": "Запуск и проверка игрового окружения"}
            return
        if now < self.retry_at:
            return
        if os.name != "nt":
            self.value = {"status": "unavailable", "reason": "Игровой runtime требует Windows; Telegram продолжает работу"}
            return
        game = self.project / "integrations/saruman"
        python = game / ".venv/Scripts/python.exe"
        if not python.is_file() or not (game / ".env.saruman").is_file():
            self.value = {"status": "not_configured", "reason": "Подготовьте игровую .venv и .env.saruman; см. инструкцию Windows"}
            return
        try:
            with socket.create_connection(("127.0.0.1", 22228), timeout=0.2):
                self.value = {"status": "external", "reason": "Игровой процесс запущен отдельно. Закройте его перед запуском из Lab."}
                return
        except OSError:
            pass
        try:
            with socket.create_connection(("127.0.0.1",25333),timeout=.2):pass
        except OSError:
            self.value={"status":"waiting_world","reason":"Ожидание Minecraft с UnionClef. Вход в мир — в разделе «Управление»"}
            return
        self.worker_id = uuid.uuid4().hex
        self.applied_filter_enabled = self.filter_enabled
        self.heartbeat()
        env = {**os.environ, "PYTHONPATH": str(game / "src"), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
               "SARUMAN_LAB_CONTROL_DIR": str(self.directory), "SARUMAN_LAB_WORKER_ID": self.worker_id}
        self.directory.mkdir(parents=True, exist_ok=True)
        argv=[str(python), "-m", "saruman.launcher", "run", "--env-file", ".env.saruman"]
        if not self.filter_enabled:argv.append("--no-filter")
        with (self.directory / "minecraft.log").open("ab") as log:
            self.process = subprocess.Popen(argv,
                cwd=game, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.value = {"status": "starting", "reason": "Запуск и проверка игрового окружения"}


class CharacterRuntime:
    def __init__(self, project, store, director, *, game=None, clock=time.time):
        self.store, self.director, self.clock = store, director, clock
        self.game = game or MinecraftProcess(project, clock)
        self.lock = threading.RLock()
        self.owner = OwnerLock(project / ".saruman/runtime/owner.lock")
        self.identity = uuid.uuid4().hex
        self.active = False
        self.epoch = 0
        self.thread = None
        self.closed = threading.Event()
        self.error = None
        self.last_tick = None
        self.journal_error = False
        self.connection = GameConnection(project,store,clock)
        self.replies = None
        self.workflows = None

    def publish(self):
        self.store.set_meta("character_runtime", {"active": self.active, "updated": self.clock(),
                             "owner": self.identity, "epoch": self.epoch})

    def start(self, payload=None):
        payload = {} if payload is None else payload
        if (not isinstance(payload,dict) or set(payload)-set(RUNTIME_DEFAULTS)
                or any(type(value) is not bool for value in payload.values())):
            raise ValueError("Переключатели Minecraft и фильтра должны быть true или false")
        with self.lock:
            options = {**RUNTIME_DEFAULTS, **self.store.meta("runtime_options", {})}
            if self.active and payload.get("filter_enabled",options["filter_enabled"]) != options["filter_enabled"]:
                raise BlockingIOError("Нажмите Stop перед изменением фильтра текста")
            self.owner.acquire()
            options.update(payload)
            self.store.set_meta("runtime_options", options)
            self.active = True; self.epoch += 1
            self.publish()
            self.game.filter_enabled = options["filter_enabled"]
            self.game.request(options["minecraft"])
            self.director.start()
            if self.replies:self.replies.start()
            if self.workflows:self.workflows.start()
            if not self.thread or not self.thread.is_alive():
                self.last_tick=self.clock()
                self.thread = threading.Thread(target=self.loop, name="character-lifecycle", daemon=True)
                self.thread.start()
        return self.snapshot()

    def stop(self):
        with self.lock:
            if self.owner.file:
                self.active = False; self.epoch += 1
                self.game.directive=None
                self.game.request(False)
                self.game.heartbeat()
                self.connection.cancel()
                try:self.publish()
                except (OSError,sqlite3.Error):
                    self.journal_error=True
                    self.error='Журнал временно недоступен; игровые действия остановлены'
        return self.snapshot()

    def step(self):
        with self.lock:
            self.last_tick=self.clock()
            # The game watchdog must receive its IPC heartbeat even when SQLite
            # is temporarily busy. Never put a database commit before this step.
            try:
                self.game.step()
                self.connection.step()
                self.error = None
            except (OSError, ValueError, RuntimeError):
                self.error = "Не удалось обновить игровой процесс; Telegram продолжает работу"
            self.journal_error=False
            try:self.publish()
            except (OSError,sqlite3.Error):
                self.journal_error=True
                self.error='Журнал временно недоступен; повтор записи выполняется автоматически'

    def loop(self):
        while not self.closed.is_set():
            try:
                self.step()
            except Exception as error:
                # This is a long-lived worker boundary: a failed iteration must
                # be visible and retried, not silently kill the lifecycle thread.
                self.error = 'Ошибка обработчика ('+type(error).__name__+'); повтор выполняется автоматически'
            self.closed.wait(2)

    def snapshot(self):
        settings = self.store.settings()
        draft = self.store.meta("draft_status", {})
        connection = self.store.meta("connection", {})
        telegram = {"status": "stopped", "reason": "Персонаж остановлен; очередь сохранена"}
        if self.active:
            telegram = {"status": "running", "reason": "Расписание и очередь работают независимо от Minecraft"} if settings["enabled"] else {
                "status": "disabled", "reason": "Включите Telegram в его настройках"}
            if settings["enabled"] and connection.get("error"):
                telegram = {"status": "retrying", "reason": connection["error"]}
            elif settings["enabled"] and draft.get("error"):
                telegram = {"status": "retrying", "reason": "Подготовка поста: " + draft["error"]}
        alive=bool(self.thread and self.thread.is_alive())
        fresh=self.last_tick is not None and 0<=self.clock()-self.last_tick<15
        healthy=not self.active or alive and fresh
        minecraft=dict(self.game.value)
        error=self.error
        if not healthy:
            error='Обработчик Lab не отвечает; последнее состояние устарело. Нажмите Start для восстановления.'
            minecraft={'status':'interrupted','reason':error}
            if settings['enabled']:telegram={'status':'interrupted','reason':error}
        elif self.active and self.journal_error and settings['enabled']:
            telegram={'status':'retrying','reason':self.error}
        return {"active": self.active, "components": {"minecraft": minecraft, "telegram": telegram},
                'health':{'lifecycle_alive':alive,'fresh':fresh,'healthy':healthy,'last_tick':self.last_tick},
                "options": {**RUNTIME_DEFAULTS, **self.store.meta("runtime_options", {})}, "error": error,
                "next_draft": self.store.meta("next_draft"), "auto_post": settings["auto_post"],
                "connection":self.connection.value,"game_settings":self.store.meta("game_connection",{"mode":"server","target":"","launcher":"","arguments":[]})}

    def close(self):
        self.stop()
        if self.workflows:self.workflows.close()
        if self.owner.file:
            self.game.shutdown()
        self.closed.set()
        if self.thread:
            self.thread.join(3)
        self.director.close()
        if self.replies:self.replies.close()
        self.owner.close()
