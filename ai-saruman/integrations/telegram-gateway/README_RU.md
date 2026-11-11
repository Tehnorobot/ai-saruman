# Telegram: первый запуск и проверка

Отдельный шлюз для одного канала. Работает без LLM и без запуска Minecraft.
Python 3.10+, внешние Python-пакеты не нужны.

После первых проверок: [подключение к персонажу и управление из окна](AUTONOMOUS_RU.md).

```text
ПК: client.py → зашифрованный SSH-туннель → VPS: gateway.py → HTTPS → Telegram
```

Для первых проверок домен и открытый HTTP API не нужны. На VPS порт 8787 слушает
только localhost; с компьютера доступ к нему идёт через SSH. Локальный VPN для
этого соединения не нужен, если адрес VPS доступен напрямую.

## 1. Арендовать VPS

Практический вариант для оплаты из России — [Timeweb Cloud](https://timeweb.cloud/services/cloud-servers).
Провайдер указывает размещение в Нидерландах/Германии и оплату российскими картами;
[способы оплаты](https://timeweb.cloud/docs/service-payments/payment-methods).
Альтернатива — [FirstVDS](https://firstvds.ru/products/vds_vps_hosting), локация Нидерланды.
Доступность конкретного зарубежного IP из твоей сети нужно проверить после выдачи.

Выбери:

- Ubuntu 24.04 LTS, x86_64.
- Нидерланды или Германия, если доступны в панели.
- 1 vCPU, 1 ГБ RAM, 15–20 ГБ диска достаточно для этого текстового шлюза.
- Публичный IPv4 и SSH. GPU, панель хостинга и Kubernetes не нужны.
- Для начала короткий период оплаты; проверь итоговую стоимость с IPv4.

[Инструкция создания сервера](https://timeweb.cloud/docs/cloud-servers/manage-servers/create-server).
На этом сервере не будут работать Minecraft или модели: только отправка постов.

При создании сервера добавь публичный SSH-ключ. В PowerShell на ПК, если отдельного
ключа ещё нет (не перезаписывай существующий):

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ssh" | Out-Null
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\saruman-vps" -C "saruman-telegram"
Get-Content "$env:USERPROFILE\.ssh\saruman-vps.pub"
```

В панель провайдера скопируй только содержимое `.pub`. Приватный файл без `.pub`
остаётся на ПК. При первом SSH-входе сверяй отпечаток сервера через панель/консоль
провайдера. Далее `VPS_IP` означает выданный IP, его нужно заменить в командах.

```powershell
Test-NetConnection VPS_IP -Port 22
ssh -i "$env:USERPROFILE\.ssh\saruman-vps" root@VPS_IP
```

В SSH-терминале VPS:

```bash
apt update
apt install -y python3 curl
curl -I --max-time 15 https://api.telegram.org
```

Нужен ответ HTTP с успешно проверенным TLS; редирект тоже подтверждает соединение.
Это пока не проверка бота. Если соединения нет, проверь сеть сервера/обратись в поддержку.
Для первой проверки шлюза достаточно входящего SSH-порта 22; порт 8787 наружу не открывай.

## 2. Создать бота и тестовый канал

1. Через свой VPN открой в Telegram официального [@BotFather](https://t.me/BotFather).
2. Выполни `/newbot`, задай имя и уникальный username бота, оканчивающийся на `bot`.
3. Сохрани выданный токен. Это `TELEGRAM_BOT_TOKEN`; не присылай его в чат.
4. Создай отдельный пустой тестовый канал. Для простой настройки задай ему публичный
   username, например `@saruman_gateway_test_ТВОЙ_СУФФИКС` латиницей.
5. Добавь своего бота администратором канала с правом «Публикация сообщений».
   Остальные назначаемые права для этого теста не требуются.

В `.env` укажи **username канала**, не бота. Для приватного канала поддерживается
числовой ID вида `-100...`; определение такого ID можно настроить позже.

[Создание бота](https://core.telegram.org/bots/features#creating-a-new-bot),
[Bot API](https://core.telegram.org/bots/api#sendmessage).

## 3. Загрузить шлюз на VPS

В SSH-терминале VPS (новый отдельный пользователь без пароля):

```bash
adduser --system --group --home /opt/saruman-telegram telegram-gateway
install -d -m 750 -o telegram-gateway -g telegram-gateway /opt/saruman-telegram
```

В PowerShell на ПК:

```powershell
cd C:\Users\aimihail\Desktop\projects\ai-saruman\integrations\telegram-gateway
scp -i "$env:USERPROFILE\.ssh\saruman-vps" gateway.py client.py .env.example root@VPS_IP:/opt/saruman-telegram/
```

На VPS:

```bash
cd /opt/saruman-telegram
cp .env.example .env
python3 gateway.py --generate-key
nano .env
```

Заполни `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=токен_от_BotFather
TELEGRAM_CHANNEL_ID=@username_тестового_канала
GATEWAY_API_KEY=ключ_из_команды_generate-key
GATEWAY_DATABASE=data/gateway.sqlite3
```

`GATEWAY_API_KEY` — отдельный случайный ключ для связи ПК со шлюзом. Он отличается
от Telegram-токена. После редактирования:

```bash
chown telegram-gateway:telegram-gateway /opt/saruman-telegram/.env
chmod 600 /opt/saruman-telegram/.env
sudo -u telegram-gateway python3 gateway.py --env-file /opt/saruman-telegram/.env
```

Этот терминал оставь открытым. Сервер слушает `127.0.0.1:8787` на VPS.
Закрытие терминала/перезагрузка остановит этот пробный запуск. Ниже есть постоянный вариант.
На VPS запускай один процесс шлюза с одной базой.

## 4. Открыть туннель и настроить клиент

В отдельном PowerShell на ПК:

```powershell
ssh -i "$env:USERPROFILE\.ssh\saruman-vps" -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:8787:127.0.0.1:8787 root@VPS_IP
```

Оставь окно открытым. Отсутствие вывода нормально. При ошибке занятого порта сначала
останови свой предыдущий туннель. HTTP на localhost защищён внешним SSH-туннелем.
Не заменяй URL на `http://VPS_IP:8787`: клиент запрещает передачу ключа по удалённому HTTP.

В другом PowerShell на ПК:

```powershell
cd C:\Users\aimihail\Desktop\projects\ai-saruman\integrations\telegram-gateway
Copy-Item .env.client.example .env.client
notepad .env.client
```

В `.env.client` оставь `GATEWAY_URL=http://127.0.0.1:8787` и впиши такой же
`GATEWAY_API_KEY`, как на VPS. Telegram-токен на ПК не нужен.
`.env`, `.env.client` и база исключены из Git. Не коммить их принудительно.

## 5. Простые проверки

На ПК из папки шлюза. `uv run --no-project` не затрагивает окружение игрового персонажа.
При первом запуске uv может скачать Python 3.12. Если Python 3.10+ уже доступен,
вместо `uv run --no-project --python 3.12 python` можно использовать его напрямую.

```powershell
uv run --no-project --python 3.12 python client.py health
uv run --no-project --python 3.12 python client.py check
```

Ожидаем: `HTTP: 200`; первая команда возвращает `ok: true`, вторая — username бота,
название канала и `can_post: true`. Эти команды **не публикуют** сообщения.

Только следующая команда отправляет пост. Используй тестовый канал:

```powershell
uv run --no-project --python 3.12 python client.py publish --id smoke-001 --text "Проверка связи: Саруман вырастил дерево."
uv run --no-project --python 3.12 python client.py status smoke-001
```

Ожидаем: `status: sent`, числовой `message_id`, одно сообщение в канале.
Повтори команду `publish` с **тем же ID и тем же текстом**. Второго сообщения быть не должно.
Для нового поста используй новый ID, например `smoke-002`.

Если `status: unknown`, сначала посмотри канал: Telegram мог принять пост до обрыва ответа.
Не создавай новый ID для слепой повторной отправки. `status` читает локальный журнал шлюза,
он сам не сверяет содержимое канала и не проверяет последующее удаление сообщения.

Если `failed`, запрос отклонён: проверь права/настройки и `error`. При `429` журнал
сохранит `retry_after`. В этой тестовой версии автоматической очереди повторов нет;
запросы с уже использованным ID не отправляются заново даже после ошибки.
После исправления причины для нового явного теста задай новый ID.

Состояния:

| Состояние | Значение |
|---|---|
| `sending` | Запрос зарегистрирован, ответ ещё не получен |
| `sent` | Telegram подтвердил отправку и вернул message_id |
| `failed` | Telegram отклонил запрос |
| `unknown` | Нет достоверного ответа о доставке; нужна проверка канала |

`health` проверяет только доступ к шлюзу. `check` отдельно проверяет Telegram и права.
Отказ шлюза не подключён к игровому процессу и не останавливает Minecraft.

## 6. Автотесты без Telegram

```powershell
uv run --no-project --python 3.12 python -m unittest -v test_gateway.py test_automation.py
```

Тесты поднимают настоящий HTTP-сервер на localhost и подменяют Telegram.
Проверяют авторизацию, права, Unicode, повторную/одновременную отправку,
перезапуск, неопределённую доставку, 429 и защиту ключей. Не публикуют сообщения.
Успешные автотесты не заменяют проверку реального VPS/бота/канала.

## 7. Постоянный запуск на текущем VPS: systemd

Для уже работающего Python-шлюза Docker не требуется. Подготовлен
`saruman-telegram.service`: автозапуск после перезагрузки, перезапуск при падении,
отдельный пользователь и запись только в папку данных (и временную папку службы).
Используется прежняя база `data/gateway.sqlite3`, история отправок сохраняется.

В PowerShell на ПК из папки шлюза:

```powershell
scp saruman-telegram.service root@VPS_IP:/opt/saruman-telegram/
```

В первом SSH-окне, где работает пробный шлюз, нажми Ctrl+C. Затем на VPS:

```bash
install -d -m 700 -o telegram-gateway -g telegram-gateway /opt/saruman-telegram/data
install -m 644 /opt/saruman-telegram/saruman-telegram.service /etc/systemd/system/saruman-telegram.service
systemd-analyze verify /etc/systemd/system/saruman-telegram.service
systemctl daemon-reload
systemctl enable --now saruman-telegram
systemctl status saruman-telegram --no-pager
```

Ожидаем `active (running)`. Если проверка unit-файла сообщает ошибку, исправь её
перед запуском службы. `.env` остаётся в `/opt/saruman-telegram`, владелец
`telegram-gateway`, права `600`; значение `GATEWAY_DATABASE` должно оставаться
`data/gateway.sqlite3` либо абсолютным путём к этой же базе.

После запуска проверь с VPS:

```bash
cd /opt/saruman-telegram
python3 client.py --env-file .env check
python3 client.py --env-file .env status smoke-001
```

`smoke-001` — ID первого теста. Для уже отправленного теста после перехода на службу
ожидаем прежние `sent` и `message_id`. Эти команды ничего не публикуют.
Далее можно закрывать SSH-терминал: служба от него не зависит.

Диагностика и управление на VPS:

```bash
journalctl -u saruman-telegram -n 30 --no-pager
systemctl restart saruman-telegram
systemctl stop saruman-telegram
```

Перезапуск делай между публикациями. Если отправка прервана, журнал может пометить
её `unknown`: сначала проверь канал, не отправляй её вслепую с новым ID.
Ограничения файловой системы задаёт systemd:
[описание параметров](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).

### Туннель с компьютера

Обычная SSH-команда из раздела 4 уже подходит для проверки с ПК. Также подготовлен
`start-tunnel.ps1`; из папки шлюза:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-tunnel.ps1 -Server VPS_IP
```

Параметр ExecutionPolicy действует только для этого процесса. Скрипт не меняет
системную политику, не сохраняет пароли и не регистрирует автозапуск Windows.
Ключ сервера должен быть предварительно проверен и сохранён обычным SSH-входом.

После настройки отдельного SSH-ключа можно включить восстановление туннеля:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-tunnel.ps1 -Server VPS_IP -IdentityFile "$env:USERPROFILE\.ssh\saruman-vps" -Reconnect
```

Режим `-Reconnect` требует уже установленного на VPS публичного ключа и доступа
без интерактивного ввода пароля (для зашифрованного ключа — загруженного ssh-agent).
Скрипт не устанавливает ключи сам. Перед этим проверь обычный SSH-вход с ключом.
При обрыве соединения скрипт повторяет подключение; неверный ключ/отпечаток он не
обходит. После перезапуска Windows скрипт нужно запустить снова. Автозапуск
туннеля вместе с персонажем будет подключён на этапе интеграции.
[Параметры OpenSSH](https://man.openbsd.org/ssh_config).

### Альтернатива: Docker

Подготовлены `Dockerfile` и `compose.yaml`. Установи Docker по
[официальной инструкции Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
загрузи эти два файла вместе с `gateway.py` в папку сервера. Останови пробный
Python-процесс через Ctrl+C. В папке сервера с заполненным `.env`:

```bash
docker compose up -d --build
docker compose ps
```

Compose сохраняет журнал в отдельном Docker volume `gateway-data`, слушает только
localhost VPS и автоматически запускает контейнер после перезагрузки Docker.
Контейнер работает без root, без Docker socket и без монтирования папок ПК.
При переходе с прямого Python-запуска база `data/gateway.sqlite3` **сама не переносится**
в Docker volume: для первых тестов используй новые ID; перед переносом реальных
публикаций нужна миграция журнала. Не удаляй volume с историей отправок.
SSH-туннель на ПК пока поддерживается отдельным процессом.

Очередь с контролируемыми повторами, подключение к персонажу и окну состояния
описаны в [AUTONOMOUS_RU.md](AUTONOMOUS_RU.md). SSH-туннель
подходит и для постоянного использования, покупать домен для него не нужно.
Публичный HTTPS-доступ по домену — отдельная необязательная альтернатива.
Отдельная LLM на VPS не требуется.

## API для будущей интеграции

Все `/v1/*` требуют `Authorization: Bearer <GATEWAY_API_KEY>`.

```text
GET  /healthz               состояние самого шлюза
GET  /v1/check              бот, канал, право публикации
POST /v1/posts              публикация / результат уже зарегистрированного запроса
GET  /v1/posts/{id}         сохранённый результат
POST /v1/posts/{id}/retry   явный повтор только отклонённого запроса; тело {}
GET  /v1/updates?offset=N   новые посты только настроенного канала
```

Тело публикации — ровно два поля; канал выбирается только конфигурацией сервера:

```json
{"publication_id":"event-tree-001","text":"Сегодня я вырастил первое дерево."}
```

Ответ при подтверждённой отправке содержит:

```json
{"publication_id":"event-tree-001","status":"sent","message_id":42,"error":null}
```

Реальный ответ также содержит UTC-даты и `retry_after`. Текст сохраняется целиком
в SQLite. Произвольная выгрузка старой истории канала, изображения и видео пока
не подключены. Получение новых постов и автономная очередь
на компьютере доступны после [обновления и настройки интеграции](AUTONOMOUS_RU.md).
