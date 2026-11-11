"""Role-scoped structured inference. No provider may execute local tools itself."""

import json

import httpx
import jsonschema


from saruman.role_protocol import (
    ProviderOutputError, ProviderConfigurationError, build_request, decode,
    endpoint, normalize_usage, validate_provider_configuration,
)


class RoleRequestError(RuntimeError):
    """Sanitized role failure suitable for operator diagnostics."""


class RoleClient:
    def __init__(self, store, role, session, transport=None):
        self.store, self.role, self.session = store, role, session
        self.transport = transport or self._http
        self.blocked_revision = None
        self.last_trace = None

    def resume(self):
        self.blocked_revision = None

    def complete(self, instructions, payload, schema, images=None):
        profile = self.store.profiles()[self.role]
        trace = {}
        if self.blocked_revision == profile["revision"]:
            raise RoleRequestError(
                "Роль приостановлена после ошибки; проверьте настройки и нажмите Stop → Start"
            )
        serialized = json.dumps(payload, ensure_ascii=False)
        # UTF-8 bytes are a deliberately conservative preflight, not billed usage.
        reserve = (
            len((instructions + serialized + json.dumps(schema)).encode("utf-8"))
            + (len(json.dumps(images).encode("utf-8")) if images else 0)
            + profile["max_output"]
        )
        identifier = self.store.begin(self.role, self.session, profile, reserve)
        usage = {}
        try:
            if profile["provider"] == "codex":
                from saruman.codex_rpc import complete

                value, trace = complete(
                    instructions,
                    [{"type": "text", "text": serialized}] + (images or []),
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
                value, usage = self.transport(profile, instructions, serialized, schema, images) if images else self.transport(profile, instructions, serialized, schema)
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
            raise RoleRequestError(f"Ошибка роли {self.role} ({profile['provider']} / {profile['model']}): {detail}") from None
        self.store.finish(identifier, usage, model=trace.get("model"))
        self.last_trace = {"provider": profile["provider"], "model": trace.get("model", profile["model"]),
                           "role": self.role, "usage": usage}
        return value

    @staticmethod
    def _http(profile, instructions, serialized, schema, images=None):
        url, headers, body = build_request(profile, instructions, serialized, schema, images)
        with httpx.Client(timeout=profile["timeout"], follow_redirects=False, trust_env=False) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ProviderOutputError({})
        usage = normalize_usage(profile["provider"], data.get("usage"))
        try:
            value = decode(profile["provider"], data)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderOutputError(usage) from None
        return value, usage

    _decode = staticmethod(decode)
