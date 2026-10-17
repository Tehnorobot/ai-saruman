"""Subscription-only stdio transport. NetTyan, not Codex, executes game tools.

Protocol reference: https://learn.chatgpt.com/docs/app-server
Each operation owns a child process; errors are never automatically retried.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque


def require_subscription(account):
    if not isinstance(account, dict) or account.get("type") not in (
        "chatgpt",
        "chatgptAuthTokens",
    ):
        raise RuntimeError(
            "Нужен ChatGPT-вход Codex. Выполни codex login; API-key fallback выключен."
        )


class Rpc:
    def __init__(self, timeout=180):
        self.deadline = time.monotonic() + timeout
        self.pending = deque()
        self.incoming = queue.Queue()
        self.sequence = 0

    def __enter__(self):
        binary = shutil.which("codex")
        if not binary:
            raise RuntimeError(
                "Codex CLI отсутствует в PATH. Установи CLI и выполни codex login."
            )
        command = [binary, "app-server", "--listen", "stdio://"]
        for feature in (
            "shell_tool",
            "unified_exec",
            "apps",
            "plugins",
            "multi_agent",
            "memories",
            "browser_use",
            "computer_use",
            "image_generation",
            "view_image",
        ):
            command += ["--disable", feature]
        command += ["-c", 'web_search="disabled"']
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "saruman_minecraft",
                        "title": "Saruman Minecraft",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        self.incoming.put(message)
                except json.JSONDecodeError:
                    continue
        finally:
            self.incoming.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _next(self, deadline):
        remaining = min(deadline, self.deadline) - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Истёк срок ответа Codex; повтор не выполнялся.")
        try:
            message = self.incoming.get(timeout=remaining)
        except queue.Empty:
            raise RuntimeError(
                "Истёк срок ответа Codex; повтор не выполнялся."
            ) from None
        if message is None:
            raise RuntimeError("Codex app-server завершился до ответа.")
        if "method" in message and "id" in message:
            self.send(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": "Only NetTyan may execute character tools; Codex tool execution is disabled.",
                    },
                }
            )
            return {}
        return message

    def request(self, method, params):
        self.sequence += 1
        identifier = self.sequence
        self.send({"id": identifier, "method": method, "params": params})
        deadline = min(self.deadline, time.monotonic() + 20)
        while True:
            message = self._next(deadline)
            if message.get("id") == identifier:
                if "error" in message:
                    raise RuntimeError(f"Ошибка Codex RPC: {method}.")
                return message.get("result", {})
            if "method" in message:
                self.pending.append(message)

    def notification(self):
        return self.pending.popleft() if self.pending else self._next(self.deadline)

    def __exit__(self, *_):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()


def complete(instructions, inputs, schema, model="auto", timeout=180):
    start = time.monotonic()
    trace = {}
    with (
        Rpc(timeout) as rpc,
        tempfile.TemporaryDirectory(prefix="saruman-minecraft-") as cwd,
    ):
        require_subscription(
            rpc.request("account/read", {"refreshToken": False}).get("account")
        )
        if model == "auto":
            models, cursor = [], None
            while True:
                page = rpc.request(
                    "model/list",
                    {"limit": 100, "includeHidden": False, "cursor": cursor},
                )
                models.extend(x for x in page.get("data", []) if not x.get("hidden"))
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            if not models:
                raise RuntimeError("Codex не вернул каталог моделей.")
            model = next(
                (x["model"] for x in models if x.get("isDefault")), models[0]["model"]
            )
        thread = rpc.request(
            "thread/start",
            {
                "model": model,
                "cwd": cwd,
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "environments": [],
                "baseInstructions": instructions,
                "developerInstructions": "Return only the requested JSON. Do not execute tools or external actions. NetTyan validates and executes proposed tool calls.",
            },
        )
        thread_id = thread["thread"]["id"]
        trace["model"] = thread.get("model", model)
        rpc.request(
            "turn/start",
            {
                "threadId": thread_id,
                "model": model,
                "input": inputs,
                "outputSchema": schema,
                "environments": [],
            },
        )
        texts = {}
        while True:
            event = rpc.notification()
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
            elif method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    texts[item["id"]] = item.get("text", "")
            elif method == "turn/completed":
                if params.get("turn", {}).get("status") != "completed":
                    raise RuntimeError(
                        "Модельный ход Codex не завершён успешно; повтор не выполнялся."
                    )
                if not texts:
                    raise RuntimeError("Codex завершил ход без agentMessage.")
                try:
                    result = json.loads(list(texts.values())[-1])
                except json.JSONDecodeError:
                    raise RuntimeError("Codex вернул невалидный JSON.") from None
                break
    trace["latency_ms"] = round((time.monotonic() - start) * 1000)
    return result, trace
