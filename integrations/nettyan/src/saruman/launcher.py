"""Configure Saruman before importing NetTyan's side-effectful entry point."""

from __future__ import annotations

import argparse
import multiprocessing
import os
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
    os.environ.setdefault("NETTYAN_CHARACTER_NAME", "Saruman")


def runtime_args(args) -> list[str]:
    result = ["nettyan", "--chat"]
    if not args.auto_start:
        result.append("--stop-auto")
    if not args.with_social:
        result.append("--offline")
    for option in ("no_stt", "no_filter", "no_redis"):
        if getattr(args, option):
            result.append("--" + option.replace("_", "-"))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Саруман: Minecraft runtime на базе NetTyan"
    )
    parser.add_argument("command", choices=("doctor", "run"))
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
    try:
        load_environment(env_path)
        from saruman.doctor import check

        # Run always validates services, but never requests a model generation.
        if not check(
            live=args.live or args.command == "run",
            no_stt=args.no_stt,
            no_filter=args.no_filter,
            no_redis=args.no_redis,
        ):
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
        from main import main as nettyan_main

        nettyan_main()
        return 0
    except (ValueError, ImportError) as error:
        print(f"[FAIL] {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
