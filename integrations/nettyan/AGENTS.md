# NetTyan — Project Context

RE-READ THIS FULLY IF THE CONVERSATION WAS SUMMARIZED! Always read this file at the start of every conversation before doing anything else.

> При первом прочтении или после суммаризации диалога — скажи кратко (5-10 слов) выжимку правил. Не повторяй каждый раз.

## Project structure

| Путь | Что там |
|---|---|
| `src/agent/` | логика поведения поверх языковой модели, инструменты, промпты |
| `src/character/` | персонаж: конфиг, имена, эмоции, голосовые референсы |
| `src/tts/`, `src/data_collectors/stt/` | синтез и распознавание речи |
| `src/data_collectors/` | входящие события: площадки трансляций, игра, донаты |
| `src/data_flow/` | база, модели, слой доступа к данным |
| `src/live2d/`, `src/front/` | аватар и вывод на трансляцию |
| `docker/` | контейнеры: фильтрация, TTS, STT |
| `resources/` | персонажи, промпты, звуки |
| `docs/help/` | пользовательская документация (публикуется на github.io) |

## STRICT Rules

- Auto-commit and push your changes (if not explained otherwise).
- - Auto-commit and PUSH!! your changes (if not explained otherwise).
  - **Do NOT add `Co-Authored-By` lines to commit messages.** Ever.
  - **All commits MUST use author name and email, not ai's.** Never use Anthropic/Claude credentials. Use owner's creds if git config differs.
  - Do not forget periodically do pulls to keep up with parallel workers.
  - Add upperleveled module name to commit message if relevant (e.g. "main: implement ...").
- **Автономность:** не делай лишнего, не строй велосипеды, переиспользуй пайплайн-простые методы, избегай дублирования кода.

## Tone & style

No pompous slogans, no self-praise, no "elite" or "advanced" anything. Short, dry, casual — like baritone's "Google Maps for Blockgame" or "plays block game". If it sounds like a marketing pitch, rewrite it. Think understated British humour, not a startup landing page.

## Code run commands

Activate our UV virtual environment:

If you are on windows

```bash
.venv\Scripts\activate.bat
```

If on macOS or Linux

```bash
source .venv/bin/activate
```

## Введение в проект (план для AI)

1. Прочитать `AGENTS.md` (этот файл)
2. Прочитать `docs/ai/progress.md` — **обязательно**
3. Если нужен контекст — изучить код по задаче

## Документация сессий

- `docs/ai/progress.md` — детальный прогресс по структуре **IPI** (Investigate → Plan → Implement)
- `docs/ai/archive/` — архив прогресса (при >500 строк или завершении крупного блока)
- `docs/ai/readme.md` — формат и правила ведения progress-файлов

Формат архивов: `DD-MM-YYYY-task-name.md`

## Important files

- `README.md` — project overview, fork history, credits
- `docs/help/0_INSTALLATION.md` — how to build and run from scratch
- `docs/help/DEVELOP.md` — notes for project developers
- `AGENTS.md` — this file (AI assistant rules)
- `docs/ai/progress.md` — прогресс AI по задачам
