# Саруман: первый запуск Minecraft runtime

Это игровой runtime **Сарумана**. Сохранены
LangChain, инструменты, PostgreSQL, Redis, TTS/STT, HUD, OBS и VTube Studio.
Игровые события и команды идут через штатный Py4J-мост UnionClef.
Модельный провайдер `codex/...` добавлен отдельно; старые LiteLLM-провайдеры остаются.

## Что реализовано

- `resources/Characters/Saruman/`: личность из нашего PERSONA.md, идеи, отношения,
  псевдонимы и голосовой манифест. Все режимы личности сохраняют Сарумана.
- `src/saruman/`: запуск, проверка окружения, конфигурируемый шлюз и LangChain/Codex-адаптер.
- Модель предлагает JSON tool calls; адаптер валидирует имя и аргументы, а **Saruman**
  выполняет действие. Codex shell/desktop tools и approvals отключены.
- Ответы tool, история и изображения передаются обратно в модель. Usage возвращается
  как наблюдаемая метрика LangChain; если usage нет, точные числа не выдумываются.
- `compose.saruman.yml`: отдельные сервисы с loopback-портами и постоянным томом PostgreSQL.
- `scripts/start-saruman.ps1`: native Windows launcher; по умолчанию модельный цикл
  на паузе, Twitch/DonationAlerts не включаются без явной настройки.

Это ещё **не контейнер отдельного агента**. На первом этапе игровой агент,
Minecraft, аудиоустройства, OBS и VTube работают на Windows-хосте; контейнеры
обслуживают БД, фильтр и голосовые модели. Разделение агентного слоя и host bridge —
следующий этап после проверки реального игрового/голосового контура.
Telegram, видеопроизводство, публикация YouTube и единая лаборатория не добавлялись.

## 1. Подготовить Windows

Клонируй основной репозиторий `https://github.com/Tehnorobot/ai-saruman.git`.
Работай в его папке **`integrations/saruman`**, например
`C:\Repos\ai-saruman\integrations\saruman`, а не в корне лаборатории.
Оба Python-проекта содержат пакет `saruman`; не устанавливай их в одну .venv.
Saruman теперь входит в основной репозиторий обычными файлами: отдельный clone,
fork или `git submodule update` для него не требуется.
Разработка в Ubuntu возможна, но Windows имеет своё окружение и свои секреты.

1. Установи uv, Git, FFmpeg и Docker Desktop. Python-приложение запускай нативно,
   из PowerShell, не из WSL. Внутренний Linux/WSL2 backend Docker Desktop — отдельная вещь.
2. По инструкции курса установи Minecraft **Java 1.21.11 + Fabric**, Java 21,
   подходящий UnionClef и модпак, включая Simple Voice Chat. `@help` проверяет наличие мода.
3. В лаунчере выбери ник `Saruman`. Если нужен другой свободный ник, поменяй
   `Names.Minecraft` и `Names.Aliases` в `char_config.yml` согласованно.
4. Установи виртуальный аудиокабель и перезагрузи Windows.
5. Установи OBS; включи WebSocket, пароль и игровую сцену. Порт примера — 4455.
6. Для аватара установи VTube Studio и Live2D-модель; включи API и согласуй порт
   с `VTUBE_WEBSOCKET_URL`. При первом запросе подтверди плагин в VTube Studio.
7. Установи Codex CLI **в Windows**, выполни `codex login` через ChatGPT.
   Вход из Ubuntu автоматически в Windows не переносится. Учётные файлы не копируй в Git.

Ссылки курса: https://course.personalabs.ru/intensive и
https://course.personalabs.ru/instructions/minecraft . Проверяй совместимость
конкретного JAR: профиль не добавляет игровых заклинаний, маунтов, скин или новый тип NPC.
Персонаж входит на сервер как отдельный игрок. Первые действия проверяй в своём тестовом мире.

## 2. Конфигурация

```powershell
cd C:\Repos\ai-saruman\integrations\saruman
Copy-Item .env.saruman.example .env.saruman
```

Если `.env.saruman` уже существует, не перезаписывай его: отредактируй нужные поля.
Впиши уникальный `POSTGRES_PASSWORD`, пароль OBS и реальные имена звуковых устройств.
Не используй чужие `GodUsers`: по умолчанию список доверенных операторов пуст.

Внешние переменные окружения имеют приоритет над файлом. Старые `.env`/`.env.postgres`
не должны подменять явно заданные значения; launcher загружает `.env.saruman` первым.
Проверь старые экспортированные переменные, если выбран не тот профиль или сервер БД.

`CORE_LLM_MODEL_NAME=codex/auto` выбирает default из каталога Codex. Можно закрепить
`codex/<id-доступной-модели>`. Каталог не гарантирует доступ: это подтверждается
успешным ответом. Никакого fallback на платный API-ключ нет. Лимиты подписки остаются.

Адаптер возвращает **буферизованный** ответ после валидации JSON, не live-токены.
У каждого вызова новый процесс/thread; прерывание текущей генерации кнопкой Stop
пока не реализовано. Таймаут ограничен `CODEX_TURN_TIMEOUT`. Не включай 24/7
автономный цикл до проверки частоты вызовов. Включённый Start может расходовать подписку.
Vision можно включить отдельно, указав совместимую модель в `VISION_LLM_MODEL_NAME`.
Embeddings/RAG для первого запуска выключены (`EMBEDDINGS_MODEL=NONE`), код сохранён;
для RAG нужен отдельно настроенный embedding provider — подписочный вход не заменяет его.

## 3. Поднять сервисы

Для выбранного трека Rich используй Fish Speech 1.5.1 на GPU и Parakeet на CPU.
Подготовь checkpoints Fish до запуска. При 8 GB VRAM проверяй запас памяти вместе с игрой и аватаром.

```powershell
docker compose --env-file .env.saruman -f compose.saruman.yml --profile rich --profile voice up -d --build
docker compose --env-file .env.saruman -f compose.saruman.yml --profile rich --profile voice ps
uv sync --locked --python 3.10 --no-default-groups --group collectors
```

Группа collectors пока нужна даже при выключенном Twitch: upstream импортирует
её модули из main.py. STT в этом запуске — штатный streaming Parakeet-контейнер;
настройка STT_BACKEND не переключает активный run_voice_processor на faster-whisper.
Для первого запуска предусмотрен именно Parakeet. Локальный STT-код не удалён.

Профили `simple` и `rich` **взаимоисключающие**: оба используют 22231.
Rich — исходный Fish образ курса; требует поддерживаемой NVIDIA/CUDA, GPU-доступа
Docker и подготовленных checkpoints в `data/fish-tts/checkpoints` по инструкции Fish.
Если ранее включал запасной CPU TTS, сначала останови `tts-simple`, затем включай `rich`.
Реальный GPU-запуск, загрузка весов и задержка голоса требуют проверки на твоём компьютере.

Compose-проект `saruman` использует том `saruman_saruman_db` для памяти/отношений. `docker compose down` его сохраняет;
**не используй `down -v`, если нужно сохранить данные**. Перезапуск Saruman очищает
оперативный контекст Redis штатным `flush_on_start`, но не PostgreSQL.
Память из `agent-workplace/` в корне ai-saruman в эту БД автоматически не импортируется.

## 4. Проверка и запуск

Открой Minecraft, войди в тестовый мир/сервер. Затем:

```powershell
.\scripts\start-saruman.ps1 -Doctor
.\scripts\start-saruman.ps1
```

Doctor проверяет версию Python, импорты, профиль, голосовые файлы и настройки.
Live-проверка дополнительно читает account/read Codex, доступность TCP-сервисов,
`inGame()` мода и `SELECT 1` PostgreSQL. **Не генерирует ответ, не запускает миграции
и не отдаёт игровые команды.** Открытый TCP TTS/STT не доказывает исправность модели.

Первый запуск остановится при провале обязательных preflight-проверок. Gradio —
`http://127.0.0.1:22228`. Нажми штатный **Start**, когда готов расходовать лимит модели.
Для явного автостарта: `start-saruman.ps1 -AutoStart`.
Для текстового этапа без распознавания речи: добавь `-NoStt` и к Doctor, и к запуску.
`-NoFilter` допустим для диагностики в приватном мире, но отключает модерацию.

Если PowerShell блокирует скрипт, не меняй глобальную execution policy; эквивалент:

```powershell
uv run --locked --python 3.10 --no-default-groups --group collectors saruman-minecraft doctor --live
uv run --locked --python 3.10 --no-default-groups --group collectors saruman-minecraft run
```

## 5. Обязательный Windows smoke test

- [ ] Doctor показывает профиль Saruman, подключение к миру и БД.
- [ ] Start запускает один агентный цикл; в логах нет переподключений по кругу.
- [ ] Сообщение с обращением к Саруману попадает в его контекст; ответ — в игровой чат.
- [ ] Разрешённое follow/goto вызывает реальное движение, а не только реплику.
- [ ] После движения состояние/цепочка задач подтверждают результат. Постановка
      команды в очередь сама по себе не доказывает завершение.
- [ ] TTS слышен в OBS и через виртуальный микрофон в Simple Voice Chat.
- [ ] Голос другого игрока распознаётся с правильным именем; нет самоподслушивания TTS.
- [ ] OBS захватывает именно игровую сцену, VTube получает разрешение и двигает аватар.
- [ ] После перезапуска сохранились записи отношений в PostgreSQL.

Штатный Stop останавливает решения агента, **не гарантирует остановку уже запущенного
маршрута UnionClef**. Для аварийной остановки движения используй `@stop` в игровом чате.
При зависшем вызове модели действует таймаут; не нажимай Start повторно вслепую.

Голосовой манифест временно ссылается на тестовые WAV, уже поставляемые с Saruman.
Они не изображают голос конкретного актёра. Для своего голоса замени пути/точные
расшифровки и проверь права на публикацию аудио, Live2D-модели и оформления.

## Порты текущего примера

| Сервис | Порт | Примечание |
|---|---|---|
| UnionClef Py4J | 25333 | Java gateway, принадлежащий моду |
| Python callback | 25334 | Обратные события мода; первый запуск на одной Windows-машине |
| Gradio / HUD | 22228 / 22236 | UI Saruman, не новая объединённая лаборатория |
| TTS / STT / фильтр | 22231 / 22233 / 22230 | Loopback, конфигурация Compose |
| PostgreSQL / Redis | 5432 / 6379 | Не публиковать в интернет |
| OBS / VTube | 4455 / 22232 | Включить API приложений и согласовать их порты |

Адрес игрового сервера задаётся в Minecraft. Его порт и UDP-порт Voice Chat —
отдельные настройки сервера, не порты Python. Установка WSL2 backend Docker
не позволяет одновременно работать в двух загруженных ОС dual-boot: при уходе
из Windows все её процессы останавливаются.

## Проверки разработчика в Ubuntu

Из корня ai-saruman сначала перейди в игровой runtime. Далее все команды
используют его собственный `pyproject.toml`, `uv.lock` и локальную `.venv`:

```bash
cd integrations/saruman
uv sync --locked --python 3.10 --no-default-groups --group collectors
uv run --locked --no-default-groups --group collectors python -m unittest discover -s tests -p test_saruman.py -v
uv run --locked --no-default-groups --group collectors saruman-minecraft doctor --env-file .env.saruman.example
```

Последняя команда с незаполненным example обязана завершиться с FAIL для пароля БД.
Тесты используют fake RPC и fake gateway, а не реальную подписку или Minecraft.
Windows, слышимость голоса и GPU **не считаются проверенными** этими тестами.

Официальный протокол: https://learn.chatgpt.com/docs/app-server .
Адаптер следует account/read → thread/start → turn/start → agentMessage/usage/completed.
При обновлении Codex проверь совместимость протокола; stderr дочернего процесса
не сохраняется, чтобы не засветить приватные данные. Логи Saruman содержат текст
разговоров и аргументы игровых инструментов — не публикуй их без проверки.

## Имена и единые настройки

Текущий путь runtime — `integrations/saruman/`, пакет — `saruman-minecraft`.
`SARUMAN_CHARACTER_NAME=Saruman` выбирает профиль; старое имя переменной не используется.
`.env.example` и `.env.saruman.example` содержат одинаковые значения.
Основной конфиг читается раньше запасных `.env` и `.env.postgres`.

Локальный `docker-compose.override.yml` должен соответствовать новому example-файлу:
не включай в него `docker-compose.yml`, иначе получается рекурсивное включение.
Вариант `docker compose --env-file .env.saruman up -d --build` с новым override
и явный `-f compose.saruman.yml --profile rich --profile voice` используют одинаковые
сервисы и том. Старые контейнеры/данные не удаляются и не мигрируются переименованием.
Если там уже есть память, сохрани резервную копию и перенеси данные отдельно.

После перемещения каталога выполни `uv sync` из раздела подготовки сервисов,
чтобы переустановить команды и editable-пути виртуального окружения.
