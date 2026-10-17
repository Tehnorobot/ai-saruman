# 0_Installation

## 0. Преподготовка

### 0.1. ffmpeg

Убедись, что установлен ffmpeg

Для **Linux** Ubuntu так:

```bash
sudo apt-get install libportaudio2 python3-pyaudio portaudio19-dev python3-dev build-essential ffmpeg
```

Для **MacOs**:

```bash
brew install ffmpeg
```

Для **Windows**:

Запустите терминал powershell от администратора и введите команду

```powershell
winget install ffmpeg
```

Важная поправка для Windows: **НЕ** ЗАПУСКАЙТЕ Python-часть на **WSL**, РАБОТАЙТЕ из-под винды напрямую! Я не тестировал под WSL, там будут танцы с бубном из-за звуковых устройств и портов.

### 0.2. Docker

Docker вам понадобится, чтобы запустить:

- Базу данных (PostgreSQL) и Redis — на них держится память персонажа и обмен
  контекстом между процессами
- Распознавание речи (Parakeet) — это способ по умолчанию
- Синтез речи: Fish TTS (хороший) или Simple TTS (слабее, зато без видеокарты)
- Систему фильтрации — по желанию

Для работы контейнеров очень желательно чтобы ваш ПК / ноут имел:

- более 8G RAM
- не менее 4G VRAM

Собственно, если нет Docker'а, и вам это нужно, [установите](https://www.docker.com/) его!
Для удобства можете поставить Docker Desktop.

Совсем без Docker запуститься можно, но урезанно: синтез речи придётся поднимать
скриптом `simple_tts_server.py`, распознавание речи переключить на локальный
faster-whisper (`STT_BACKEND="fasterwhisper_local"`), а скрипт запускать с флагом
`--no-redis`. Это рабочий режим для первого знакомства, но не основной.

## 1. Установи UV менеджер на систему

UV - быстрый питонячий менеджер, и в нём сильно ускорен `pip install` + он проще управляет версиями питона и venv'ами.

<https://docs.astral.sh/uv/getting-started/installation/>

Устанавливается через консоль PowerShell даже под винду.

Ставь через UV питон 3.10.9:

```bash
uv python install 3.10.9
```

Открой репозиторий в IDE, поддерживающей Python, например, в VS Code.

Если вы работаете через git (есть доступ к этому репозиторию, то клонируйте его в папку через git clone).
Если скачали откуда-то (гугл диск или что-то ещё), то распакуйте архив в папку и откройте эту папку в VS Code.

Зайди в репозиторий из терминала

```bash
cd папка_репозитория
```

После этого открой терминал, поставь venv

```bash
uv venv --python 3.10.9
```

> (опционально) Для удобства работы в VS Code, выбери в VS Code Python-файл какой-нибудь, и выбери в интерпретаторах python исполняемый файл из папки venv

<details><summary>Старый базовый способ без UV</summary>

Ставьте сами питон нужной версии и создайте окружение через VS Code, либо базовой командой по типу

```bash
python -m venv .venv
# активируйте этот venv, потом
pip install uv
```

</details>

## 2. **ОПЦИОНАЛЬНО: ДЛЯ КРУТОЙ FISH TTS и STT**

Убедись, что есть CUDA и CuDNN

Для TTS и STT точно понадобится:

- Nvidia Cuda [12.9](https://developer.nvidia.com/cuda-12-9-0-download-archive) 3.1 G
- Nvidia [CuDNN 9.15](https://developer.nvidia.com/cudnn-9-15-0-download-archive) 700 MB

<details><summary>CUDNN для WINDOWS + Path (обязательно!)</summary>

Нужно добавить CUDNN в Path (переменные среды и путь) Windows, как правило установщики Nvidia этого не делают как нужно!!
Если этого не сделать, будут проблемы с запуском STT модели на CUDA.

как добавить база есть тут <https://remontka.pro/add-to-path-variable-windows/>

в PATH нужно добавить путь к bin от cudnn:

`C:\Program Files\NVIDIA\CUDNN\v9.15\bin\12.9`

</details>

После установки всех "куд" перезагрузись

---

## 3. Поставь зависимости

В терминале IDE, в папке репозитория!
Чтобы не париться, сразу ставь все группы зависимости.

```bash
uv sync --all-groups
```

<details>
  <summary>Старый способ</summary>

  ```bash
  uv pip install -r pyproject.toml
  ```

</details>

## 4. Установка Text To Speech (TTS)

TTS можно ставить 2 способами - ПРОСТОЙ (зависимость, Vosk) и КРУТОЙ (FishTTS, Docker)

Если ставите сложным способом, 4.1 можно пропустить.

### 4.1 Простой способ для TTS

```bash
uv pip install --group simpletts
```

### 5. Ставим STT (распознавание речи)

Способов два, выбирается переменной `STT_BACKEND` в `.env`.

**Parakeet (по умолчанию, в Docker).** Ничего ставить в Python-окружение не надо —
модель крутится в контейнере, скрипт ходит к ней по HTTP. Контейнер поднимается
на шаге 7 вместе с остальными. В `.env` это `STT_BACKEND="parakeet"`.

**faster-whisper локально.** Если не хотите Docker под распознавание. Тогда ставим
зависимости:

```bash
uv pip install --group stt
```

Если есть CUDA, дополнительно:

```bash
uv pip install --group gpu
```

и в `.env` укажите `STT_BACKEND="fasterwhisper_local"` и `STT_COMPUTE_DEVICE="cuda"`
(изначально там `cpu`). Настройка `.env` будет ДАЛЕЕ, на этапе CONFIGURE.

При первом запуске модель качается из сети, может понадобиться впн.

Перед этим для pydub понадобится **ffmpeg**:

- Под **Windows** легче ставить через PowerShell `winget install ffmpeg`
- под **Linux** Ubuntu это `sudo apt-get install ffmpeg`
- под **MacOs** `brew install ffmpeg`.

При запуске финального скрипта ещё будет качаться STT модель, может понадобится впн.

## 6. Docker-сервисы

Всё, кроме контейнеров, уже установлено. Осталось поднять сервисы.

Compose-файлы лежат **в корне репозитория** и разбиты по сервисам:

| Файл | Что поднимает |
|---|---|
| `docker-compose.yml` | PostgreSQL и Redis — база, нужна всегда |
| `docker-compose.tts.yml` | Fish TTS, видеокарта NVIDIA |
| `docker-compose.tts-mac.yml` | Fish TTS на Apple Silicon (MPS) |
| `docker-compose.simpletts.yml` | Simple TTS, только процессор |
| `docker-compose.pipertts.yml` | Piper TTS |
| `docker-compose.stt.yml` | Parakeet — распознавание речи |
| `docker-compose.filter.yml` | Система фильтрации |

Вручную перечислять их при каждом запуске не нужно. Скопируйте пример
и оставьте в нём то, что вам нужно:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
```

Внутри — список `include`, где раскомментируете свои строчки. TTS выбирается
**один**: либо Fish под NVIDIA, либо Fish под Mac, либо Simple без видеокарты.
Файл `docker-compose.override.yml` в репозиторий не коммитится и остаётся
на вашей машине.

Дальше поднимаете всё одной командой из корня репозитория:

```bash
docker compose up -d --build
```

Docker сам подхватит `docker-compose.override.yml`.

> Пароль базы в `docker-compose.yml` — заглушка. Задайте свой там же и продублируйте
> его в `.env.postgres` (шаблон — `.env.postgres.example`).

Проверить, что поднялось:

```bash
docker compose ps
```

Ожидаемые контейнеры: `nettyan_db`, `nettyan_redis`, плюс выбранные TTS/STT/фильтр.

Если фильтр вам сейчас не нужен, а вы его подняли — можно просто остановить
его контейнер, остальное продолжит работать.

## Финал

Вы завершили установку, можете вернуться в [содержание](index.md) и оттуда перейти к конфигурации.

## Дополнительно

Если нужны конкретные группы (зависимостей) из pyproject.toml, то:

```bash
uv sync --group dev --group simpletts --group stt
```

и сколько угодно групп какие вам нужны.

Но изначально мы ставили под все зависимости.
