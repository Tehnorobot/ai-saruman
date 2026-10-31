# Универсальные Skills для Codex Development Lab

В архиве есть папка `skills/`, которую нужно положить **в корень Codex-Development-Lab**, заменив одноимённую папку.

Содержимое:
- `general-development`: универсальная разработка по `ТЗ.md`.
- `code-quality`: реализация, исправление ошибок и разумная проверка.
- `testing-evidence`: независимая проверка и подлинные доказательства.
- `fabric-minecraft-1.21.11`: универсальная разработка Fabric 1.21.11, без требований к Auto Defense.
- `minecraft-mod-compatibility`: совместимость модов; используется только когда нужно.

## Установка на Windows

1. Останови активные задания лаборатории и сделай резервную копию существующей папки `skills`.
2. Распакуй архив **в корневую папку Codex-Development-Lab**. После распаковки должно быть `Codex-Development-Lab/skills/general-development/SKILL.md`, а не `Codex-Development-Lab/codex-universal-skills/skills/...`.
3. Чтобы обновить Skills в уже созданных проектах (а не только в будущих), выполни:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\SYNC_SKILLS_WINDOWS.ps1
   ```

   Скрипт заменяет только перечисленные стандартные Skills в `projects/*/workspace/.agents/skills`. Другие пользовательские Skills он не трогает.
4. Пересобери образ, в котором находятся Skills, и пересоздай контейнеры после завершения текущих задач:

   ```powershell
   docker compose build worker agent
   docker compose up -d --no-build --force-recreate worker agent
   ```

## Важно

В имеющейся сборке Codex Lab при создании рабочего пространства Skills копируются из контейнера только если их папки ещё не существует. Поэтому **без синхронизации старые проекты продолжат использовать прежние Skills**.

Этот архив не меняет `ТЗ.md`, `.env`, авторизацию Codex, Dockerfile, бэкапы или исходники создаваемых модов. Он также не исправляет возможные ошибки существующей Docker-сборки.

В Skills намеренно нет требований к конкретному моду (Auto Defense, Saruman, Magical Scepter), фиксированных дистанций, порогов здоровья, поведения в воде или обязательных интеграций. Такие вещи должны задаваться ТЗ конкретного проекта.
