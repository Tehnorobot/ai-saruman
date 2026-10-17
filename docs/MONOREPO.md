# Один репозиторий Saruman

```text
ai-saruman/
├── .git/                       # единственная история проекта
├── src/saruman/                # лаборатория, Python 3.14+
├── agent-workplace/            # личность, Markdown-память, скрипты
├── integrations/nettyan/       # игровой runtime, Python 3.10.x
│   ├── src/saruman/            # launcher, doctor и адаптер модели
│   ├── src/agent/              # поведение и игровые инструменты
│   ├── resources/Characters/  # профили и голосовые референсы
│   ├── pyproject.toml
│   ├── uv.lock
│   └── scripts/start-saruman.ps1
└── docs/
```

## Что изменилось при переносе

17 октября 2026 года полный рабочий каталог NetTyan перемещён из соседней
папки `NetTyan` в `integrations/nettyan`. Код, ресурсы, права файлов,
лицензии и локальные изменения сохранены. Исходный commit адаптации:
`63cf1ea173120dedaa7ced4e72978e068e9cb72e` (`feat: saruman v1`).

Вложенные `.git` NetTyan и agent-workplace вынесены за пределы ai-saruman:

```text
/home/aimihail/projects/saruman/repository-backups/2026-10-17-monorepo/
├── nettyan.git/
├── agent-workplace.git/
└── README.md
```

Это локальные резервные копии истории, они не отправляются в основной GitHub.
Незакоммиченные изменения agent-workplace оставлены в рабочей папке.
При новом коммите ai-saruman получит файлы компонентов, но не их прежнюю историю
как набор отдельных коммитов. Для просмотра старой истории служат резервные `.git`.

Исходники: [NetTyan](https://github.com/3ndetz/NetTyan) и
[agent-workplace](https://github.com/3ndetz/agent-workplace).
Авторские `LICENSE`, `AUTHORS` и README сохранены в соответствующих компонентах.
Особое примечание NetTyan о звуковых ресурсах оставлено в его README;
этот перенос не меняет условия распространения ресурсов.

## Окружения и запуск

Это один Git-репозиторий, но пока два независимых Python-приложения.
Корневые `pyproject.toml` и `uv.lock` относятся только к лаборатории.
У NetTyan свои зависимости и отдельная `.venv` в `integrations/nettyan`.
Оба приложения содержат модуль `saruman`, поэтому смешивать их в одном
окружении или одновременно добавлять оба `src` в PYTHONPATH нельзя.

Лаборатория запускается из корня по [README](../README.md).
Игровой runtime запускается из `integrations/nettyan` по
[Windows-инструкции](../integrations/nettyan/docs/SARUMAN_WINDOWS.md).
Все относительные пути конфигурации, Compose, миграций и ресурсов NetTyan
по-прежнему отсчитываются от его папки, а не от корня ai-saruman.
Его `.github/workflows` сохранены как исходные файлы: GitHub не запускает
workflow из вложенной папки. Общий CI этим переносом не настраивался.

Лаборатория не перезапускает игровой агент автоматически; памяти Markdown,
SQLite и PostgreSQL пока не объединены. Контейнеризация агента, Telegram
и YouTube также не добавляются самим переносом.

## Git и Windows

Основной remote: `https://github.com/Tehnorobot/ai-saruman.git`.
Проверяй и публикуй изменения из корня ai-saruman:

```bash
git status --short
git diff
# После проверки файлов и секретов:
git add .
git diff --cached --stat
git commit -m "Integrate NetTyan Minecraft runtime into Saruman"
git push -u origin HEAD
```

Перенос сам по себе не создаёт коммит и не выполняет push.
Игнорируются локальные `.env*`, кроме example-шаблонов, окружения, кэши
и данные лаборатории. Это не заменяет проверку содержимого перед публикацией.

В Windows клонируй ai-saruman либо обнови существующую копию после push.
Не копируй Linux `.venv`: создай Windows-окружение внутри `integrations/nettyan`
по инструкции. Секреты `.env.saruman` настраиваются локально и не приходят из Git.
