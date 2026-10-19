# Запуск Сарумана

Из `integrations/saruman/`, после [установки](0_INSTALLATION.md)
и [настройки](1_CONFIG.md):

```powershell
docker compose --env-file .env.saruman -f compose.saruman.yml --profile rich --profile voice up -d --build
.\scripts\start-saruman.ps1 -Doctor
.\scripts\start-saruman.ps1
```

Войди в мир Minecraft до проверки Doctor. Открой `http://127.0.0.1:22228`.
Start включает модельный цикл; по умолчанию он остановлен.
Явный автостарт: `-AutoStart`, диагностика без STT: `-NoStt`.

Если используешь локальный `docker-compose.override.yml`, скопированный из
актуального example, эквивалент запуска сервисов:

```powershell
docker compose --env-file .env.saruman up -d --build
```

Состояние сервисов:

```powershell
docker compose --env-file .env.saruman -f compose.saruman.yml --profile rich --profile voice ps
```

Stop в интерфейсе прекращает решения агента, но не гарантирует остановку уже
запущенного маршрута. Для остановки движения используй `@stop` в игровом чате.
Для сохранения памяти не удаляй тома PostgreSQL (`down -v`).

Ограничения и проверка реального игрового/голосового контура:
[Windows checklist](../SARUMAN_WINDOWS.md).
