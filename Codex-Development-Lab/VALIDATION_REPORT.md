# Validation report — Codex Development Lab

Historical packaging validation. Current fixes and independent verification are
documented in [FIXES_RU.md](FIXES_RU.md) and [FIX_VALIDATION_RU.md](FIX_VALIDATION_RU.md).

Date: 2026-10-20. Scope: source package assembled for Windows Docker Desktop + WSL2; NO production Docker build or Minecraft gameplay run was possible in this authoring environment (Docker daemon and Codex login absent).

## Completed checks (PASS)

- `python -W error::ResourceWarning -m unittest discover -s tests -q`: **13 passed**.
- Independent FastAPI upload of one Markdown file and durable queue creation: **PASS** (in-process TestClient).
- Durable worker lease prevents double-claim: **PASS**.
- User pause/resume/stop endpoints and state changes: **PASS**.
- Codex quota error classifier + fake failed-agent test: **PASS**.
- Fake quota scenario requests backup, records QUOTA_PAUSED, and prevents instant restart: **PASS**.
- Backup archive contains workspace, manifest, consistent online SQLite snapshot and SHA-256: **PASS**.
- File route traversal blocking and no imaginary verification without tests: **PASS**.
- Fake Codex CLI subprocess separated into executor task mailbox: **PASS**.
- Yandex S3 config validation, source isolation, local fake S3 upload, deduplication and >60-second clip using real FFmpeg/FFprobe: **PASS** (FAKE storage client, no real cloud call).
- Python sources: `compileall` **PASS**.
- Dashboard JS syntax with Node `--check`: **PASS**.
- `compose.yaml` YAML parse: **PASS** (does NOT equal `docker compose config` or image build).
- Composition isolation review: Agent mounts `/projects`, `/agent-tasks`, `codex_auth`, and **does not** mount `/data`, `/artifacts`, backups, Docker socket, or S3 credentials. **PASS** by manifest inspection.

## Not tested in this environment

- Docker Compose build/start including Node, Java, Gradle, Codex CLI images: **NOT_TESTED**.
- Login with ChatGPT device authorization and real quota error message text: **NOT_TESTED**.
- Real Codex subagent spawning in target pinned CLI 0.162.1: **NOT_TESTED**.
- Real Gradle project compilation and remapped production Fabric JAR: **NOT_TESTED**.
- Client rendering inside Xvfb/Mesa in Docker Desktop / recording of live Minecraft: **NOT_TESTED**.
- Yandex Cloud S3 real upload, IAM permission and signed URL: **NOT_TESTED** (user .env required).
- Actual Minecraft gameplay, bow cancellation, shield, PvP targeting, 26 acceptance cases: **NOT_TESTED**.
- Other language/library build templates beyond Gradle, Maven, pytest, npm: **NOT_TESTED**.

## Design constraints / known limits

- New projects begin with one TZ.md and preinstalled Skills; Codex must scaffold the source. The source files of the requested final mod are NOT pre-verified deliverables of this archive.
- The independent harness verifies actual command exit codes and recorded artifacts, NOT agent claims. Scenario PASS requires objective assertions.
- The worker cannot know remaining subscription quota ahead of time: it classifies service responses and performs a bounded retry after the configured interval.
- A container stopped during a Codex turn cannot preserve that turn's hidden continuation; it restarts from the last saved round. Logs and source remain.
- The agent is run without a second Linux sandbox *inside the isolated Docker agent container* because nested kernel sandboxing is not universally available. Avoid trusting code from third parties, don't mount private host files.
- The agent container can read all project source trees under the shared `/projects` mount. For unrelated owners, use independent lab deployments.
- A <=58s display capture does not prove the gameplay took place within that window. Real client GameTest assertions are required.
- Backup archives intentionally exclude heavy MP4/PNG/JARs; these stay in `artifacts/` and remote S3 when configured. A separate backup of `artifacts/` is advised for disaster recovery.

## Выбор модели/Reasoning (доработка)

Добавлена настройка модели и reasoning на каждый проект; автоматическая миграция SQLite, сохранение при перезапуске и передача в `codex exec`. Тесты: см. `tests/test_codex_settings.py`. Реальный аккаунт Codex и модели в среде сборки недоступны; доступность конкретных моделей и уровней проверяется при запуске пользователя.

## Проверки обновления model/reasoning

- `python -m unittest discover -s tests -q` — **20/20 PASS** (включая 7 новых проверок выбора, изменения и миграции).
- `python -m compileall -q app agent worker common backup storage tests` — PASS.
- `node --check` на встроенном JavaScript веб-панели — PASS.
- `compose.yaml` парсится как YAML и содержит 6 ожидаемых сервисов.
- `START_WINDOWS.ps1` сохраняет UTF-8 BOM для Windows PowerShell 5.1.
- **NOT_TESTED:** настоящий Codex CLI, Docker Compose startup на Windows, поддержка конкретных модельных IDs аккаунтом.
