# Установка Сарумана

Полная инструкция под текущую конфигурацию находится в
[SARUMAN_WINDOWS.md](../SARUMAN_WINDOWS.md).

1. Работай в `ai-saruman/integrations/saruman/`, нативно в Windows.
2. Установи uv, Git, FFmpeg и Docker Desktop с GPU-доступом.
3. Подготовь Minecraft, Fabric, UnionClef, Simple Voice Chat, виртуальный
   аудиокабель, OBS и при необходимости VTube Studio по инструкции курса.
4. Установи Codex CLI и выполни `codex login` с авторизацией ChatGPT.
5. Создай `.env.saruman` из `.env.saruman.example`, если файла ещё нет;
   заполни пароли и аудиоустройства. Не перезаписывай существующие секреты.
6. Подготовь checkpoints для Fish Speech 1.5.1 в `data/fish-tts/checkpoints`.
7. Установи Python-окружение игры:

```powershell
uv sync --locked --python 3.10 --no-default-groups --group collectors
```

Для Rich: Fish на NVIDIA GPU, Parakeet в контейнере на CPU.
Наличие CUDA на хосте само по себе не включает GPU в контейнере STT.
Окружение лаборатории в корне и окружение игры должны оставаться раздельными.

Далее: [настройки](1_CONFIG.md), [запуск](2_USAGE.md).
Сведения о происхождении кода: [SOURCE.md](../../SOURCE.md).
