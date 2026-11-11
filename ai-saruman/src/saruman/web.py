"""Loopback-only HTTP UI for character tests; no third-party dependencies."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
import secrets
import threading
import traceback
from urllib.parse import parse_qs, urlparse

from .memory import Workplace, estimate_tokens
from .provider import CodexProvider
from .storage import Store
from .telegram import Control, Store as TelegramStore

MAX_BODY = 64 * 1024


class Application:
    def __init__(self, project, data_dir=None, provider=None):
        self.workplace = Workplace(project)
        self.store = Store((data_dir or project / ".saruman") / "lab.sqlite3")
        self.provider = provider or CodexProvider()
        self.provider_status = {"connected": False, "models": [], "limits": None,
                                "note": "Нажми «Подключить Codex», чтобы проверить аккаунт и лимиты."}
        self.token = secrets.token_urlsafe(32)
        self.turn_lock = threading.Lock()
        self.telegram = Control(TelegramStore((data_dir or project / ".saruman") / "telegram.sqlite3"))

    def overview(self):
        return {"csrf_token": self.token, "memory": self.workplace.snapshot(),
                "sessions": self.store.sessions(), "analytics": self.store.analytics(),
                "provider": self.provider_status, "persona": self.workplace.read("PERSONA.md")}

    def refresh_provider(self):
        try:
            self.provider_status = self.provider.status()
        except (RuntimeError, OSError) as error:
            self.provider_status = {"connected": False, "models": [], "limits": None, "error": str(error)}
        return self.provider_status

    def chat(self, payload):
        text = payload.get("text")
        session_id, model = payload.get("session_id"), payload.get("model")
        remember = payload.get("remember", False)
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("Сообщение должно содержать от 1 до 12 000 символов.")
        if not isinstance(model, str) or not model or len(model) > 160:
            raise ValueError("Сначала подключи Codex и выбери модель.")
        if not isinstance(remember, bool) or not isinstance(session_id, str):
            raise ValueError("Некорректные параметры диалога.")
        if not self.turn_lock.acquire(blocking=False):
            raise BlockingIOError("Предыдущий ответ ещё формируется.")
        try:
            messages = self.store.messages(session_id)
            history = [{"role": row["role"], "content": row["content"]} for row in messages]
            instructions, prompt, metrics = self.workplace.context(history, text.strip(), remember)
            run_id = self.store.begin(session_id, text.strip(), model, metrics)
            trace = {"model": model}
            try:
                result = self.provider.complete(instructions, prompt, model, trace)
                facts = result["memories"] if remember else []
                warning = None
                try:
                    remembered = self.workplace.remember(facts, session_id, run_id)
                except (ValueError, OSError) as error:
                    remembered, warning = 0, "Ответ получен, но память не сохранена: " + str(error)
                self.store.finish(run_id, session_id, result["reply"], trace, remembered,
                                  estimate_tokens(json.dumps(facts, ensure_ascii=False)) if facts else 0, warning)
                return {"reply": result["reply"], "run_id": run_id, "warning": warning,
                        "remembered": remembered}
            except (RuntimeError, OSError, ValueError) as error:
                self.store.finish(run_id, session_id, None, trace, error=str(error))
                raise RuntimeError(str(error))
        finally:
            self.turn_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "SarumanLab/0.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, *_):
        pass  # Chat text, URL query contents and auth data do not belong in access logs.

    def allowed_origin(self):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        return host in allowed and (not origin or origin == "http://" + host)

    def respond(self, data, code=200, content_type="application/json; charset=utf-8"):
        content = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if not self.allowed_origin():
            return self.respond({"error": "Недопустимый источник запроса."}, 403)
        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/api/overview":
                return self.respond(self.app.overview())
            if parsed.path == "/api/telegram":
                return self.respond(self.app.telegram.store.snapshot(int(params.get("page", ["0"])[0])))
            if parsed.path == "/api/messages":
                return self.respond(self.app.store.messages(params.get("session_id", [""])[0]))
            if parsed.path == "/api/database":
                return self.respond(self.app.store.table(params.get("table", ["sessions"])[0]))
            if parsed.path == "/api/export":
                session_id = params.get("session_id", [""])[0]
                return self.respond({"session_id": session_id,
                                     "messages": self.app.store.messages(session_id),
                                     "runs": [row for row in self.app.store.runs() if row["session_id"] == session_id]})
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/styles.css": ("styles.css", "text/css; charset=utf-8")}
            if parsed.path not in assets:
                return self.respond({"error": "Не найдено."}, 404)
            name, kind = assets[parsed.path]
            return self.respond(files("saruman").joinpath("static", name).read_bytes(), content_type=kind)
        except ValueError as error:
            self.respond({"error": str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        if not self.allowed_origin() or not secrets.compare_digest(
                self.headers.get("X-Saruman-Token", ""), self.app.token):
            return self.respond({"error": "Обнови страницу: локальная сессия не подтверждена."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.respond({"error": "Некорректный размер запроса."}, 413)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ожидается JSON-объект.")
            if self.path == "/api/sessions":
                return self.respond(self.app.store.create_session(), 201)
            if self.path == "/api/provider":
                return self.respond(self.app.refresh_provider())
            if self.path == "/api/chat":
                return self.respond(self.app.chat(payload))
            if self.path == "/api/telegram/settings":
                return self.respond(self.app.telegram.store.configure(payload))
            if self.path == "/api/telegram/check":
                return self.respond(self.app.telegram.check())
            if self.path == "/api/telegram/draft":
                return self.respond({"id": self.app.telegram.store.manual(payload.get("text"))}, 201)
            if self.path == "/api/telegram/action":
                self.app.telegram.store.action(payload.get("id"), payload.get("action"))
                return self.respond({"ok": True})
            self.respond({"error": "Не найдено."}, 404)
        except BlockingIOError as error:
            self.respond({"error": str(error)}, 409)
        except (ValueError, TypeError) as error:
            self.respond({"error": str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (RuntimeError, OSError) as error:
            self.respond({"error": str(error)}, 502)
        except Exception:
            traceback.print_exc()
            self.respond({"error": "Внутренняя ошибка. Подробности в терминале сервера."}, 500)


def make_server(app, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    return server


def main():
    parser = argparse.ArgumentParser(description="Локальная лаборатория персонажа Саруман")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project", type=Path, default=Path.cwd(),
                        help="Корень проекта с папкой agent-workplace")
    args = parser.parse_args()
    try:
        app = Application(args.project.resolve())
        server = make_server(app, args.port)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print(f"Saruman Lab → http://127.0.0.1:{server.server_port}", flush=True)
    print("Локальная БД: " + str(app.store.path), flush=True)
    app.telegram.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.telegram.close()
        server.server_close()
