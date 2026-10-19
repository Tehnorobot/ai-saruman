# Один репозиторий Saruman

```text
ai-saruman/
├── src/saruman/                 # лаборатория, Python 3.14+
├── agent-workplace/             # основа автономного персонажа и Markdown-память
├── integrations/saruman/        # игровой runtime, Python 3.10.x
│   ├── src/saruman/             # launcher, doctor, Codex-адаптер
│   ├── src/agent/               # агентный цикл и игровые инструменты
│   ├── resources/Characters/Saruman/
│   ├── compose.saruman.yml
│   ├── pyproject.toml
│   ├── uv.lock
│   └── scripts/start-saruman.ps1
└── docs/
```

Игровой компонент включён обычными файлами, без submodule и вложенной `.git`.
История происхождения, исходные ревизии и сведения о резервных Git-метаданных
сохранены в [SOURCE.md](../integrations/saruman/SOURCE.md).
Лицензии и авторство компонентов сохраняются.

## Окружения

Лаборатория и игровой runtime — два приложения с разными зависимостями,
`pyproject.toml`, `uv.lock` и `.venv`. Оба содержат модуль `saruman`;
не объединяй их окружения и не добавляй оба `src` в один `PYTHONPATH`.

Пакет лаборатории называется `saruman`, команда — `saruman`.
Пакет игры называется `saruman-minecraft`, команды — `saruman-minecraft` и
`saruman-runtime`. Все относительные пути игры отсчитываются от `integrations/saruman`.

После перемещения папки переустанови editable-пакет через `uv sync --locked
--python 3.10 --no-default-groups --group collectors`: сгенерированные launcher-файлы
и editable-пути в `.venv` привязаны к местоположению установки.

## Запуск и данные

Лаборатория: [корневой README](../README.md).
Игра: [Windows-инструкция](../integrations/saruman/docs/SARUMAN_WINDOWS.md).
Основной игровой конфиг — `.env.saruman`, выбор персонажа — `SARUMAN_CHARACTER_NAME=Saruman`.

Оба варианта Compose используют проект `saruman` и том `saruman_saruman_db`.
Старые контейнеры и тома не удаляются и автоматически не переносятся;
если в другой БД уже накоплена память, сначала сделай резервную копию и перенеси её
отдельно. Не используй `down -v` для перезапуска с сохранением данных.

Память Markdown, SQLite лаборатории и PostgreSQL игры пока не объединены.
Полная МАС, оркестрация, ИТ-агенты и Telegram остаются отдельными будущими задачами.

## Git

Основной remote — `https://github.com/Tehnorobot/ai-saruman.git`.
Работай с Git из корня репозитория. Локальные `.env*`, кроме example-шаблонов,
окружения, модели и пользовательские данные исключены из Git.
Вложенные `.github/workflows` — исходные файлы компонента; GitHub не запускает их
как общий CI монорепозитория.
