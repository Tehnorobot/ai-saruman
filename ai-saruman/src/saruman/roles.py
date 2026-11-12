"""One role database/protocol for Lab and the separately installed game runtime."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .provider import OUTPUT_SCHEMA

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
    def __init__(self, project, data_dir=None):
        load_environment(project)
        game = project / "integrations/saruman"
        path = os.getenv("SARUMAN_ROLE_DB") or (game / "data/role-models.sqlite" if game.exists() else (data_dir or project / ".saruman") / "role-models.sqlite")
        path = Path(path)
        if not path.is_absolute():
            path = (game if game.exists() else project) / path
        self.store = settings.RoleStore(path)
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
                "roles": {key: {"label": value[0], "description": value[1], "optional": value[2]} for key, value in settings.ROLE_INFO.items()},
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


def api_complete(profile, instructions, prompt, schema):
    url, headers, body = protocol.build_request(profile, instructions, prompt, schema)
    request = Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=profile["timeout"]) as response:
            data = json.load(response)
    except HTTPError as error:
        code = error.code
        error.close()
        raise RuntimeError(f"{profile['provider']}: HTTP {code}. Проверьте ключ, квоту и модель; замены провайдера нет.") from None
    except (URLError, OSError, ValueError):
        raise RuntimeError(f"{profile['provider']}: не удалось получить ответ API. Замены провайдера нет.") from None
    if not isinstance(data, dict):
        raise protocol.ProviderOutputError({})
    usage = protocol.normalize_usage(profile["provider"], data.get("usage"))
    try:
        return protocol.decode(profile["provider"], data), usage
    except (KeyError, IndexError, TypeError, ValueError):
        raise protocol.ProviderOutputError(usage) from None


class LabRoleProvider:
    def __init__(self, models, codex, *, role="lab_chat", schema=OUTPUT_SCHEMA):
        self.models, self.codex = models, codex
        self.role, self.schema = role, schema

    def complete(self, instructions, prompt, model, trace):
        profile = self.models.store.profiles()[self.role]
        trace.update(model=profile["model"], provider=profile["provider"], reasoning=profile["reasoning"])
        reserve = len((instructions + prompt + json.dumps(self.schema)).encode()) + profile["max_output"]
        identifier = self.models.store.begin(self.role, trace["session_id"], profile, reserve)
        started, usage = time.monotonic(), {}
        try:
            if profile["provider"] == "codex":
                value = self.codex.complete(instructions, prompt, profile["model"], trace,
                                            reasoning=profile["reasoning"], timeout=profile["timeout"],
                                            **({"output_schema": self.schema} if self.role != "lab_chat" else {}))
                usage = protocol.normalize_usage("codex", trace.get("usage"))
            else:
                value, usage = api_complete(profile, instructions, prompt, self.schema)
            if not isinstance(value, dict):
                raise ValueError("Ответ модели не содержит JSON-объект.")
            if self.role == "lab_chat" and (set(value) != {"reply", "memories"} or not isinstance(value["reply"], str) or not isinstance(value["memories"], list)):
                raise ValueError("Ответ модели не содержит корректные reply и memories.")
            self.models.store.finish(identifier, usage, model=trace.get("model"))
            return value
        except Exception as error:
            usage = getattr(error, "usage", usage)
            if profile["provider"] == "codex":
                usage = protocol.normalize_usage("codex", trace.get("usage"))
            self.models.store.finish(identifier, usage, type(error).__name__ + ": " + str(error))
            raise
        finally:
            if profile["provider"] != "codex":
                trace["usage"] = {target: usage[source] for source, target in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                                   ("total_tokens", "totalTokens"), ("cached_input_tokens", "cachedInputTokens"),
                                   ("cache_write_tokens", "cacheWriteTokens")) if source in usage}
            trace["latency_ms"] = round((time.monotonic() - started) * 1000)
