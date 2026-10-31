# Независимая проверка ограниченного запуска сборок

Дата: 2026-10-20. Проверку и статусы назначил отдельный агент `runner_verifier`,
не изменявший реализацию. Проверялась граница файлового доступа и восстановление
заданий; это не проверка качества Minecraft-мода и не гарантия отсутствия
уязвимостей Docker или ядра.

**Результат: PASS для проверенной конфигурации ограниченного runner.**
Прямой Docker socket отсутствует у worker, агента и контейнера сборки.
Только доверенная служба runner имеет Engine socket. Она принимает ограниченные
запросы и не предоставляет вызывающему выбор образа, host mounts или привилегий.

## Среда и воспроизводимость

Проверки выполнены на Docker Desktop Windows с Linux-контейнерами.
Образы создавались под отдельными тестовыми тегами, рабочая лаборатория не
перезапускалась:

| Назначение | Тег | Image ID |
| --- | --- | --- |
| Полная регрессия | `codex-lab-service:verify-restricted` | `b687164180c7de24d56ae2d2cb9419e7a7c0c15d0c133f8ea791a8432fa22ffe` |
| Ограниченный запуск | `codex-lab-runner:verify-restricted` | `841b1cd81be02f92b9c6678306086c6bca2af63e5521fa08232b2f8a14670307` |
| Java/Gradle/Xvfb в сборке | `codex-lab-worker:fix-test` | `4ab52fa66e15f71fe246eb4bec78a7a688e761942fcfa8b64fa891c0a516dcbf` |

Клиент интеграционной проверки использовал актуальные исходники через read-only
mount `/opt/lab`, UID/GID `1000:1000`, без capabilities и Engine socket.
Его `/projects` и `/evidence` были отдельными временными папками внутри
`audit/runner-scratch`, реально подключёнными с Windows. Служба runner имела
отдельные named volumes состояния и Unix socket, без project bind mounts и сети.
SHA-256 `common/safe_archive.py` в конечном образе runner совпал с исходником:
`394ac2505b8310101ef83441f21a24b68977e6e966d9ddd39466e75162c5794f`.

Основные воспроизводимые команды:

```powershell
docker build -f Dockerfile.service -t codex-lab-service:verify-restricted .
docker build -f Dockerfile.runner -t codex-lab-runner:verify-restricted .
docker run --rm --network none --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true codex-lab-service:verify-restricted python3 -m unittest discover -s tests -v
```

Интеграционный сценарий находится в
[`audit/runner_verification.py`](audit/runner_verification.py). Он предполагает
Linux-клиент с `/projects`, `/evidence` и read-only Unix socket runner, а для
проверки восстановления ждёт внешнего пересоздания runner на тех же named
volumes и создания `/evidence/broker-restarted.json`. Это намеренно отдельный
тестовый стенд: сценарий нельзя направлять на проекты рабочей лаборатории.
Дополнительные режимы `VERIFY_POLICY_ONLY=1` и `VERIFY_VIDEO_ONLY=1` проверяют
реальный HTTP API и экспорт записи экрана соответственно.

## Результаты

| Проверка | Статус | Подтверждение |
| --- | --- | --- |
| Полная регрессия из конечного service image | PASS | 82 теста обнаружены, 78 выполнены успешно, 4 явно пропущены |
| Безопасность архивов и журнал восстановления на Windows bind mount | PASS | Все 20 тестов выполнены; `TMPDIR` направлен в подключённую папку Windows |
| Ограничения реального HTTP API runner | PASS | `image`, `mounts`, `env`, `privileged`, `HostConfig` и путь вместо project ID отвергнуты с HTTP 400; `/containers/create` — HTTP 404 |
| Реальная конфигурация дочернего контейнера | PASS | Engine inspect: `Binds=null`, только два named-volume subpaths `/workspace` и `/output`, UID 1000, read-only rootfs, cap-drop ALL, no-new-privileges, 4 CPU, 10 GiB, 500 процессов |
| Отсутствие служебного доступа внутри сборки | PASS | Код не нашёл Docker/control socket, базу очереди, другой project mount, состояние runner, задания агента или его авторизацию; запись в `/opt/lab` запрещена |
| Реальная сборка и возврат файлов | PASS | Offline Gradle 9.2.1 с встроенным Java plugin собрал синтетический JAR; изменённый файл вернулся, удалённый исчез, неизменённый сохранился |
| Таймаут | PASS | Команда со sleep 90 завершена по timeout 1 секунду; файл возвращён, маркер и временный запуск удалены |
| Пауза | PASS | После сигнала PAUSED работа остановлена, файл возвращён, маркер и временный запуск удалены |
| Перезапуск runner при работающей сборке | PASS | Runner принудительно пересоздан с прежним volume; оставшийся контейнер остановлен с exit 143, worker восстановил изменённый файл и удалил запуск/маркер |
| Реальный экспорт MP4 | PASS | Xvfb и ffmpeg записали 2.867 секунды экрана; файл прошёл ffprobe и вернулся через `/output` в артефакты |
| Конфигурация Compose | PASS | Разобрана с `--env-file NUL --no-interpolate`: host binds только внутри лаборатории, кроме Engine socket у runner; worker UID 1000 без added capabilities; его control volume read-only; runner без сети/портов |
| START_WINDOWS.ps1 | PASS | Конечная версия разобрана PowerShell parser без выполнения; `up --force-recreate` пересоздаёт runner и обновляет закреплённый image ID после сборки |

Четыре пропуска полной регрессии ожидаемы: dashboard JavaScript требует Node,
CLI parser требует отдельного opt-in, два Docker integration теста требуют
отдельного стенда. Фактическая изоляция и таймаут отдельно проверены через новый
runner описанным выше сценарием. Node/CLI повторно в этой проверке не запускались;
результаты предыдущей проверки остаются в `FIX_VALIDATION_RU.md`.

## Найденная и исправленная несовместимость Windows

Первый фактический возврат workspace с Windows bind mount воспроизвёл
`OSError: [Errno 22] Invalid argument`: файловая система Docker Desktop не
поддержала `renameat2(RENAME_EXCHANGE)`. Система сохранила прежний workspace,
копию в runner и маркер блокировки, вместо продолжения задания с потерянным
результатом.

После исправления `replace_tree` использует журналируемую замену там, где обмен
не поддерживается. Повторная интеграционная проверка вернула исходники и JAR
успешно. Отдельные тесты на настоящем Windows bind mount запускали дочерний
процесс и завершали его через `os._exit` после первого и второго rename с fsync;
`recover_tree` сохранял обновлённые исходники и новый JAR. Проверены также
восстановление старых исходников при пропавшем staging, запрещённые пути и
symlink вместо журнала. Это проверка аварийного завершения процесса, а не
физического отключения питания Windows.

## Сохранённые доказательства и ограничения

Небольшие журналы, JSON конфигурации и артефакты оставлены в
[`audit/runner-scratch/evidence/`](audit/runner-scratch/evidence/).
Большой временный workspace с Gradle cache удалён после копирования доказательств.
Все созданные проверкой контейнеры, контейнеры сборок и оба временных named
volumes удалены; рабочие контейнеры и данные лаборатории не затрагивались.
Тестовые теги образов сохранены для повторной проверки.

Синтетический `restricted-runner-verification.jar`:
`ce2a6f1180c268ab8c976202c9d7296b128585e628ca6301e79bd905863d9cdc`.
MP4, 5312 байт:
`8e68028c06b3ca028bb315d2b290047f4e831331d23415fc47155bcd4d80f54e`.
Запись показывает пустой Xvfb desktop; Minecraft в ней не запускался.

Реальная модель Codex, авторизация, Minecraft/GameTests, EULA и облачный S3 не
использовались. `.env` и хранилище секретов не читались и не изменялись.
Сеть bridge у сборок остаётся доступной для зависимостей; проверяемая граница
относится к подключённым файлам. Сам доверенный runner по-прежнему имеет широкие
полномочия Engine; пользовательский код там не выполняется. Код или конфигурация
этой службы, Docker и ядро входят в границу доверия.
