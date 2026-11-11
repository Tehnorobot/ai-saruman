"""Explicit test client. Only the publish command sends a Telegram message."""
from __future__ import annotations

import argparse
from http.client import HTTPException
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler

if __package__:
    from .gateway import ID_PATTERN, NoRedirect, read_env
else:
    from gateway import ID_PATTERN, NoRedirect, read_env


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("GATEWAY_URL must be an HTTP(S) origin without credentials, query or path")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Remote HTTP is forbidden; use HTTPS or a local SSH tunnel")
    return value.rstrip("/")


def call(base_url: str, key: str, method: str, path: str, payload=None):
    base_url = validate_url(base_url)
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base_url + path, data=data, headers=headers, method=method)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=90) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read(64 * 1024)
    except (URLError, OSError, HTTPException):
        return 0, {"error": "gateway_unreachable", "detail":
                   "Check server/tunnel. For publish, query status with the SAME publication ID before retrying."}
    try:
        result = json.loads(raw)
    except ValueError:
        return status, {"error": "gateway_non_json_response"}
    return status, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.client"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="Gateway liveness only, no Telegram request")
    commands.add_parser("check", help="Check bot, target channel and posting permission; no publication")
    publish = commands.add_parser("publish", help="SEND a message to the configured test channel")
    publish.add_argument("--id", required=True, help="Keep the same ID when repeating the same request")
    source = publish.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file", type=Path, help="UTF-8 plain text file")
    status = commands.add_parser("status")
    status.add_argument("id")
    args = parser.parse_args()
    try:
        env = read_env(args.env_file)
        url = validate_url(env.get("GATEWAY_URL", "http://127.0.0.1:8787"))
        key = env.get("GATEWAY_API_KEY", "")
        if args.command != "health" and not key:
            raise ValueError("Set GATEWAY_API_KEY in the client .env.client")
        if args.command in ("publish", "status"):
            if not ID_PATTERN.fullmatch(args.id):
                raise ValueError("Invalid publication ID")
        if args.command == "publish":
            text = args.file.read_text(encoding="utf-8-sig") if args.file else args.text
            method, path, payload = "POST", "/v1/posts", {"publication_id": args.id, "text": text}
        else:
            method, payload = "GET", None
            path = {"health": "/healthz", "check": "/v1/check", "status": f"/v1/posts/{getattr(args, 'id', '')}"}[args.command]
        code, result = call(url, key if args.command != "health" else "", method, path, payload)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"HTTP: {code}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    success = 200 <= code < 300
    if args.command == "check":
        success = success and result.get("can_post") is True
    if args.command in ("publish", "status"):
        success = success and result.get("status") == "sent"
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
