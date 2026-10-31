# Сервисы Сарумана

Команды выполняются из `integrations/saruman/`, а не из `docker/`.
Основная конфигурация — `compose.saruman.yml`, окружение — `.env.saruman`.

```powershell
docker compose --env-file .env.saruman -f compose.saruman.yml --profile rich --profile voice up -d --build
```

Rich использует Fish Speech `v1.5.1-dev` на NVIDIA GPU и Parakeet на CPU.
Checkpoints Fish подготовь в `data/fish-tts/checkpoints` до запуска.
PostgreSQL хранит память в постоянном томе, Redis — оперативный контекст.
Не удаляй тома для обычного перезапуска.

Локальный `docker-compose.override.yml` из актуального example даёт тот же набор
сервисов через `docker compose --env-file .env.saruman up -d --build`.
Файлы `docker-compose.tts.yml` и `docker-compose.stt.yml` сбрасывают профили
через Compose `!reset`, чтобы override включал эти сервисы автоматически.
Используй актуальный Docker Compose из Docker Desktop.

Полная инструкция: [Windows](../docs/SARUMAN_WINDOWS.md).
