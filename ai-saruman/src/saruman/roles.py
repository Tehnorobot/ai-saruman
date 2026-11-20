"""One role database/protocol for Lab and the separately installed game runtime."""
from contextlib import suppress
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import importlib.util
import json
import os
from pathlib import Path
from select import select
from socket import SHUT_RDWR
import sys
from threading import Event, Thread
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .provider import OUTPUT_SCHEMA, ProviderRequestCancelled, ProviderRequestError, ProviderRequestTimeout

RUNTIME = Path(__file__).resolve().parents[2] / "integrations/saruman/src/saruman"


def load(name):
    key = "_saruman_shared_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, RUNTIME / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


settings, protocol = load("role_settings"), load("role_protocol")


def load_environment(project):
    """Read the same credentials as the game. Existing process environment wins."""
    path = project / "integrations/saruman/.env.saruman"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not (key.startswith(("MINIMAX_", "LITELLM_", "DSLAB_", "CORE_LLM_", "CODEX_")) or key == "SARUMAN_ROLE_DB"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


class ModelSettings:
    def __init__(self, project, data_dir=None, adapter_roles=None):
        load_environment(project)
        game = project / "integrations/saruman"
        path = os.getenv("SARUMAN_ROLE_DB") or (game / "data/role-models.sqlite" if game.exists() else (data_dir or project / ".saruman") / "role-models.sqlite")
        path = Path(path)
        if not path.is_absolute():
            path = (game if game.exists() else project) / path
        self.adapter_roles = adapter_roles or {}
        self.store = settings.RoleStore(path, self.adapter_roles)
        self.store.recover()

    def snapshot(self):
        configurations = {"codex": {"configured": True, "note": "Авторизация через codex login; доступ проверяется отдельно."}}
        for provider in protocol.KEY_NAMES:
            try:
                protocol.validate_provider_configuration(provider)
                configurations[provider] = {"configured": True, "note": "Ключ задан; доступ к модели подтвердит запрос."}
            except ValueError as error:
                configurations[provider] = {"configured": False, "note": str(error)}
        return {"profiles": self.store.profiles(), "catalogue": settings.catalogue(self.store.path),
                "roles": {**{key: {"label": value[0], "description": value[1], "optional": value[2]}
                             for key, value in settings.ROLE_INFO.items()}, **self.adapter_roles},
                "providers": configurations, "history": self.store.history(), "totals": self.store.totals()}

    def save(self, payload):
        role, profile, revision = payload.get("role"), payload.get("profile"), payload.get("revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("Передайте версию настроек из открытого окна.")
        self.store.recover()
        self.store.save(role, profile, paused=True, expected_revision=revision)
        return self.snapshot()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


class ProviderOutputLimit(ProviderRequestError):
    def __init__(self, provider, limit, usage):
        used = usage.get("output_tokens")
        count = f"получено {used} токенов, запрошено до {limit}" if used is not None else f"запрошено до {limit} токенов"
        super().__init__(f"{provider}: модель исчерпала лимит ответа ({count}).")
        self.usage = usage


class ProviderRateLimit(ProviderRequestError):
    pass


def client_disconnected(client_socket, timeout=0):
    # The loopback worker sends one request per connection. Readability after
    # the POST body has been consumed means the worker closed its connection.
    try:
        return bool(select([client_socket], [], [], timeout)[0])
    except (OSError, ValueError):
        return True


def read_until_complete(url, headers, body, client_socket):
    """Wait for the reply while the adapter owns its loopback request."""
    target = urlsplit(url)
    connection_type = HTTPSConnection if target.scheme == "https" else HTTPConnection
    connection = connection_type(target.hostname, target.port, timeout=20)
    done, cancelled = Event(), Event()
    watcher = None
    try:
        connection.connect()
        upstream = connection.sock
        upstream.settimeout(None)

        def watch_client():
            while not done.is_set():
                if client_disconnected(client_socket, 0.2) and not done.is_set():
                    cancelled.set()
                    with suppress(OSError):
                        upstream.shutdown(SHUT_RDWR)
                    return

        if client_socket is not None:
            watcher = Thread(target=watch_client, name="adapter-model-cancellation", daemon=True)
            watcher.start()
        connection.request("POST", target.path + ("?" + target.query if target.query else ""), body, headers)
        with connection.getresponse() as response:
            if not 200 <= response.status < 300:
                raise HTTPError(url, response.status, "", response.headers, response)
            return json.load(response)
    except (OSError, HTTPException, ValueError):
        if cancelled.is_set():
            raise ProviderRequestCancelled("Запрос модели отменён игровым адаптером.") from None
        raise
    finally:
        done.set()
        connection.close()
        if watcher is not None:
            watcher.join()


def api_complete(profile, instructions, prompt, schema, *, client_socket=None, images=None):
    url, headers, body = protocol.build_request(profile, instructions, prompt, schema, images)
    body = json.dumps(body, ensure_ascii=False).encode()
    headers = {**headers, "Content-Type": "application/json"}
    try:
        if profile["timeout"] is None:
            data = read_until_complete(url, headers, body, client_socket)
        else:
            request = Request(url, data=body, headers=headers, method="POST")
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=profile["timeout"]) as response:
                data = json.load(response)
    except HTTPError as error:
        code = error.code
        error.close()
        failure = {429: ProviderRateLimit, 504: ProviderRequestTimeout}.get(code, ProviderRequestError)
        raise failure(f"{profile['provider']}: HTTP {code}.") from None
    except (OSError, HTTPException) as error:
        cause = error.reason if isinstance(error, URLError) else error
        if isinstance(cause, TimeoutError):
            limit = profile["timeout"]
            detail = f"истёк тайм-аут ожидания API ({limit} с)" if limit is not None else "истёк тайм-аут подключения к API"
            raise ProviderRequestTimeout(
                f"{profile['provider']}: {detail}."
            ) from None
        raise ProviderRequestError(f"{profile['provider']}: сбой соединения с API ({type(cause).__name__}).") from None
    except (json.JSONDecodeError, UnicodeError):
        raise ProviderRequestError(f"{profile['provider']}: API вернул ответ, который не удалось прочитать как JSON.") from None
    if not isinstance(data, dict):
        raise protocol.ProviderOutputError({})
    usage = protocol.normalize_usage(profile["provider"], data.get("usage"))
    try:
        return protocol.decode(profile["provider"], data), usage
    except (KeyError, IndexError, TypeError, ValueError):
        limit = profile["max_output"]
        if data.get("stop_reason") == "max_tokens" or usage.get("output_tokens", 0) >= limit:
            raise ProviderOutputLimit(profile["provider"], limit, usage) from None
        raise protocol.ProviderOutputError(usage) from None


class LabRoleProvider:
    def __init__(self, models, codex, *, role="lab_chat", schema=OUTPUT_SCHEMA):
        self.models, self.codex = models, codex
        self.role, self.schema = role, schema

    def complete(self, instructions, prompt, model, trace, *, timeout_limit=None, client_socket=None, images=None):
        profile = self.models.store.profiles()[self.role]
        if timeout_limit == 0:
            profile["timeout"] = None
        elif timeout_limit:
            profile["timeout"] = min(profile["timeout"], timeout_limit)
        trace.update(model=profile["model"], provider=profile["provider"], reasoning=profile["reasoning"])
        reserve = len((instructions + prompt + json.dumps(self.schema)).encode()) + profile["max_output"]
        if images:
            reserve += len(json.dumps(images).encode())
        identifier = self.models.store.begin(self.role, trace["session_id"], profile, reserve)
        activity = getattr(self.models, 'activity', None)
        activity_trace=trace.get('parent_id') or identifier
        if activity:
            activity.record('llm.request', status='running', trace=activity_trace,
                scope=None if trace.get('parent_id') else 'lab_private',
                details={'role':self.role,'provider':profile['provider'],'model':profile['model'],'reasoning':profile['reasoning'],
                         'timeout_seconds':profile['timeout'],
                         'request_id':identifier,'instructions':instructions,'input':prompt,'output_schema':self.schema})
        started, usage = time.monotonic(), {}
        try:
            if profile["provider"] == "codex":
                options = {"output_schema": self.schema} if self.role != "lab_chat" else {}
                if profile["timeout"] is None and client_socket is not None:
                    options["cancelled"] = lambda: client_disconnected(client_socket)
                if images:
                    options["images"] = images
                value = self.codex.complete(instructions, prompt, profile["model"], trace,
                                            reasoning=profile["reasoning"], timeout=profile["timeout"], **options)
                usage = protocol.normalize_usage("codex", trace.get("usage"))
            else:
                options = {"client_socket": client_socket} if profile["timeout"] is None else {}
                if images:
                    options["images"] = images
                value, usage = api_complete(profile, instructions, prompt, self.schema, **options)
            if not isinstance(value, dict):
                raise ValueError("Ответ модели не содержит JSON-объект.")
            if self.role == "lab_chat" and (set(value) != {"reply", "memories"} or not isinstance(value["reply"], str) or not isinstance(value["memories"], list)):
                raise ValueError("Ответ модели не содержит корректные reply и memories.")
            self.models.store.finish(identifier, usage, model=trace.get("model"))
            if activity:
                activity.record('llm.result', status='completed', trace=activity_trace,
                    details={'role':self.role,'output':value,'usage':usage})
            return value
        except Exception as error:
            usage = getattr(error, "usage", usage)
            if profile["provider"] == "codex":
                usage = protocol.normalize_usage("codex", trace.get("usage"))
            self.models.store.finish(identifier, usage, type(error).__name__ + ": " + str(error))
            if activity:
                activity.record('llm.result', status='cancelled' if isinstance(error, ProviderRequestCancelled) else 'error', trace=activity_trace,
                    details={'role':self.role,'error':str(error) if isinstance(error, ProviderRequestError) else type(error).__name__,
                             'usage':usage})
            raise
        finally:
            if profile["provider"] != "codex":
                trace["usage"] = {target: usage[source] for source, target in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                                   ("total_tokens", "totalTokens"), ("cached_input_tokens", "cachedInputTokens"),
                                   ("cache_write_tokens", "cacheWriteTokens")) if source in usage}
            trace["latency_ms"] = round((time.monotonic() - started) * 1000)
