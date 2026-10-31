# Saruman · Minecraft-персонаж

Саруман играет в Minecraft через UnionClef, читает игровой чат и отвечает голосом.
Личность, имена и отношения находятся в `resources/Characters/Saruman/`.
Манера речи и банк предыстории: [характер Сарумана](resources/Characters/Saruman/README.md).
Две модели для персонажа: [PersonaAgent, ActionAgent и настройка провайдеров](docs/dev/ROLE_AGENTS.md).
Агентный цикл и игровые инструменты выполняются локальным runtime.

Деревья и бой переданы TreeGrow и Auto Defense; LLM обслуживает общение и остальные
просьбы игроков. [Модовый runtime: настройка, очередь и текущие ограничения](docs/mod-development/native-runtime/IMPLEMENTATION.md).

Рабочая папка — `integrations/saruman/` в репозитории `ai-saruman`.
Лаборатория в корне — отдельное приложение с собственным Python-окружением.

## Первый запуск: Windows, трек Rich

Полная подготовка: [Windows-инструкция](docs/SARUMAN_WINDOWS.md).
Нужны Python 3.10.9–3.10.x, uv, FFmpeg, Docker Desktop с GPU-доступом,
Minecraft с UnionClef и настроенные аудиоустройства.

1. Создай `.env.saruman` из `.env.saruman.example`, если его ещё нет.
2. Заполни пароль БД, OBS и реальные имена аудиоустройств.
3. Авторизуй Codex в Windows через ChatGPT: `codex login`.
4. Подготовь веса Fish Speech 1.5 в `data/fish-tts/checkpoints` по инструкции курса.
5. Из этой папки выполни:

```powershell
uv sync --locked --python 3.10 --no-default-groups --group collectors
docker compose --env-file .env.saruman -f compose.saruman.yml --profile rich --profile voice up -d --build
.\scripts\start-saruman.ps1 -Doctor
.\scripts\start-saruman.ps1
```

После запуска открой `http://127.0.0.1:22228`. Модельный цикл изначально на паузе;
кнопка Start начинает работу и расходует лимиты подписки.

В Rich используется Fish Speech `v1.5.1-dev` на GPU и Parakeet TDT v3 0.6B INT8
на CPU. `STT_COMPUTE_DEVICE` не переключает Parakeet на CUDA. Голосовые WAV пока
тестовые, не изображают голос конкретного актёра; все лежат внутри профиля Сарумана.

Второй способ запуска сервисов: скопируй `docker-compose.override.yml.example`
в локальный `docker-compose.override.yml` и выполни
`docker compose --env-file .env.saruman up -d --build`. Он использует те же
определения сервисов и тот же том PostgreSQL, что и `compose.saruman.yml`.
`simple` — отдельный запасной CPU TTS; не запускай его одновременно с `rich`.

## LLM Сарумана

```dotenv
SARUMAN_CHARACTER_NAME=Saruman
CORE_LLM_MODEL_NAME=codex/gpt-6-sol
CODEX_REASONING_EFFORT=low
VISION_LLM_MODEL_NAME=NONE
EMBEDDINGS_MODEL=NONE
```

`auto` запрашивает `model/list` у Codex app-server и выбирает запись с `isDefault`,
либо первую доступную запись каталога. Для закрепления модели укажи
`codex/<точный-ID-из-каталога>`. Каталог не гарантирует доступ аккаунта к модели.
Адаптер требует ChatGPT-авторизацию и не переключается на API-ключ при ошибке.
Codex предлагает вызовы инструментов в JSON; проверяет и выполняет их runtime Сарумана.

Основная конфигурация — `.env.saruman`. `.env.example` содержит те же значения;
`.env` и `.env.postgres` читаются только как запасные источники. Переменные процесса
имеют приоритет. Локальные файлы с секретами исключены из Git.
Выбор модели в лаборатории не меняет модель игрового персонажа.

## Подсистемы

| Путь | Назначение |
|---|---|
| `src/saruman/` | Запуск, doctor, Codex-адаптер и шлюз Minecraft |
| `src/agent/` | Агентный цикл, промпты и инструменты |
| `resources/Characters/Saruman/` | Личность, имена, идеи, отношения и голос |
| `src/data_collectors/` | Игровые, голосовые и социальные события |
| `src/data_flow/` | PostgreSQL, Redis и контекст |
| `src/tts/`, `docker/stt/` | Синтез и распознавание речи |
| `src/front/`, `src/live2d/` | HUD и аватар |

Команда `saruman-minecraft` запускает launcher (`doctor` или `run`).
`saruman-runtime` — прямой вход в движок с его аргументами; для обычного запуска
используй launcher. Команда `saruman` в корневом окружении относится к лаборатории.

## Проверки и границы готовности

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_saruman.py -v
```

Тесты используют подставные RPC и gateway. Реальную слышимость, движение в Minecraft,
GPU и голосовой маршрут проверяй по checklist в Windows-инструкции.
Runtime пока работает на Windows-хосте; контейнеры обслуживают вспомогательные сервисы.
МАС, оркестратор, Telegram-агенты и автоматическая синхронизация памяти лаборатории
с PostgreSQL не реализованы этим модулем.

## Документация

- [Установка](docs/help/0_INSTALLATION.md)
- [Настройки](docs/help/1_CONFIG.md)
- [Запуск](docs/help/2_USAGE.md)
- [Свои звуки и голос](docs/help/instructions/SOUNDS.md)
- [Аудит переименования](docs/ai/RENAME_AUDIT.md)
- [Происхождение кода](SOURCE.md)

## Лицензия

Код распространяется по [MIT](LICENSE); сведения об исходных авторах сохранены в
[AUTHORS](AUTHORS). Переименование не меняет авторство исходного кода.
Поставляемые звуковые сигналы и тестовые реплики синтезированы проектом;
для собственных голосовых записей и Live2D-моделей проверь права на использование.
