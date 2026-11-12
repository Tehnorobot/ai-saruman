"""Read-only preflight. No model generation, migration, game action or file writes."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def filter_is_ready(base_url: str) -> bool:
    """A reloader's listening socket is not evidence that the filter app started."""
    try:
        with urlopen(base_url.rstrip("/") + "/health", timeout=3) as response:
            result = json.load(response)
        return (
            isinstance(result, dict)
            and result.get("status") == "ok"
            and result.get("models_loaded") is True
        )
    except (OSError, ValueError):
        return False


def wait_for_filter(base_url, timeout, *, clock=time.monotonic, sleep=time.sleep, cancelled=lambda:False):
    """Cold model loading may outlive TCP startup; wait without generating content."""
    deadline = clock() + timeout
    while not cancelled():
        if filter_is_ready(base_url):return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(2, remaining))
    return False


def validate_profile(root: Path) -> list[str]:
    import yaml

    name = os.getenv("SARUMAN_CHARACTER_NAME", "Saruman")
    if not name or Path(name).name != name or name in (".", "..") or "\\" in name:
        return ["Имя профиля должно быть именем каталога, а не путём."]
    home = root / "resources" / "Characters" / name
    errors = []
    try:
        from character.character_manager import CharacterManager

        character = CharacterManager(name, force_new=True)
        nick = character.get_names().Minecraft
        if not nick or nick not in character.get_names().Aliases:
            errors.append("Minecraft-ник должен присутствовать в Aliases.")
        prompt = character.read_character_prompt()
        if not prompt.strip():
            errors.append("Пустой streamer_abstract.")
        ideas = character.read_character_ideas()
        if not ideas.get("base"):
            errors.append("Нет базовых идей персонажа.")
        tags = character.read_character_user_status_tags()
        if any(
            not tags.get(k, {}).get(g)
            for k in ("agressive", "negative", "neutral", "positive", "love")
            for g in ("m", "f")
        ):
            errors.append("Неполная таблица отношений.")
        with (home / "Voice" / "refs_manifest.yml").open(encoding="utf-8") as stream:
            refs = yaml.safe_load(stream)
        if "neutral" not in refs:
            errors.append("Нет neutral в голосовом манифесте.")
        for key in refs:
            transcript, wav = character.resolve_voice_ref(key)
            if not transcript or not Path(wav).is_file():
                errors.append(f"Нет WAV или расшифровки эмоции {key}.")
    except (OSError, ValueError, TypeError, KeyError) as error:
        errors.append(f"Профиль не читается: {type(error).__name__}")
    return errors


def check(live=False, no_stt=False, no_filter=False, no_redis=False) -> bool:
    from saruman.launcher import ROOT

    failures = []

    def report(label, ok, hint=""):
        print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f": {hint}" if hint else ""))
        if not ok:
            failures.append(label)

    report(
        "Python 3.10.9+ (<3.11)",
        sys.version_info[:2] == (3, 10) and sys.version_info[:3] >= (3, 10, 9),
    )
    modules = (
        "yaml",
        "dotenv",
        "pydantic_settings",
        "langchain_core",
        "langchain_litellm",
        "langchain_openai",
        "gradio",
        "gradio_client",
        "pandas",
        "numpy",
        "soxr",
        "pygame",
        "soundcard",
        "sounddevice",
        "PIL",
        "py4j",
        "sqlalchemy",
        "alembic",
        "psycopg2",
        "redis",
        "mistralai",
        "obsws_python",
        "mss",
        "socketio",
        "requests",
        "faiss",
        "jsonschema",
        "pydub",
        "twitchAPI",
    )
    missing = [m for m in modules if importlib.util.find_spec(m) is None]
    report(
        "Python-зависимости",
        not missing,
        ", ".join(missing) if missing else "установлены",
    )
    if not any(m in missing for m in ("yaml", "pydantic_settings", "langchain_core")):
        try:
            errors = validate_profile(ROOT)
        except Exception as error:  # noqa: BLE001 - upstream CharacterManager raises plain Exception
            errors = [f"Не удалось загрузить профиль: {type(error).__name__}"]
        report("Профиль персонажа", not errors, "; ".join(errors))
    report("Пароль PostgreSQL задан", bool(os.getenv("POSTGRES_PASSWORD")))
    model = os.getenv("CORE_LLM_MODEL_NAME", "")
    hybrid = os.getenv("SARUMAN_AUTOMATION_MODE", "mods").lower() == "mods"
    requires_codex = model.startswith("codex/")
    from saruman.role_settings import read_profiles
    try:
        saved = read_profiles()
        active = ("persona", "action") if hybrid else ("legacy_core", "legacy_planner", "legacy_reaction", "legacy_chat")
        profiles = {role: saved[role] for role in active}
        requires_codex = any(p["provider"] == "codex" for p in profiles.values())
        for role, profile in profiles.items():
            report(f"Модель роли {role}", True, f"{profile['provider']} / {profile['model']} / {profile['reasoning']}")
            if profile["provider"] != "codex":
                prefix = profile["provider"].upper()
                from saruman.role_client import endpoint
                try:
                    endpoint(profile["provider"])
                    report(f"Настройки {role}: {prefix}", bool(os.getenv(prefix+"_API_KEY")), "Ключ проверяется только на наличие; запросы API не отправляются")
                except ValueError:
                    report(f"Настройки {role}: {prefix}", False, "Проверьте адрес провайдера в .env.saruman")
    except (ValueError, OSError):
        report("Модели ролей", False, "Проверьте сохранённые настройки ролей")
    if hybrid:
        compiler = os.getenv("SARUMAN_JAVAC") or shutil.which("javac")
        report("Модовый адаптер: javac JDK 21+", bool(compiler and Path(compiler).is_file()), "SARUMAN_JAVAC или javac в PATH")
    if requires_codex:
        report("Codex CLI в PATH", shutil.which("codex") is not None)
    print(
        "[NOTE] Fish TTS выбирает голосовой референс по эмоции из Voice/refs_manifest.yml."
    )
    print(
        "[NOTE] Скин, заклинания и способности задаются игрой; профиль их не добавляет."
    )
    if not live:
        print(
            "[NOTE] Сервисы, вход Codex и аудиоустройства не проверены. Используй doctor --live."
        )
        return not failures

    if requires_codex and shutil.which("codex"):
        try:
            from saruman.codex_rpc import Rpc, require_subscription

            with Rpc() as rpc:
                require_subscription(
                    rpc.request("account/read", {"refreshToken": False}).get("account")
                )
            report("Codex: ChatGPT-вход", True)
        except RuntimeError as error:
            report("Codex: ChatGPT-вход", False, str(error))
        except OSError:
            report(
                "Codex: ChatGPT-вход",
                False,
                "выполни codex login; API-key fallback отсутствует",
            )
    endpoints = [
        (
            "Minecraft gateway",
            os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
            int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
        )
    ]
    for label, key, fallback in (
        ("TTS", "TTS_ENDPOINT_URL", "http://127.0.0.1:22231"),
        ("STT", "PARAKEET_API_URL", "http://127.0.0.1:22233"),
        ("Фильтр", "FILTER_BASE_URL", "http://127.0.0.1:22230"),
    ):
        if (label == "STT" and no_stt) or (label == "Фильтр" and no_filter):
            continue
        url = urlparse(os.getenv(key, fallback))
        endpoints.append(
            (
                label,
                url.hostname or "127.0.0.1",
                url.port or (443 if url.scheme == "https" else 80),
            )
        )
    if not no_redis:
        url = urlparse(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
        endpoints.append(("Redis TCP", url.hostname or "127.0.0.1", url.port or 6379))
    for label, host, port in endpoints:
        try:
            with socket.create_connection((host, port), timeout=2):
                pass
            report(label + " TCP", True)
        except OSError:
            report(label + " TCP", False, "порт недоступен")
    if not no_filter:
        filter_ready = filter_is_ready(os.getenv("FILTER_BASE_URL", "http://127.0.0.1:22230"))
        report(
            "Фильтр: HTTP и модели готовы",
            filter_ready,
            "" if filter_ready else "проверь docker compose logs filter; открытого TCP-порта недостаточно",
        )
    # Port-open does not prove a compatible mod or an active world.
    if importlib.util.find_spec("py4j"):
        try:
            from py4j.java_gateway import GatewayParameters, JavaGateway
            from py4j.protocol import Py4JError

            gateway = JavaGateway(
                gateway_parameters=GatewayParameters(
                    address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
                    port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
                    read_timeout=2,
                )
            )
            try:
                report("Minecraft: мир открыт", bool(gateway.entry_point.inGame()))
                if os.getenv("SARUMAN_AUTOMATION_MODE", "mods").lower() == "mods":
                    try:
                        from saruman.mod_adapter import probe_native_mods
                        artifacts = probe_native_mods(gateway)
                        report("Minecraft: API Auto Defense и TreeGrow", True, ", ".join(artifacts.values()))
                    except (Py4JError, ValueError, TypeError, RuntimeError, OSError) as error:
                        detail = " ".join(str(error).split())[:500]
                        report("Minecraft: API Auto Defense и TreeGrow", False, detail or "Нужны совместимые JAR; см. native-runtime/IMPLEMENTATION.md")
            finally:
                gateway.close()  # Never shutdown(): the mod owns the gateway.
        except (Py4JError, OSError, ValueError, TypeError):
            report(
                "Minecraft: протокол UnionClef",
                False,
                "запусти совместимый клиент и войди в мир",
            )
    if importlib.util.find_spec("psycopg2"):
        try:
            import psycopg2

            connection = psycopg2.connect(
                host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
                port=os.getenv("POSTGRES_PORT", "5432"),
                dbname=os.getenv("POSTGRES_DB", "saruman"),
                user=os.getenv("POSTGRES_USER", "saruman"),
                password=os.getenv("POSTGRES_PASSWORD", ""),
                connect_timeout=2,
            )
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    report("PostgreSQL: авторизация", cursor.fetchone() == (1,))
            finally:
                connection.close()
        except (psycopg2.Error, OSError, ValueError):
            report(
                "PostgreSQL: авторизация",
                False,
                "проверь настройки; секреты не выводятся",
            )
    print(
        "[NOTE] TCP TTS/STT не проверяет качество/модель. OBS, VTube и слышимость голоса проверь по Windows checklist."
    )
    return not failures
