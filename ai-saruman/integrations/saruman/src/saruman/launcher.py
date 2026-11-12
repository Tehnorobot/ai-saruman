"""Configure the character before importing the runtime entry point."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_environment(path: Path) -> None:
    from dotenv import load_dotenv

    if not path.is_file():
        raise ValueError(
            "Нет .env.saruman. Скопируй .env.saruman.example и заполни настройки."
        )
    load_dotenv(path, override=False)
    os.environ.setdefault("SARUMAN_CHARACTER_NAME", "Saruman")


def runtime_args(args) -> list[str]:
    result = ["saruman-runtime", "--chat"]
    if not args.auto_start:
        result.append("--stop-auto")
    if not args.with_social:
        result.append("--offline")
    for option in ("no_stt", "no_filter", "no_redis"):
        if getattr(args, option):
            result.append("--" + option.replace("_", "-"))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Саруман: игровой персонаж Minecraft")
    parser.add_argument("command", choices=("doctor", "run", "reset-memory"))
    parser.add_argument(
        "--cancel",
        action="store_true",
        help="reset-memory: отменить запланированный сброс",
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.saruman")
    parser.add_argument(
        "--live", action="store_true", help="doctor: read-only проверки сервисов"
    )
    parser.add_argument(
        "--auto-start", action="store_true", help="Сразу включить модельный цикл"
    )
    parser.add_argument(
        "--with-social",
        action="store_true",
        help="Включить настроенные стримерские сервисы",
    )
    parser.add_argument("--no-stt", action="store_true")
    parser.add_argument("--no-filter", action="store_true")
    parser.add_argument("--no-redis", action="store_true")
    args = parser.parse_args(argv)
    env_path = args.env_file.resolve()
    os.chdir(ROOT)
    managed = None
    instance = None
    try:
        load_environment(env_path)
        from saruman.managed import configure, FileOwnerLock
        if args.command == "run":
            instance = FileOwnerLock(ROOT / "data/minecraft-runtime.lock")
            instance.acquire()
        managed = configure(ROOT) if args.command == "run" else None
        if managed:
            # Check the configured port before imports can initialize/flush shared Redis.
            # An independently launched game process is never adopted or terminated blindly.
            port = int(os.getenv("GRADIO_SERVER_PORT", "22228"))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    managed.phase_changed("external", "Игровой процесс уже запущен отдельно; закройте его перед запуском из Lab")
                    managed.step()
                    return 1
            except OSError:
                pass
        if args.command == "reset-memory":
            from saruman.memory_reset import cancel_reset, request_reset

            if args.cancel:
                cancel_reset()
                print("Сброс игровой памяти отменён.")
            else:
                request_reset()
                print(
                    "Сброс памяти запланирован на следующий полный запуск Python runtime."
                )
            return 0
        from saruman.doctor import check, filter_is_ready, wait_for_filter

        if args.command == "run" and not args.no_filter:
            wait_seconds = float(os.getenv("SARUMAN_FILTER_STARTUP_WAIT", "180"))
            if not 0 <= wait_seconds < float("inf"):
                raise ValueError("SARUMAN_FILTER_STARTUP_WAIT должен быть конечным числом >= 0")
            base = os.getenv("FILTER_BASE_URL", "http://127.0.0.1:22230")
            if wait_seconds and not filter_is_ready(base):
                print(f"[WAIT] Фильтр загружает модели; ожидаю готовность до {wait_seconds:g} с. SARUMAN_FILTER_STARTUP_WAIT меняет ожидание.", flush=True)
                wait_for_filter(base, wait_seconds)

        # Run always validates services, but never requests a model generation.
        if not check(
            live=args.live or args.command == "run",
            no_stt=args.no_stt,
            no_filter=args.no_filter,
            no_redis=args.no_redis,
        ):
            if managed:
                managed.phase_changed("waiting_services", "Игровая проверка не пройдена; проверьте Doctor и службы")
                managed.step()
            return 1
        if args.command == "doctor":
            return 0
        if sys.platform != "win32":
            print(
                "Запуск игрового/аудио runtime предназначен для native Windows; Ubuntu — разработка и тесты."
            )
            return 1
        multiprocessing.freeze_support()
        multiprocessing.set_start_method("spawn", force=True)
        sys.argv = runtime_args(args)
        if managed:
            # The Lab owns Start/Stop. The legacy automatic loop also stops the supervisor
            # periodically and must not run alongside the explicit lifecycle bridge.
            if "--stop-auto" not in sys.argv:
                sys.argv.append("--stop-auto")
            managed.phase_changed("starting", "Запуск игрового обработчика")
        from main import main as saruman_main

        saruman_main()
        return 0
    except (ValueError, ImportError, RuntimeError) as error:
        print(f"[FAIL] {error}")
        return 1
    finally:
        if managed:
            managed.close()
        if instance:
            instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
