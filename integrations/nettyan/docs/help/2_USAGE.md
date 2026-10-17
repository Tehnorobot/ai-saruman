
# 2_Usage

## 1. Run workers

База (PostgreSQL и Redis) нужна почти всегда, остальное по выбору. Полностью
без контейнеров скрипт запускается только с `--no-redis` и урезанной озвучкой
и распознаванием — см. установку.

### 1.1 Docker-сервисы

Как настроить Docker — в [установке](0_INSTALLATION.md), раздел 6. Если
`docker-compose.override.yml` уже собран, поднимается всё одной командой из
корня репозитория:

```bash
docker compose up -d
```

Контейнеры должны работать ДО запуска основного скрипта.

### 1.2 Simple TTS server (NO Docker)

Вариант для ПРОСТОЙ более "слабой" версии TTS без интонаций и т.д.
Откройте отдельный терминал, выполните пункты этого файла 4.1, 4.2, 4.3.

## 2. Run game

Игровая часть — мод **UnionClef**: <https://github.com/3ndetz/unionclef>
(раньше назывался autoclef, старый репозиторий больше не обновляется).

1. Поставьте [Fabric Loader](https://fabricmc.net/) под версию Minecraft,
   указанную в заголовке релиза.
2. Скачайте [последний релиз](https://github.com/3ndetz/unionclef/releases/latest)
   и возьмите оттуда **только один файл** — основной, вида
   `unionclef-<версия_майнкрафта>-<версия_мода>.jar`.

   > В релизе рядом лежат `tungsten-*.jar` и `shredder-*.jar`. Их скачивать
   > **не нужно**: они уже собраны внутрь основного файла. Положить их рядом
   > с UnionClef нельзя, будет конфликт. Отдельно они пригодятся только тем,
   > кто хочет один из этих модов сам по себе, без UnionClef.

3. Положите скачанный jar в папку `.minecraft/mods/` и запустите игру.

Проверить, что мод поднялся, можно командой `@help` в игровом чате.
Внутри три подсистемы со своими префиксами команд: `@` — задачи бота,
`#` — перемещение по блокам, `;` — точное движение.

## 3. Configure .env

Удостоверьтесь, что вы правильно настроили `.env` файл.
После любых изменений в нём нужно:

- открыть НОВЫЙ терминал
- закрыть старый и старую программу, если была запущена.

В некоторых случаях требуется ПОЛНОСТЬЮ перезапустить VS Code или даже систему, если `.env` не обновился или изменена системная переменная пути.

## 4. Running script

### 4.0. Зайдите в папку репозитория из терминала

`cd этот_репозиторий` или где у вас папка с этим проектом.

### 4.1. Activate venv if not yet activated

For example, activating venv for windows

```bash
.venv\Scripts\activate
```

For linux / macOS

```bash
source .venv/bin/activate
```

### 4.2. Optional: Start simpletts server

If you DON'T using Fish TTS from Docker, RUN this simple version of TTS.

```bash
python simple_tts_server.py
```

And wait for starting, downloading and installing models.
You may need a proxy if not downloading.

Do NOT **stop** the terminal console where it is running! This server should run in a background.

### 4.3. Run main script

Option 1:

```bash
cd src
python main.py [params]
```

Option 2:

```bash
uv run nettyan [params]
```

Available params (сверено с `parse_args()` в `src/main.py`):

| Флаг | Длинный | Что делает |
|---|---|---|
| `-c` | `--chat` | мультиагент с ChatAgent, нативный tool calling — для современных LLM |
| `-m` | `--multi` | мультиагент |
| `-l` | `--old` | старый autogpt-агент |
| `-n` | `--no-agent` | не запускать игрового агента вообще |
| `-r` | `--no-stt` | без распознавания речи |
| `-o` | `--offline` | без соцсервисов (чат Twitch и прочее) |
| `-f` | `--no-filter` | без фильтрации |
| `-s` | `--stop-auto` | не стартовать агента автоматически |
| `-w` | `--warmup` | прогреть TTS после старта |
| | `--no-redis` | вместо Redis использовать multiprocessing Manager |

Флаги без агента можно комбинировать. Сухой тестовый прогон без распознавания
речи, соцсервисов и фильтра:

```bash
python main.py -rof
```

То же, но ещё и без Redis — если не поднимали контейнеры:

```bash
python main.py -rof --no-redis
```

Список выше сверен с кодом, но первоисточник всегда `src/main.py` — там же
описания флагов.

## Финал

Вы попробовали использовать скрипт и он у вас запустился, теперь можете вернуться в [содержание](index.md) и оттуда перейти к КАСТОМИЗАЦИИ.
