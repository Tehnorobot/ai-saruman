# Независимая проверка тестов

Дата: 2026-10-20. Проверяющий: отдельный verifier. Исходники, `.env`, секреты и существующие отчёты не изменялись. Рабочие сервисы Compose, Codex и Minecraft не запускались.

## Результаты

| Проверка | Статус | Доказательство и границы |
| --- | --- | --- |
| Существующие unit/integration tests на Linux | **PASS: 20/20** | Python 3.12 в service image, исходники подключены read-only, сеть отключена. `Ran 20 tests in 3.495s`, `OK`. Включён реальный FFmpeg/FFprobe, S3/Codex подменены. |
| Тесты внутри неизменённого service image | **FAIL** | `Ran 5 tests`, `FAILED (errors=2)`: `ModuleNotFoundError: agent` и `ModuleNotFoundError: worker`. Dockerfile.service копирует `tests`, но не копирует импортируемые ими пакеты. Команда из README_RU.md:125 неработоспособна без дополнительных source mounts/исправления образа. |
| Тесты непосредственно на Windows | **FAIL: 17 passed, 1 failed, 1 error, 1 skipped** | Python 3.10.15, requirements установлены в отдельный временный workspace target. Два теста создают POSIX `#!/bin/sh` executable `codex`, который Windows не запускает; отсюда FAIL статуса fake agent и отсутствие observed-argv.txt. FFmpeg не найден, video test skipped. Это ограничение переносимости тестового стенда; Linux-прогон выше проходит. |
| Сборка Dockerfile.service | **PASS** | `docker build -f Dockerfile.service -t codex-lab-service:audit .`, exit 0. Большинство слоёв взяты из build cache; это не проверка чистой сборки зависимостей с нуля. |
| Доступность Docker | **PASS** | Docker Desktop 4.95.0, Engine 29.8.2, Compose v5.6.0. В sandbox доступ к API заблокирован; разрешённый read-only запуск вне sandbox подтвердил работающий daemon. |
| Структура Compose без .env | **PASS** | `docker compose --env-file NUL config --no-env-resolution --no-interpolate --quiet`, exit 0. Интерполяция пользовательских значений и запуск контейнеров не проверялись. |
| Синтаксис Python | **PASS** | `ast.parse` всех 12 исходных `.py` в app/agent/worker/common/backup/storage/tests на Python 3.10.15. |
| Синтаксис PowerShell | **PASS** | `System.Management.Automation.Language.Parser.ParseFile` для START_WINDOWS.ps1: ошибок нет. Скрипт не выполнялся. |
| Синтаксис JavaScript | **PASS** | Inline script из app/index.html передан `node --check` в no-network контейнере Node 22.16.0, exit 0. Браузерные взаимодействия не проверены этой проверкой. |

## Воспроизводимые ошибки за пределами существующих тестов

Независимо выполнен `audit/reproduce_storage_backup.py` на Python 3.10.15. Он использует только синтетические данные и mock S3/FFprobe.

- **FAIL — целостность manifest при параллельной записи.** Между `tar.add()` и вычислением хеша файл меняется: в архиве `version = 1`, на диске `version = 2`, `manifest_matches_archive: false`. Хеш manifest не соответствует байтам архивной копии.
- **FAIL — S3 dedup при изменении конфигурации.** После смены `old-bucket/old-prefix` на `new-bucket/new-prefix` повторная загрузка того же файла возвращает `ALREADY_UPLOADED` и старые bucket/key; `upload_calls` остаётся равным 1. Новое место назначения не заполняется.

Дополнительно независимо выполнены два reproducer-скрипта других reviewers. Exit 0 здесь означает успешное воспроизведение ошибки; это не PASS продукта.

`audit/reproduce_runtime.py`: Python 3.12, no-network Linux container, read-only source mount, временная SQLite:

- **FAIL — pause/resume:** после `USER_PAUSED` повторный запрос того же round возвращает старый `CANCELLED`; нового request нет.
- **FAIL — restart during verification:** состояние `round=1, max_rounds=1, phase=verifying` после перезапуска становится `FINISHED`, `checks_called=false`, verification-файл отсутствует.
- **FAIL — Kotlin Gradle Minecraft detection:** для `build.gradle.kts` с Fabric и `minecraft_version=1.21.11` вызываются только `gradle_build`/`production_jar`; `minecraft_client_gametest` отсутствует. Сам Gradle в этом узком тесте mock.
- **FAIL — trust boundary verifier:** реальный дочерний процесс через `engine.command` изменяет управляющую SQLite: `controller_phase=changed_by_project_test`. Записывалась только синтетическая БД во временной директории.

`audit/reproduce_queue_ui.py`: Windows Python 3.10.15, реальная SQLite и неизменённая функция API action, извлечённая из AST; HTTP/browser не эмулируются:

- **FAIL — crash after pause/stop:** при истёкшей lease задача остаётся `RUNNING` с установленным control flag, `db.claim` возвращает `None`, resume даёт 409. Проверены оба действия.
- **FAIL — control/finish race:** если worker записывает `FINISHED` между чтением и update в action, pause возвращает задачу в `RUNNING`, сохраняя `phase=max_rounds_reached`.
- **FAIL — dashboard journal pagination:** повторный вызов `events_list` без after возвращает те же первые 500 записей; последние три доступны только с after. Исходный JS действительно запрашивает `/events` без after и заменяет содержимое панели. Это сочетание проверки настоящей БД и исходного клиента, без визуального browser test.

## Команды воспроизведения

Рабочий Linux-прогон исходных тестов (PowerShell, из корня проекта):

```powershell
docker build -f Dockerfile.service -t codex-lab-service:audit .
docker run --rm --network none -e LAB_DB=/tmp/lab.sqlite3 -e PYTHONPATH=/audit -v "${PWD}:/audit:ro" -w /audit codex-lab-service:audit python -m unittest discover -s tests -v
```

Воспроизведение дефекта комплектования service image без source mounts:

```powershell
docker run --rm --network none -e LAB_DB=/tmp/lab.sqlite3 codex-lab-service:audit python -m unittest discover -s tests -v
```

## Что результаты не доказывают

20 успешных тестов не проверяют совместимость настоящего Codex CLI, полный цикл агент → сборка → независимые проверки, восстановление после остановки/просроченной lease, гонки pause/resume/settings, границы доверия к генерируемым тестам, старт всей Compose-схемы, реальный облачный S3 и gameplay Minecraft. В тесте `test_events_cannot_be_faked_by_model_output` проверяется лишь пустое начальное состояние, без попытки подделки результата. Traversal test использует обычный URL с `../`, который HTTP-клиент может нормализовать до обработки сервером. Результаты существующего VALIDATION_REPORT.md не использовались как доказательство текущего прогона.
