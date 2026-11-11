"""Small, single-channel Telegram publisher. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.parse import parse_qs, urlsplit


ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
MAX_BODY = 24 * 1024


def read_env(path: Path) -> dict[str, str]:
    """Read literal KEY=value pairs; never execute shell commands or interpolation."""
    values = {}
    if path.exists():
        for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                raise ValueError(f"Invalid env entry at line {number}")
            value = value.strip()
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"Unclosed env value at line {number}")
                value = value[1:-1]
            values[key.strip()] = value
    return {**values, **os.environ}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    channel_id: str
    api_key: str
    database: Path

    @classmethod
    def load(cls, path: Path) -> Settings:
        env = read_env(path)
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        channel = env.get("TELEGRAM_CHANNEL_ID", "")
        key = env.get("GATEWAY_API_KEY", "")
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
            raise ValueError("Set TELEGRAM_BOT_TOKEN in the server .env")
        if not re.fullmatch(r"(?:-100[0-9]+|@[A-Za-z][A-Za-z0-9_]{4,})", channel):
            raise ValueError("Set TELEGRAM_CHANNEL_ID to -100... or @channel_username")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", key):
            raise ValueError("GATEWAY_API_KEY must contain 32-256 URL-safe characters")
        database = Path(env.get("GATEWAY_DATABASE", "data/gateway.sqlite3"))
        if not database.is_absolute():
            database = path.resolve().parent / database
        return cls(token, channel, key, database)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a different endpoint.


class TelegramError(Exception):
    def __init__(self, code: str, *, uncertain=False, retry_after=None):
        super().__init__(code)
        self.code, self.uncertain, self.retry_after = code, uncertain, retry_after


class Telegram:
    def __init__(self, token: str):
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, method: str, payload: dict):
        request = Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read()
        except HTTPError as error:
            # 5xx may follow a successful write. Do not blindly send again.
            if error.code >= 500 or 300 <= error.code < 400:
                raise TelegramError(f"telegram_http_{error.code}", uncertain=True) from None
            try:
                body = json.loads(error.read(64 * 1024))
                retry = body.get("parameters", {}).get("retry_after")
                if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
                    retry = None
            except (ValueError, AttributeError):
                retry = None
            raise TelegramError(f"telegram_http_{error.code}", retry_after=retry) from None
        except (URLError, OSError, HTTPException):
            # The error might happen after Telegram has received the request.
            raise TelegramError("telegram_connection_error", uncertain=True) from None
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
            if body.get("ok") is not True:
                code = body.get("error_code", "rejected")
                raise TelegramError(f"telegram_{code}",
                                    retry_after=body.get("parameters", {}).get("retry_after"))
            return body["result"]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TelegramError("telegram_invalid_response", uncertain=True) from None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Publisher:
    def __init__(self, settings: Settings, telegram=None):
        self.settings = settings
        self.telegram = telegram if telegram is not None else Telegram(settings.bot_token)
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS publications (
                publication_id TEXT PRIMARY KEY, text TEXT NOT NULL, digest TEXT NOT NULL,
                status TEXT NOT NULL, message_id INTEGER, error TEXT, retry_after INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    def recover_interrupted(self):
        # Call after binding the server port. A second failed start must not alter active sends.
        with self.connect() as db:
            db.execute("UPDATE publications SET status='unknown', error='server_restarted_during_send', "
                       "updated_at=? WHERE status='sending'", (now(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.settings.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, publication_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM publications WHERE publication_id=?",
                             (publication_id,)).fetchone()
        if row is None:
            return None
        return {key: row[key] for key in (
            "publication_id", "status", "message_id", "error", "retry_after", "created_at", "updated_at")}

    def check(self):
        bot = self.telegram.call("getMe", {})
        if not isinstance(bot, dict) or not isinstance(bot.get("id"), int):
            raise TelegramError("telegram_invalid_bot_response")
        chat = self.telegram.call("getChat", {"chat_id": self.settings.channel_id})
        member = self.telegram.call("getChatMember", {
            "chat_id": self.settings.channel_id, "user_id": bot["id"]})
        if (not isinstance(chat, dict) or not isinstance(chat.get("id"), int)
                or not isinstance(member, dict)):
            raise TelegramError("telegram_invalid_channel_response")
        can_post = chat.get("type") == "channel" and (
            member.get("status") == "creator" or (
                member.get("status") == "administrator" and member.get("can_post_messages") is True))
        return {"bot_username": bot.get("username"), "channel_id": chat["id"],
                "channel_title": chat.get("title"), "can_post": can_post}

    def updates(self, offset):
        chat = self.telegram.call("getChat", {"chat_id": self.settings.channel_id})
        updates = self.telegram.call("getUpdates", {"offset": offset, "timeout": 0, "limit": 100,
                                                  "allowed_updates": ["channel_post", "edited_channel_post"]})
        if not isinstance(updates, list):
            raise TelegramError("telegram_invalid_updates_response")
        selected, next_offset = [], offset
        for update in updates:
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                continue
            next_offset = max(next_offset, update["update_id"] + 1)
            post = update.get("channel_post") or update.get("edited_channel_post") or {}
            if post.get("chat", {}).get("id") == chat["id"]:
                selected.append(update)
        return {"updates": selected, "next_offset": next_offset}

    def retry(self, publication_id):
        with self.connect() as db:
            row = db.execute("SELECT text FROM publications WHERE publication_id=?", (publication_id,)).fetchone()
        if row is None:
            return 404, {"error": "not_found"}
        return self.publish({"publication_id": publication_id, "text": row["text"]}, retry=True)

    def publish(self, payload, retry=False):
        if not isinstance(payload, dict) or set(payload) != {"publication_id", "text"}:
            raise ValueError("Expected exactly publication_id and text; channel is set on the server")
        publication_id, text = payload["publication_id"], payload["text"]
        if not isinstance(publication_id, str) or not ID_PATTERN.fullmatch(publication_id):
            raise ValueError("publication_id: 1-96 letters, numbers, underscores or hyphens")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be empty")
        try:
            units = len(text.encode("utf-16-le")) // 2
        except UnicodeEncodeError:
            raise ValueError("text contains invalid Unicode") from None
        if units > 4096:
            raise ValueError("text exceeds 4096 UTF-16 units; split it into separate posts")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO publications "
                                "(publication_id,text,digest,status,created_at,updated_at) "
                                "VALUES (?,?,?,'sending',?,?)", (publication_id, text, digest, now(), now()))
            fresh = cursor.rowcount == 1
            existing = db.execute("SELECT * FROM publications WHERE publication_id=?",
                                  (publication_id,)).fetchone()
            if existing["digest"] != digest:
                return 409, {"error": "publication_id_conflict", "publication_id": publication_id}
            if retry and not fresh and existing["status"] == "failed":
                from datetime import timedelta
                wait = existing["retry_after"] if type(existing["retry_after"]) is int else 0
                deadline = datetime.fromisoformat(existing["updated_at"]) + timedelta(seconds=wait)
                if deadline > datetime.now(timezone.utc):
                    return 429, {"publication_id": publication_id, "status": "failed",
                                 "error": "telegram_http_429", "retry_after": max(1, int((deadline - datetime.now(timezone.utc)).total_seconds()))}
                claimed = db.execute("UPDATE publications SET status='sending',updated_at=? "
                                     "WHERE publication_id=? AND status='failed'", (now(), publication_id))
                fresh = claimed.rowcount == 1
        if fresh:
            try:
                result = self.telegram.call("sendMessage", {
                    "chat_id": self.settings.channel_id, "text": text,
                    "link_preview_options": {"is_disabled": True}})
                message_id = result.get("message_id") if isinstance(result, dict) else None
                if not isinstance(message_id, int) or isinstance(message_id, bool):
                    raise TelegramError("telegram_missing_message_id", uncertain=True)
                status, error, retry = "sent", None, None
            except TelegramError as failure:
                status = "unknown" if failure.uncertain else "failed"
                message_id, error, retry = None, failure.code, failure.retry_after
            with self.connect() as db:
                db.execute("UPDATE publications SET status=?,message_id=?,error=?,retry_after=?,updated_at=? "
                           "WHERE publication_id=?", (status, message_id, error, retry, now(), publication_id))
        record = self.get(publication_id)
        code = {"sent": 200, "sending": 202, "unknown": 409, "failed": 502}[record["status"]]
        return code, record


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, publisher):
        self.publisher = publisher
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "TelegramGateway/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass  # Do not log Authorization headers, text, tokens or upstream URLs.

    def respond(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        provided = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.publisher.settings.api_key
        if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            self.respond(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if self.path == "/healthz":
            return self.respond(200, {"ok": True, "service": "telegram-gateway", "api_version": 2})
        if not self.authorized():
            return
        if self.path == "/v1/check":
            try:
                return self.respond(200, self.server.publisher.check())
            except TelegramError as error:
                return self.respond(502, {"error": error.code, "retry_after": error.retry_after})
        parsed = urlsplit(self.path)
        if parsed.path == "/v1/updates":
            try:
                offset = parse_qs(parsed.query).get("offset", ["0"])[0]
                if not offset.isdecimal() or len(offset) > 16:
                    raise ValueError()
                return self.respond(200, self.server.publisher.updates(int(offset)))
            except ValueError:
                return self.respond(400, {"error": "invalid_offset"})
            except TelegramError as error:
                return self.respond(502, {"error": error.code})
        prefix = "/v1/posts/"
        if self.path.startswith(prefix) and ID_PATTERN.fullmatch(self.path[len(prefix):]):
            record = self.server.publisher.get(self.path[len(prefix):])
            return self.respond(200 if record else 404, record or {"error": "not_found"})
        self.respond(404, {"error": "not_found"})

    def do_POST(self):
        if not self.authorized():
            return
        retry_match = re.fullmatch(r"/v1/posts/([A-Za-z0-9_-]{1,96})/retry", self.path)
        if self.path != "/v1/posts" and not retry_match:
            return self.respond(404, {"error": "not_found"})
        length = self.headers.get("Content-Length", "")
        if self.headers.get("Transfer-Encoding") or not length.isdecimal():
            return self.respond(400, {"error": "content_length_required"})
        if len(length) > 6 or not 0 < int(length) <= MAX_BODY:
            return self.respond(413, {"error": "body_too_large"})
        if self.headers.get_content_type() != "application/json":
            return self.respond(415, {"error": "application_json_required"})
        try:
            body = self.rfile.read(int(length))
            if len(body) != int(length):
                raise ValueError("Incomplete body")
            payload = json.loads(body)
            if retry_match:
                if payload != {}:
                    raise ValueError("Retry accepts an empty object; original text is retained")
                code, result = self.server.publisher.retry(retry_match[1])
            else:
                code, result = self.server.publisher.publish(payload)
        except (ValueError, UnicodeError) as error:
            # No upstream responses or credentials are included in validation messages.
            return self.respond(400, {"error": "invalid_request", "detail": str(error)})
        self.respond(code, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--generate-key", action="store_true")
    args = parser.parse_args()
    if args.generate_key:
        print(secrets.token_urlsafe(32))
        return
    if os.name != "nt":
        os.umask(0o077)
    try:
        publisher = Publisher(Settings.load(args.env_file))
    except ValueError as error:
        parser.error(str(error))
    with Server((args.host, args.port), publisher) as server:
        publisher.recover_interrupted()
        print(f"Telegram gateway listening on {args.host}:{args.port}; Ctrl+C to stop", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
