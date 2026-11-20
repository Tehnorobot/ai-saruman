"""Current Lab dependencies and explicit, accounted model probes."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import subprocess
from threading import Lock
import time
import uuid

from .provider import ProviderRequestTimeout, ProviderWorkspaceError
from .roles import LabRoleProvider, ProviderOutputLimit, ProviderRateLimit, protocol, settings


SERVICE_LABELS = {
    "lab": "Saruman Lab", "postgres": "PostgreSQL", "redis": "Redis", "tts": "Озвучка · Fish Speech",
    "stt": "Распознавание речи · Parakeet", "filter": "Фильтр речи и чата",
    "minecraft": "Minecraft и UnionClef", "world": "Игровой мир", "worker": "Игровой обработчик",
    "telegram": "Telegram",
}
NATIVE_SERVICES = ("postgres", "redis", "tts", "stt", "filter")
MODEL_TIMEOUT = 60
RED_SQUARE = {"type": "image", "url": "data:image/png;base64,"
              "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAcElEQVR4nO3PAQkAAAyEwO9feoshgnABdLep8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3M7QGp3PDiCKMcygAAAABJRU5ErkJggg=="}


def row(status, detail):
    return {"status": status, "detail": detail}


def model_error(error):
    if isinstance(error, ProviderWorkspaceError):
        return "Codex не подтвердил доступ к рабочему пространству."
    if isinstance(error, ProviderRateLimit):
        return "Провайдер ограничил запросы или исчерпана квота (HTTP 429)."
    if isinstance(error, ProviderRequestTimeout):
        return "Модель не ответила за время проверки."
    if isinstance(error, settings.RoleBudgetExceeded):
        return "Бюджета роли недостаточно для запроса."
    if isinstance(error, ProviderOutputLimit):
        return "Модель исчерпала настроенный лимит ответа."
    if isinstance(error, (protocol.ProviderOutputError, ValueError)):
        return "Модель вернула ответ неподходящего формата."
    code = re.search(r"\bHTTP\s+(\d{3})\b", str(error))
    if code and code[1] in {"401", "403"}:
        return f"Провайдер отклонил авторизацию или доступ к модели (HTTP {code[1]})."
    if code:
        return f"Модель недоступна (HTTP {code[1]})."
    if "ChatGPT-вход" in str(error):
        return "Войдите в аккаунт Codex в настройках Lab."
    return "Запрос к модели не завершился. Проверьте журнал по этой роли."


class Readiness:
    def __init__(self, project, models, codex, runtime, extensions, telegram):
        self.project = project
        self.models, self.codex, self.runtime = models, codex, runtime
        self.extensions, self.telegram = extensions, telegram
        self.services_lock = Lock()
        self.models_lock = Lock()
        self.service_results = {}
        self.model_results = {}

    @staticmethod
    def profile_key(profile):
        return tuple(profile[key] for key in ("provider", "model", "reasoning", "revision"))

    @staticmethod
    def model_configuration(profile):
        provider = profile["provider"]
        if provider == "codex":
            return None if shutil.which("codex") else "Codex CLI не найден в PATH."
        try:
            protocol.validate_provider_configuration(provider)
        except protocol.ProviderConfigurationError as error:
            return str(error)
        except ValueError:
            return "Проверьте адрес API провайдера в .env.saruman."
        return None

    def snapshot(self):
        profiles = self.models.store.profiles()
        models = []
        provider_issues = {}
        for role, profile in profiles.items():
            meta = self.models.adapter_roles.get(role) or {"label": settings.ROLE_INFO[role][0],
                                                          "optional": settings.ROLE_INFO[role][2]}
            provider = profile["provider"]
            if provider not in provider_issues:
                provider_issues[provider] = self.model_configuration(profile)
            config_issue = provider_issues[provider]
            saved = self.model_results.get(role)
            result = (saved[1] if saved and saved[0] == self.profile_key(profile)
                      else row("unchecked", "Ответ модели ещё не проверялся."))
            if config_issue:
                result = row("error", config_issue)
            models.append({"id": role, "label": meta["label"], "optional": meta.get("optional", False),
                           "model": profile["model"], "provider": profile["provider"],
                           "reasoning": profile["reasoning"], "can_check": not config_issue, **result})
        observed = self.service_results
        services = [{"id": key, "label": label, **observed.get(
            key, row("unchecked", "Нажмите «Обновить службы» для проверки."))}
                    for key, label in SERVICE_LABELS.items()]
        services.extend({"id": key, **value} for key, value in observed.items() if key.startswith("extension:"))
        return {"services": services, "models": models, "model_busy": self.models_lock.locked()}

    def native_services(self, minecraft, filter_enabled):
        wanted = list(NATIVE_SERVICES if filter_enabled else NATIVE_SERVICES[:-1]) if minecraft else []
        results = {key: row("disabled", "Выключено для выбранного запуска.") for key in NATIVE_SERVICES if key not in wanted}
        if wanted:
            game = self.project / "integrations/saruman"
            python = game / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            try:
                proc = subprocess.run([str(python), "-X", "utf8", "-m", "saruman.service_status", *wanted],
                                      cwd=game, env={**os.environ, "PYTHONPATH": str(game / "src")},
                                      capture_output=True, encoding="utf-8", timeout=9, check=True,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                observed = json.loads(proc.stdout)
                results.update({key: observed[key] for key in wanted})
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                results.update({key: row("error", "Проверка в игровом Python-окружении не завершилась.")
                                for key in wanted})
        return results

    def minecraft_services(self, enabled):
        if not enabled:
            return {key: row("disabled", "Minecraft выключен для выбранного запуска.")
                    for key in ("minecraft", "world", "worker")}
        self.extensions.refresh()
        extensions = self.extensions.snapshot()
        game = self.runtime.snapshot()["components"]["minecraft"]
        connected = extensions["connected"]
        unionclef = next((item for item in extensions["items"] if item["id"] == "unionclef"), {})
        bridge = (row("ready", "Связь с клиентом и API UnionClef подтверждена.")
                  if connected and unionclef.get("availability") in {"ready", "waiting_world"}
                  else row("error", unionclef.get("reason") or "Клиент с совместимым UnionClef не ответил."))
        world = (row("ready", "Клиент подтвердил открытый мир.") if extensions["world"]
                 else row("waiting", "Откройте мир или сервер в Minecraft."))
        worker_status = game["status"]
        status = ("ready" if worker_status == "running" else "error"
                  if worker_status in {"unavailable", "interrupted", "retrying", "waiting_services", "not_configured"}
                  else "waiting")
        results = {"minecraft": bridge, "world": world, "worker": row(status, game["reason"])}
        for item in extensions["items"]:
            if item["id"] == "unionclef":
                continue
            available = item["availability"]
            extension_status = {"ready": "ready", "disabled": "disabled", "waiting_world": "waiting",
                                "checking": "unchecked"}.get(available, "error")
            results["extension:" + item["id"]] = {"label": item["title"], **row(
                extension_status, item["reason"] or ("Включён в Lab." if item["enabled"] else "Выключен в Lab."))}
        return results

    def telegram_service(self):
        if not self.telegram.store.settings()["enabled"]:
            return row("disabled", "Telegram выключен в Lab.")
        reply = self.telegram.check(timeout=5)
        if reply["can_post"]:
            return row("ready", "Шлюз ответил. Бот может публиковать в канале.")
        if reply["connected"]:
            return row("error", "Шлюз ответил. Право бота на публикацию не подтверждено.")
        return row("error", "Шлюз Telegram не подтвердил доступ. Проверьте подключение и туннель.")

    def check_services(self, minecraft, filter_enabled):
        if not self.services_lock.acquire(blocking=False):
            raise BlockingIOError("Проверка служб уже идёт.")
        try:
            with ThreadPoolExecutor(max_workers=3) as workers:
                native = workers.submit(self.native_services, minecraft, filter_enabled)
                game = workers.submit(self.minecraft_services, minecraft)
                telegram = workers.submit(self.telegram_service)
                results = {**native.result(), **game.result(), "telegram": telegram.result(),
                           "lab": row("ready", "Локальный HTTP-сервер отвечает.")}
            now = time.time()
            self.service_results = {key: {**value, "checked_at": now} for key, value in results.items()}
        finally:
            self.services_lock.release()

    def check_model(self, role):
        if not isinstance(role, str) or (profile := self.models.store.profiles().get(role)) is None:
            raise ValueError("Роль модели не найдена.")
        if not self.models_lock.acquire(blocking=False):
            raise BlockingIOError("Проверка модели уже идёт.")
        try:
            issue = self.model_configuration(profile)
            if issue:
                return
            trace = {"session_id": "readiness:" + uuid.uuid4().hex}
            vision = role == "tool_vision"
            expected = "RED" if vision else "OK"
            prompt = ("Назови цвет квадрата одним английским словом в поле reply."
                      if vision else 'Верни "OK" в поле reply.')
            started = time.monotonic()
            try:
                reply = LabRoleProvider(self.models, self.codex, role=role).complete(
                    'Техническая проверка. Верни JSON с полями reply и пустым memories.',
                    prompt, profile["model"], trace, timeout_limit=MODEL_TIMEOUT,
                    **({"images": [RED_SQUARE]} if vision else {}))
                if str(reply.get("reply", "")).strip().upper() != expected or reply.get("memories") != []:
                    raise ValueError("Unexpected diagnostic response")
                model = trace.get("model")
                if profile["model"] != "auto" and model != profile["model"]:
                    result = row("waiting", "Провайдер выбрал другую модель.")
                else:
                    result = row("ready", "Изображение распознано." if vision else "Получен проверочный ответ.")
                if model and model != profile["model"]:
                    result["responding_model"] = model
            except (RuntimeError, OSError, ValueError) as error:
                result = row("error", model_error(error))
            self.model_results[role] = (self.profile_key(profile), {**result, "checked_at": time.time(),
                                        "latency_ms": round((time.monotonic()-started)*1000)})
        finally:
            self.models_lock.release()
