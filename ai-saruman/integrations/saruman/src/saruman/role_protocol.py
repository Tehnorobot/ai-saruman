"""Provider wire formats shared by the stdlib Lab and the game runtime.

No tools are executed here. submit_role_result only returns data to the caller.
"""
import json
import os
from urllib.parse import urlparse


class ProviderConfigurationError(ValueError):
    """Fixed local diagnostic; never includes credentials or provider responses."""


class ProviderOutputError(ValueError):
    def __init__(self, usage):
        super().__init__("Structured provider output was invalid or truncated")
        self.usage = usage


KEY_NAMES = {"minimax": "MINIMAX_API_KEY", "litellm": "LITELLM_API_KEY", "dslab": "DSLAB_API_KEY"}


def endpoint(provider):
    if provider not in KEY_NAMES:
        raise ProviderConfigurationError("Неизвестный API-провайдер")
    name = provider.upper() + "_BASE_URL"
    default = {"minimax": "https://api.minimax.io/anthropic", "dslab": "https://api.dslab.tech"}
    base = (os.getenv(name) or default.get(provider, "")).strip().rstrip("/")
    url = urlparse(base)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.query or url.fragment):
        raise ProviderConfigurationError(f"Задайте корректный {name} в .env.saruman и перезапустите Lab/runtime.")
    if provider == "minimax" and (url.scheme != "https" or url.hostname != "api.minimax.io"
            or url.port not in (None, 443) or url.path not in ("", "/anthropic", "/anthropic/v1", "/anthropic/v1/messages")):
        raise ProviderConfigurationError("MINIMAX_BASE_URL: используйте https://api.minimax.io/anthropic")
    if provider == "minimax" and not url.path:
        return base + "/anthropic/v1/messages"
    path = "/chat/completions" if provider == "litellm" else "/messages"
    if base.endswith(path):
        return base
    return base + (path if url.path.rstrip("/").endswith("/v1") else "/v1" + path)


def validate_provider_configuration(provider):
    if provider == "codex":
        return
    if provider not in KEY_NAMES:
        raise ProviderConfigurationError("Неизвестный API-провайдер")
    name = KEY_NAMES[provider]
    if not (os.getenv(name) or "").strip():
        raise ProviderConfigurationError(f"Не задан {name}. Добавьте его в .env.saruman и перезапустите Lab/runtime.")
    endpoint(provider)


def normalize_usage(provider, value):
    if not isinstance(value, dict):
        return {}
    result = {}
    if provider == "codex":
        return {target: value[source] for source, target in (
            ("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"),
            ("totalTokens", "total_tokens"), ("cachedInputTokens", "cached_input_tokens"))
            if type(value.get(source)) is int and value[source] >= 0}
    if provider in {"dslab", "minimax"}:
        keys = {"input_tokens": "input_tokens", "output_tokens": "output_tokens",
                "cache_read_input_tokens": "cached_input_tokens", "cache_creation_input_tokens": "cache_write_tokens"}
        for source, target in keys.items():
            if type(value.get(source)) is int and value[source] >= 0:
                result[target] = value[source]
        if all(k in result for k in ("input_tokens", "output_tokens")):
            result["total_tokens"] = sum(result.get(k, 0) for k in keys.values())
            # The UI treats cached input as a subset, not extra tokens.
            result["input_tokens"] += result.get("cached_input_tokens", 0) + result.get("cache_write_tokens", 0)
    else:
        for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"), ("total_tokens", "total_tokens")):
            if type(value.get(source)) is int and value[source] >= 0:
                result[target] = value[source]
        cached = (value.get("prompt_tokens_details") or {}).get("cached_tokens")
        if type(cached) is int and cached >= 0:
            result["cached_input_tokens"] = cached
    return result


def build_request(profile, instructions, serialized, schema, images=None):
    provider = profile["provider"]
    validate_provider_configuration(provider)
    key = os.environ[KEY_NAMES[provider]].strip()
    function = {"name": "submit_role_result", "description": "Return this role's result. This does not execute an action.", "parameters": schema}
    user = [{"type": "text", "text": serialized}]
    for image in images or []:
        url = image["url"]
        if not isinstance(url, str) or not url.startswith(("data:image/", "https://")):
            raise ProviderConfigurationError("Неподдерживаемый формат изображения")
        if provider == "litellm":
            user.append({"type": "image_url", "image_url": {"url": url}})
        elif url.startswith("data:image/"):
            media, data = url.split(",", 1)
            if not media.endswith(";base64"):
                raise ProviderConfigurationError("Изображение должно быть base64")
            user.append({"type": "image", "source": {"type": "base64", "media_type": media[5:-7], "data": data}})
        else:
            user.append({"type": "image", "source": {"type": "url", "url": url}})
    content = user if images else serialized
    if provider == "litellm":
        body = {"model": profile["model"], "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": content}],
                "tools": [{"type": "function", "function": function}], "tool_choice": {"type": "function", "function": {"name": function["name"]}},
                "max_tokens": profile["max_output"], "stream": False}
        headers = {"Authorization": "Bearer " + key}
    else:
        body = {"model": profile["model"], "system": instructions,
                "messages": [{"role": "user", "content": content}],
                "tools": [{"name": function["name"], "description": function["description"], "input_schema": schema}],
                "tool_choice": {"type": "tool", "name": function["name"]}, "max_tokens": profile["max_output"], "stream": False}
        effort = profile["reasoning"]
        if provider == "minimax":
            body["thinking"] = {"type": "adaptive"}
            # Preserve thinking support: no forced tool choice or Claude-specific knobs.
            body["tool_choice"] = {"type": "auto"}
            body["system"] += "\nReturn the final result using submit_role_result exactly once."
            if effort != "default":
                body["output_config"] = {"effort": effort}
            headers = {"Authorization": "Bearer " + key, "anthropic-version": "2023-06-01"}
        else:
            if effort == "default":
                effort = "high" if "sonnet" in profile["model"] else "medium"
            body["output_config"] = {"effort": effort}
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return endpoint(provider), headers, body


def decode(provider, data):
    if provider == "litellm":
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("Truncated output")
        calls = choice["message"].get("tool_calls", [])
        if len(calls) != 1 or calls[0]["function"]["name"] != "submit_role_result":
            raise ValueError("Missing structured role output")
        return json.loads(calls[0]["function"]["arguments"])
    if data.get("stop_reason") not in {"end_turn", "tool_use"}:
        raise ValueError("Incomplete provider output")
    calls = [b for b in data.get("content", []) if b.get("type") == "tool_use"]
    if calls:
        if len(calls) != 1 or calls[0]["name"] != "submit_role_result":
            raise ValueError("Unexpected structured role output")
        return calls[0]["input"]
    # MiniMax may emit the same JSON as text with auto tool choice. Validate it at caller.
    if provider == "minimax":
        return json.loads("\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text"))
    raise ValueError("Missing structured role output")
