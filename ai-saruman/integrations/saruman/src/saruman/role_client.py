"""Role-scoped structured inference. No provider may execute local tools itself."""

import json
import os
from urllib.parse import urlparse

import httpx
import jsonschema


class ProviderOutputError(ValueError):
    def __init__(self, usage):
        super().__init__("Structured provider output was invalid")
        self.usage = usage


class ProviderConfigurationError(ValueError):
    """Only fixed local messages are allowed here, never provider response text."""


class RoleRequestError(RuntimeError):
    """Sanitized role failure suitable for operator diagnostics."""


def validate_provider_configuration(provider):
    if provider == "codex":
        return
    key_name = "LITELLM_API_KEY" if provider == "litellm" else "DSLAB_API_KEY"
    if not (os.getenv(key_name) or "").strip():
        raise ProviderConfigurationError(
            f"Не задан {key_name}. Добавьте его в .env.saruman и полностью перезапустите runtime."
        )
    endpoint(provider)


def endpoint(provider):
    name = "LITELLM_BASE_URL" if provider == "litellm" else "DSLAB_BASE_URL"
    base = os.getenv(
        name, "https://api.dslab.tech" if provider == "dslab" else ""
    ).rstrip("/")
    url = urlparse(base)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ProviderConfigurationError(
            f"Задайте корректный {name} в .env.saruman и полностью перезапустите runtime."
        )
    path = "/chat/completions" if provider == "litellm" else "/messages"
    if base.endswith(path):
        return base
    return base + (path if url.path.rstrip("/") == "/v1" else "/v1" + path)


def normalize_usage(provider, value):
    if not isinstance(value, dict):
        return {}
    result = {}
    if provider == "dslab":
        keys = {
            "input_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "cache_read_input_tokens": "cached_input_tokens",
            "cache_creation_input_tokens": "cache_write_tokens",
        }
        for source, target in keys.items():
            if type(value.get(source)) is int and value[source] >= 0:
                result[target] = value[source]
        if all(key in result for key in ("input_tokens", "output_tokens")):
            result["total_tokens"] = sum(result.get(key, 0) for key in keys.values())
    else:
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            if type(value.get(source)) is int and value[source] >= 0:
                result[target] = value[source]
        cached = (value.get("prompt_tokens_details") or {}).get("cached_tokens")
        if type(cached) is int and cached >= 0:
            result["cached_input_tokens"] = cached
    return result


class RoleClient:
    def __init__(self, store, role, session, transport=None):
        self.store, self.role, self.session = store, role, session
        self.transport = transport or self._http
        self.blocked_revision = None

    def resume(self):
        self.blocked_revision = None

    def complete(self, instructions, payload, schema):
        profile = self.store.profiles()[self.role]
        if self.blocked_revision == profile["revision"]:
            raise RoleRequestError(
                "Роль приостановлена после ошибки; проверьте настройки и нажмите Stop → Start"
            )
        serialized = json.dumps(payload, ensure_ascii=False)
        # UTF-8 bytes are a deliberately conservative preflight, not billed usage.
        reserve = (
            len((instructions + serialized + json.dumps(schema)).encode("utf-8"))
            + profile["max_output"]
        )
        identifier = self.store.begin(self.role, self.session, profile, reserve)
        usage = {}
        try:
            if profile["provider"] == "codex":
                from saruman.codex_rpc import complete

                value, trace = complete(
                    instructions,
                    [{"type": "text", "text": serialized}],
                    schema,
                    profile["model"],
                    profile["timeout"],
                    reasoning_effort=None
                    if profile["reasoning"] == "default"
                    else profile["reasoning"],
                )
                raw = trace.get("usage") or {}
                usage = {
                    target: raw[source]
                    for source, target in (
                        ("inputTokens", "input_tokens"),
                        ("outputTokens", "output_tokens"),
                        ("totalTokens", "total_tokens"),
                        ("cachedInputTokens", "cached_input_tokens"),
                    )
                    if type(raw.get(source)) is int
                }
            else:
                value, usage = self.transport(profile, instructions, serialized, schema)
            jsonschema.validate(value, schema)
        except Exception as error:  # noqa: BLE001 - provider boundary; never leak exception text/credentials
            self.blocked_revision = profile["revision"]
            usage = getattr(error, "usage", usage)
            code = (
                f" HTTP {error.response.status_code}"
                if isinstance(error, httpx.HTTPStatusError)
                else ""
            )
            detail = (
                str(error)
                if isinstance(error, ProviderConfigurationError)
                else (
                    f"{type(error).__name__}{code}: запрос роли не завершён; автоматического повтора нет"
                )
            )
            self.store.finish(
                identifier,
                usage,
                reason=detail,
            )
            raise RoleRequestError(f"Ошибка роли {self.role}: {detail}") from None
        self.store.finish(identifier, usage)
        return value

    @staticmethod
    def _http(profile, instructions, serialized, schema):
        provider = profile["provider"]
        validate_provider_configuration(provider)
        key_name = "LITELLM_API_KEY" if provider == "litellm" else "DSLAB_API_KEY"
        key = os.getenv(key_name)
        function = {
            "name": "submit_role_result",
            "description": "Return the validated result of this role, not a world action.",
            "parameters": schema,
        }
        if provider == "litellm":
            body = {
                "model": profile["model"],
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": serialized},
                ],
                "tools": [{"type": "function", "function": function}],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "submit_role_result"},
                },
                "max_tokens": profile["max_output"],
                "stream": False,
            }
            headers = {"Authorization": "Bearer " + key}
        else:
            effort = profile["reasoning"]
            if effort == "default":
                effort = "high" if "sonnet" in profile["model"] else "medium"
            body = {
                "model": profile["model"],
                "system": instructions,
                "messages": [{"role": "user", "content": serialized}],
                "tools": [
                    {
                        "name": "submit_role_result",
                        "description": function["description"],
                        "input_schema": schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": "submit_role_result"},
                "max_tokens": profile["max_output"],
                "output_config": {"effort": effort},
                "stream": False,
            }
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        # No implicit retries or redirects; credentials only go to configured endpoint.
        with httpx.Client(
            timeout=profile["timeout"], follow_redirects=False, trust_env=False
        ) as client:
            response = client.post(endpoint(provider), headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        usage = normalize_usage(provider, data.get("usage"))
        try:
            value = RoleClient._decode(provider, data)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderOutputError(usage) from None
        return value, usage

    @staticmethod
    def _decode(provider, data):
        if provider == "litellm":
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("Truncated output")
            calls = choice["message"].get("tool_calls", [])
            if len(calls) != 1 or calls[0]["function"]["name"] != "submit_role_result":
                raise ValueError("Missing structured role output")
            value = json.loads(calls[0]["function"]["arguments"])
        else:
            if data.get("stop_reason") == "max_tokens":
                raise ValueError("Truncated output")
            calls = [
                block
                for block in data.get("content", [])
                if block.get("type") == "tool_use"
            ]
            if len(calls) != 1 or calls[0]["name"] != "submit_role_result":
                raise ValueError("Missing structured role output")
            value = calls[0]["input"]
        return value
