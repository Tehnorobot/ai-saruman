"""Protocol-level checks from the same environment as the game worker."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen

from saruman.doctor import filter_is_ready, postgres_is_ready
from saruman.launcher import load_environment


def redis_is_ready():
    from redis import Redis

    with Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                        socket_connect_timeout=2, socket_timeout=2) as client:
        return client.ping() is True


def get_json(url, path):
    with urlopen(url.rstrip("/") + path, timeout=3) as response:
        data = json.loads(response.read(2 * 1024 * 1024))
    return data if isinstance(data, dict) else {}


def tts_is_ready():
    config = get_json(os.getenv("TTS_ENDPOINT_URL", "http://127.0.0.1:22231"), "/config")
    return any(isinstance(item, dict) and item.get("api_name") in ("partial", "/partial")
               for item in config.get("dependencies", []))


def stt_is_ready():
    base = os.getenv("PARAKEET_API_URL", "http://127.0.0.1:22233").rstrip("/")
    data = get_json(base.removesuffix("/v1/audio/transcriptions"), "/health")
    return data.get("status") == "ok" and data.get("model_loaded") is True and data.get("vad_loaded") is True


CHECKS = {
    "postgres": (postgres_is_ready, "Вход в базу и SELECT 1 подтверждены.", "База не ответила на SELECT 1 с игровыми настройками."),
    "redis": (redis_is_ready, "Получен ответ на PING.", "Redis не подтвердил PING с игровыми настройками."),
    "tts": (tts_is_ready, "Fish Speech подтвердил API синтеза.", "Fish Speech не подтвердил API синтеза."),
    "stt": (stt_is_ready, "Parakeet и детектор речи загружены.", "Parakeet не отвечает или ещё загружает модели."),
    "filter": (lambda: filter_is_ready(os.getenv("FILTER_BASE_URL", "http://127.0.0.1:22230")),
               "API фильтра и модели готовы.", "Фильтр не отвечает или ещё загружает модели."),
}


def probe_service(identifier):
    probe, success, failure = CHECKS[identifier]
    try:
        ready = probe()
    except Exception:
        # Provider errors can contain addresses or credentials. Return only fixed diagnostics.
        ready = False
    return identifier, {"status": "ready" if ready else "error", "detail": success if ready else failure}


if __name__ == "__main__":
    load_environment(Path(".env.saruman"))
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as executor:
        print(json.dumps(dict(executor.map(probe_service, sys.argv[1:])), ensure_ascii=False))
