"""Role-scoped structured inference. No provider may execute local tools itself."""

import json
import time

import httpx
import jsonschema
from saruman.activity import emit


from saruman.role_protocol import (
    ProviderOutputError, ProviderConfigurationError, build_request, decode,
    endpoint, normalize_usage, validate_provider_configuration,
)


class RoleRequestError(RuntimeError):
    """Sanitized role failure suitable for operator diagnostics."""


def output_issue(error):
    """Describe the trusted schema, never echo a provider response into logs."""
    if isinstance(error, jsonschema.ValidationError):
        return {"schema_path": list(error.absolute_schema_path),
                "rule": error.validator, "expected": error.validator_value}
    return {"rule": "complete_structured_result"}


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
        if self.blocked_revision == profile["revision"]:
            raise RoleRequestError(
                "Роль приостановлена после ошибки; проверьте настройки и нажмите Stop → Start"
            )
        serialized = json.dumps(payload, ensure_ascii=False)
        # Persona has no tools or side effects. Correct one invalid response with
        # the exact schema issue; each paid attempt gets its own usage record.
        attempts = 2 if self.role == "persona" else 1
        deadline = time.monotonic() + profile["timeout"]
        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RoleRequestError("Истекло настроенное время ответа роли; новое сообщение можно обработать отдельно")
            try:
                return self._complete_attempt({**profile, "timeout": remaining}, instructions, serialized, schema, images)
            except RoleRequestError as error:
                if not getattr(error, "output_issue", None) or attempt + 1 == attempts:
                    raise
                serialized = json.dumps({"original_input": payload,
                    "output_correction": {"issue": error.output_issue,
                        "previous_output": error.previous_output}}, ensure_ascii=False)
                instructions += ("\nYour previous result was rejected before any publication. "
                    "Correct only its output contract using output_correction. Preserve the "
                    "answer's meaning. Do not repeat the player's message as an answer. "
                    "Return submit_role_result exactly once, matching this JSON Schema:\n"
                    + json.dumps(schema, ensure_ascii=False))

    def _complete_attempt(self, profile, instructions, serialized, schema, images):
        trace, value = {}, None
        # UTF-8 bytes are a deliberately conservative preflight, not billed usage.
        reserve = (
            len((instructions + serialized + json.dumps(schema)).encode("utf-8"))
            + (len(json.dumps(images).encode("utf-8")) if images else 0)
            + profile["max_output"]
        )
        identifier = self.store.begin(self.role, self.session, profile, reserve)
        request = json.loads(serialized)
        if isinstance(request, dict):
            request = request.get('original_input', request)
        trace_id = request.get('request_id', identifier) if isinstance(request, dict) else identifier
        emit('llm.request', status='running', trace=trace_id,
             details={'request_id': identifier, 'role': self.role, 'provider': profile['provider'],
                      'model': profile['model'], 'reasoning': profile['reasoning'],
                      'instructions': instructions, 'input': request, 'output_schema': schema})
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
            invalid_output = isinstance(error, (jsonschema.ValidationError, ProviderOutputError))
            transient = isinstance(error, httpx.TransportError) or (
                isinstance(error, httpx.HTTPStatusError)
                and (error.response.status_code == 429 or error.response.status_code >= 500))
            # A bad utterance/network interruption must not silence all later
            # conversations. Unknown/configuration/auth failures still need review.
            self.blocked_revision = None if invalid_output or transient else profile["revision"]
            usage = getattr(error, "usage", usage)
            code = (
                f" HTTP {error.response.status_code}"
                if isinstance(error, httpx.HTTPStatusError)
                else ""
            )
            issue = output_issue(error) if invalid_output else None
            detail = (
                "Неверный формат ответа: " + json.dumps(issue, ensure_ascii=False)
                if invalid_output else
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
            emit('llm.result', status='error', trace=trace_id,
                 details={'request_id': identifier, 'role': self.role, 'error': detail, 'usage': usage})
            failure = RoleRequestError(f"Ошибка роли {self.role} ({profile['provider']} / {profile['model']}): {detail}")
            if invalid_output:
                failure.output_issue = issue
                failure.previous_output = value
            raise failure from None
        self.store.finish(identifier, usage, model=trace.get("model"))
        emit('llm.result', status='completed', trace=trace_id,
             details={'request_id': identifier, 'role': self.role, 'output': value, 'usage': usage})
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
