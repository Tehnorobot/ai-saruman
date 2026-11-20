"""A small stdio JSON-RPC client for the installed Codex app-server.

No auth files are parsed and no credentials are returned to the browser.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reply", "memories"],
    "properties": {
        "reply": {"type": "string"},
        "memories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "name", "kind", "fact", "related_ids"],
            "properties": {
                "id": {"type": "string"}, "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["player", "place", "creature", "event"]},
                "fact": {"type": "string"},
                "related_ids": {"type": "array", "items": {"type": "string"}}}}}}}


class ProviderRequestError(RuntimeError):
    """A provider failure with a message suitable for the Lab journal."""


class ProviderRequestTimeout(ProviderRequestError):
    pass


class ProviderRequestCancelled(ProviderRequestError):
    pass


class ProviderWorkspaceError(ProviderRequestError):
    pass


class Rpc:
    def __init__(self, cancelled=None):
        self.cancelled = cancelled

    def __enter__(self):
        binary = shutil.which("codex")
        if not binary:
            raise RuntimeError("Codex CLI не найден в PATH. Установи его и выполни codex login.")
        command = [binary, "app-server", "--listen", "stdio://"]
        for feature in ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent",
                        "memories", "browser_use", "computer_use", "image_generation", "view_image"):
            command += ["--disable", feature]
        command += ["-c", 'web_search="disabled"']
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.incoming = queue.Queue()
        self.pending = []
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {"clientInfo": {"name": "saruman_lab", "title": "Saruman Lab",
                                                       "version": "0.1.0"},
                                        "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.incoming.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            self.incoming.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _receive(self, timeout):
        if self.cancelled is None:
            return self.incoming.get(timeout=timeout)
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if self.cancelled():
                raise ProviderRequestCancelled("Запрос Codex отменён игровым адаптером.")
            delay = min(0.2, deadline - time.monotonic()) if deadline is not None else 0.2
            if delay <= 0:
                raise queue.Empty
            try:
                return self.incoming.get(timeout=delay)
            except queue.Empty:
                continue

    def _next(self, timeout):
        message = self._receive(timeout)
        if message is None:
            raise RuntimeError("Codex app-server завершился. Проверь установку и codex login.")
        if "method" in message and "id" in message:
            self.send({"id": message["id"], "error": {
                "code": -32601, "message": "Character lab does not execute tools or approvals."}})
            return {}
        return message

    def request(self, method, params, timeout=20):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        try:
            while (remaining := deadline - time.monotonic()) > 0:
                message = self._next(remaining)
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(f"Codex {method}: {message['error'].get('message', 'ошибка протокола')}")
                    return message.get("result", {})
                if "method" in message:
                    self.pending.append(message)
        except queue.Empty:
            pass
        raise ProviderRequestTimeout(f"Codex: истёк тайм-аут запроса {method} ({timeout} с).")

    def notification(self, timeout):
        return self.pending.pop(0) if self.pending else self._next(timeout)

    def __exit__(self, *_):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()


def require_subscription(account):
    if not account or account.get("type") not in ("chatgpt", "chatgptAuthTokens"):
        raise RuntimeError("Для этого режима нужен вход Codex через ChatGPT. Выполни codex login; "
                           "API-ключи в лаборатории не используются.")


def read_account(rpc):
    for attempt in range(2):
        try:
            account = rpc.request("account/read", {"refreshToken": False}).get("account")
        except RuntimeError as error:
            if "workspace routing discovery failed" not in str(error).lower():
                raise
            if attempt == 0:
                continue
            raise ProviderWorkspaceError(
                "Codex не смог определить рабочее пространство ChatGPT при проверке аккаунта."
            ) from None
        require_subscription(account)
        return account


class CodexLogin:
    """Keep the app-server alive until its managed browser login finishes."""

    def __init__(self, refresh):
        self.refresh = refresh
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.thread = None
        self.value = {"status": "idle"}

    def snapshot(self):
        with self.lock:
            return self.value.copy()

    def start(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.cancelled.clear()
                self.value = {"status": "starting"}
                self.thread = threading.Thread(target=self.run, name="lab-codex-login", daemon=True)
                self.thread.start()
            return self.value.copy()

    def cancel(self):
        with self.lock:
            if self.value["status"] in {"starting", "waiting"}:
                self.cancelled.set()
                self.value = {"status": "cancelling"}
            return self.value.copy()

    def close(self):
        self.cancel()
        if self.thread:
            self.thread.join(timeout=2)

    def set_status(self, status, **data):
        with self.lock:
            self.value = {"status": status, **data}

    def run(self):
        try:
            with Rpc() as rpc:
                result = rpc.request("account/login/start", {"type": "chatgpt"})
                if not isinstance(result, dict):
                    raise ValueError("Unexpected login response")
                login_id, auth_url = result.get("loginId"), result.get("authUrl")
                if result.get("type") != "chatgpt" or not isinstance(login_id, str) or not login_id or not isinstance(auth_url, str):
                    raise ValueError("Unexpected login response")
                url = urlsplit(auth_url)
                if (url.scheme != "https" or url.hostname not in {"auth.openai.com", "chatgpt.com", "auth0.openai.com"}
                        or url.username is not None or url.password is not None or url.port not in {None, 443}):
                    raise ValueError("Unexpected login URL")
                with self.lock:
                    if not self.cancelled.is_set():
                        self.value = {"status": "waiting", "auth_url": auth_url}
                deadline = time.monotonic() + 600
                while not self.cancelled.is_set() and time.monotonic() < deadline:
                    try:
                        event = rpc.notification(0.25)
                    except queue.Empty:
                        continue
                    if not isinstance(event, dict) or event.get("method") != "account/login/completed":
                        continue
                    params = event.get("params")
                    if not isinstance(params, dict) or params.get("loginId") != login_id:
                        continue
                    if params.get("success") is not True:
                        self.set_status("cancelled" if self.cancelled.is_set() else "failed",
                                        detail="Вход через ChatGPT не завершился.")
                        return
                    self.set_status("checking")
                    break
                else:
                    rpc.request("account/login/cancel", {"loginId": login_id}, timeout=5)
                    self.set_status("cancelled" if self.cancelled.is_set() else "expired")
                    return
            account = self.refresh()
            self.set_status("completed" if account.get("connected") else "failed",
                            detail=None if account.get("connected") else "Codex пока не подтвердил аккаунт. Обновите данные подключения.")
        except (RuntimeError, OSError, ValueError):
            self.set_status("cancelled" if self.cancelled.is_set() else "failed",
                            detail="Не удалось завершить вход через Codex CLI. Попробуйте начать вход снова.")


class CodexProvider:
    def status(self):
        with Rpc() as rpc:
            account = read_account(rpc)
            models, cursor, catalog_error = [], None, None
            try:
                while True:
                    page = rpc.request("model/list", {"limit": 100, "includeHidden": False, "cursor": cursor})
                    models += [{"id": item["model"], "name": item["displayName"],
                                "is_default": item.get("isDefault", False),
                                "reasoning": [x["reasoningEffort"] for x in item.get("supportedReasoningEfforts", [])]}
                               for item in page.get("data", []) if not item.get("hidden")]
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
            except RuntimeError:
                catalog_error = "Каталог моделей временно недоступен."
            limits, limit_error = None, None
            try:
                limits = rpc.request("account/rateLimits/read", {})
            except RuntimeError:
                limit_error = "Codex временно не вернул данные о лимитах аккаунта."
            return {"connected": True, "email": account.get("email"), "plan": account.get("planType"), "models": models,
                    "limits": limits, "limit_error": limit_error, "catalog_error": catalog_error,
                    "note": "Каталог моделей не гарантирует доступ; его подтверждает успешный ответ."}

    def complete(self, instructions, prompt, model, trace, *, reasoning="default", timeout=180,
                 output_schema=None, cancelled=None, images=None):
        start = time.monotonic()
        turn_sent = False
        try:
            with Rpc(cancelled) as rpc, tempfile.TemporaryDirectory(prefix="saruman-chat-") as directory:
                read_account(rpc)
                if model == "auto":
                    page = rpc.request("model/list", {"limit": 100, "includeHidden": False})
                    available = [m for m in page.get("data", []) if not m.get("hidden")]
                    if not available:
                        raise RuntimeError("Codex не вернул каталог моделей.")
                    model = next((m["model"] for m in available if m.get("isDefault")), available[0]["model"])
                thread = rpc.request("thread/start", {
                    "model": model, "cwd": directory, "approvalPolicy": "never",
                    "sandbox": "read-only", "ephemeral": True, "environments": [],
                    "baseInstructions": instructions,
                    "developerInstructions": "Character test chat. No tools, file changes or external actions.",
                    "serviceName": "saruman_lab", "serviceTier": "default"})
                trace["model"] = thread.get("model", model)
                thread_id = thread["thread"]["id"]
                turn_sent = True
                rpc.request("turn/start", {"threadId": thread_id, "model": model,
                    **({"effort": reasoning} if reasoning != "default" else {}),
                    "input": [{"type": "text", "text": prompt}, *(images or [])],
                    "outputSchema": OUTPUT_SCHEMA if output_schema is None else output_schema, "environments": [],
                    "serviceTierForTurn": "default"})
                completed_text, deltas = {}, {}
                deadline = time.monotonic() + timeout if timeout is not None else None
                while True:
                    remaining = deadline - time.monotonic() if deadline is not None else None
                    try:
                        if remaining is not None and remaining <= 0:
                            raise queue.Empty
                        event = rpc.notification(remaining)
                    except queue.Empty:
                        raise ProviderRequestTimeout(f"Codex: истёк тайм-аут ожидания ответа ({timeout} с).") from None
                    params = event.get("params", {})
                    if params.get("threadId") != thread_id:
                        continue
                    method = event.get("method")
                    if method == "thread/tokenUsage/updated":
                        usage = params.get("tokenUsage", {})
                        trace["usage"] = usage.get("total")
                        trace["context_window"] = usage.get("modelContextWindow")
                    elif method == "model/rerouted":
                        trace["model"] = params.get("toModel", trace["model"])
                    elif method == "item/agentMessage/delta":
                        key = params["itemId"]
                        deltas[key] = deltas.get(key, "") + params.get("delta", "")
                    elif method == "item/completed":
                        item = params.get("item", {})
                        if item.get("type") == "agentMessage":
                            completed_text[item["id"]] = item.get("text", "")
                    elif method == "turn/completed":
                        turn = params.get("turn", {})
                        if turn.get("status") != "completed":
                            raise RuntimeError((turn.get("error") or {}).get("message", "Ответ Codex прерван."))
                        raw = list(completed_text.values()) or list(deltas.values())
                        if not raw:
                            raise RuntimeError("Codex завершил запрос без реплики персонажа.")
                        try:
                            result = json.loads(raw[-1])
                        except json.JSONDecodeError:
                            raise RuntimeError("Codex вернул ответ вне запрошенной JSON-схемы.")
                        if not isinstance(result, dict):
                            raise RuntimeError("Codex вернул некорректный объект ответа.")
                        if output_schema is None and (not isinstance(result.get("reply"), str) or not isinstance(result.get("memories"), list)):
                            raise RuntimeError("В ответе Codex отсутствуют reply или memories.")
                        return result
        except Exception:
            if not turn_sent:
                trace["usage"] = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
            raise
        finally:
            trace["latency_ms"] = round((time.monotonic() - start) * 1000)
