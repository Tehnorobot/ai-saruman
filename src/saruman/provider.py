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


class Rpc:
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

    def _next(self, timeout):
        try:
            message = self.incoming.get(timeout=max(0.01, timeout))
        except queue.Empty:
            raise RuntimeError("Codex не ответил вовремя. Запрос не будет автоматически повторён.")
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
        while time.monotonic() < deadline:
            message = self._next(deadline - time.monotonic())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"Codex {method}: {message['error'].get('message', 'ошибка протокола')}")
                return message.get("result", {})
            if "method" in message:
                self.pending.append(message)
        raise RuntimeError(f"Истекло время ожидания {method}.")

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


class CodexProvider:
    def status(self):
        with Rpc() as rpc:
            account = rpc.request("account/read", {"refreshToken": False}).get("account")
            require_subscription(account)
            models, cursor = [], None
            while True:
                page = rpc.request("model/list", {"limit": 100, "includeHidden": False, "cursor": cursor})
                models += [{"id": item["model"], "name": item["displayName"],
                            "is_default": item.get("isDefault", False)}
                           for item in page.get("data", []) if not item.get("hidden")]
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            limits, limit_error = None, None
            try:
                limits = rpc.request("account/rateLimits/read", {})
            except RuntimeError as error:
                limit_error = str(error)
            return {"connected": True, "plan": account.get("planType"), "models": models,
                    "limits": limits, "limit_error": limit_error,
                    "note": "Каталог моделей не гарантирует доступ; его подтверждает успешный ответ."}

    def complete(self, instructions, prompt, model, trace):
        start = time.monotonic()
        try:
            with Rpc() as rpc, tempfile.TemporaryDirectory(prefix="saruman-chat-") as directory:
                require_subscription(rpc.request("account/read", {"refreshToken": False}).get("account"))
                thread = rpc.request("thread/start", {
                    "model": model, "cwd": directory, "approvalPolicy": "never",
                    "sandbox": "read-only", "ephemeral": True, "environments": [],
                    "baseInstructions": instructions,
                    "developerInstructions": "Character test chat. No tools, file changes or external actions.",
                    "serviceName": "saruman_lab", "serviceTier": "default"})
                trace["model"] = thread.get("model", model)
                thread_id = thread["thread"]["id"]
                rpc.request("turn/start", {"threadId": thread_id, "model": model,
                    "input": [{"type": "text", "text": prompt}],
                    "outputSchema": OUTPUT_SCHEMA, "environments": [],
                    "serviceTierForTurn": "default"})
                completed_text, deltas = {}, {}
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    event = rpc.notification(deadline - time.monotonic())
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
                        if not isinstance(result.get("reply"), str) or not isinstance(result.get("memories"), list):
                            raise RuntimeError("В ответе Codex отсутствуют reply или memories.")
                        return result
                raise RuntimeError("Превышено время ответа Codex (180 секунд). Повтор не выполнялся.")
        finally:
            trace["latency_ms"] = round((time.monotonic() - start) * 1000)
