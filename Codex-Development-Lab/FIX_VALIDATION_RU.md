# Независимая проверка исправлений аудита

Дата: 20 октября 2026 года. Проверяющий отделён от авторов исправлений согласно `AGENTS.md`.

**Итог: PASS для исправлений всех 11 ошибок аудита.** Проверены регрессии, настоящие Docker-образы, изолированные команды и полный синтетический цикл разработки. Это не приёмка Minecraft-мода.

## Финальные образы и тесты

| Проверка | Результат |
|---|---|
| `codex-lab-service:fix-test` | Сборка PASS; image ID `84e98b12187c` |
| `codex-lab-worker:fix-test` | Сборка PASS; image ID `4ab52fa66e15` |
| Docker Engine | `29.8.2` |
| Настоящий Codex | `codex-cli 0.162.1`; фактический executor argv с `--help` — PASS |
| Настоящий Gradle | `9.2.1`; offline Java build внутри sandbox — PASS |

Штатный сервисный образ, **без подключения исходников с хоста**, сеть отключена:

```text
python3 -B -m unittest discover -s tests
Ran 50 tests in 7.209s
OK (skipped=4)
```

В этом прогоне выполнены 46 тестов. Четыре проверки требуют отдельных сред и тоже выполнены:

- Настоящий JS dashboard с тем же harness из `tests/test_queue_regressions.py`: Node `22.16.0`, без сети — PASS.
- Фактический argv `agent.run_task()` на настоящем CLI `0.162.1`: `--help`, без сети, авторизации и модельного запуска — PASS. В worker-прогоне runtime-набора: 13 tests, `OK (skipped=2)`; пропущены только две Docker-интеграции.
- Две настоящие Docker-интеграции с controller UID `0:1000`: изоляция и timeout/удаление — PASS. В этом прогоне runtime-набора: 13 tests, `OK (skipped=1)`; пропущен только отдельный CLI-parser test.

Все 50 различных проверок выполнены в соответствующих им средах. Пропуски в базовом образе не выдаются за исполнение интеграций.

## Результаты по ошибкам

| № аудита | Что действительно проверено | Статус |
|---|---|---|
| 1 | Настоящий CLI принимает фактические аргументы исполнителя, включая approval policy и model/reasoning overrides | PASS |
| 2 | Running-попытка отменяется с ожиданием подтверждения; поздний старый CANCELLED не отравляет новую попытку; ID различаются | PASS |
| 3 | Перезапуск последней проверки и пауза во время неё повторяют тот же раунд без нового модельного запроса; незавершённая проверка не получает checkpoint | PASS |
| 4 | Replacement controller забирает просроченную работу; pause/stop подтверждаются после завершения surviving executor, до этого resume/settings заблокированы | PASS |
| 5 | Настоящая команда uid1000 не видит БД, sentinel контроллера, секретные env, mailbox и Docker socket; root filesystem read-only; timeout удаляет контейнер; нет локального fallback | PASS |
| 6 | Все тестовые модули импортируются и выполняются внутри штатного сервисного образа | PASS |
| 7 | Fabric в build.gradle.kts распознаётся; без EULA игровая проверка получает NOT_TESTED, игра не запускается | PASS |
| 8 | Настоящие конкурентные SQLite-транзакции: завершение не перезаписывается pause, claim не получает преждевременное подтверждение паузы; старый owner не может менять задание | PASS |
| 9 | Принудительное изменение файла не нарушает manifest/tar SHA-256; backup ждёт writer lock; настоящая копия с /projects:ro работает | PASS |
| 10 | Смена bucket/prefix/endpoint/region/лимита длительности вызывает новую загрузку; legacy index обновляется; signer отказывает старому назначению/политике | PASS |
| 11 | Настоящий JS дочитывает события после 500, показывает новые 502/503 и использует отдельный cursor каждого задания | PASS |

Дополнительно проверен отказ Docker при очистке: project остаётся в карантине, agent и backup не получают lock до подтверждённого удаления контейнера; новый модельный раунд после такой ошибки не запускается.

До изменения runtime исходные 20 тестов проходили. Три независимые новые регрессии сначала воспроизвели FAIL: пропуск финальной проверки, Kotlin/Fabric detection и локальное исполнение команды при недоступном sandbox. После исправлений эти же проверки проходят.

## Полный синтетический цикл

В новом Compose project запущены dashboard, worker, agent, backup, storage и init-permissions. Все данные, mailbox, backup, runner-output, auth и S3 state — отдельные пустые тестовые каталоги/volumes. Рабочая .env заменена `--env-file NUL`, S3 credentials пустые, EULA false, dashboard использует случайный порт на 127.0.0.1. Проверены health endpoint и пустая очередь; пять сервисов работают, init-permissions завершён с exit 0.

В настоящем agent-контейнере uid1000 установлен временный fake Codex executable. Bootstrap проверяет через `shutil.which`, что будет запущен именно он. Реальный Codex и авторизация в этом цикле не используются.

Через dashboard API загружен synthetic.md с max_rounds=1. Наблюдаемая цепочка:

```text
upload → SQLite queue → root:1000 worker → mailbox → uid1000 agent/fake Codex
→ отдельный Docker job → настоящий pytest и реальный assert
→ verification.json → FINISHED → backup → SHA-256 каждого tar member
```

Результат:

```json
{"end_to_end":"PASS","job_status":"FINISHED","pytest":"PASS","real_pytest":"1 passed","backup_hashed_members":27}
```

Fake Codex создал pytest.ini и тест с `assert sum([19, 23]) == 42`. Сборка, запуск pytest, статус и backup выполнены настоящими сервисами. Это подтверждает mailbox permissions, host-path mapping, checkpoints и права сервисов в совместной работе.

Отдельно настоящий offline Gradle собрал Java-проект со встроенным плагином java, без внешних зависимостей, за 5.65 s. workspace-1.0.jar существует, читается как ZIP и содержит Main.class:

```text
SHA-256: 17a9d2002363ddaf0a8ea62712d4fdac47999ab69f0d0c817041ab3f95f67aa9
```

Это синтетический Java JAR, не Minecraft-мод. Использован Groovy DSL. Реальная Kotlin DSL-компиляция и Minecraft dependencies отдельно не запускались; Kotlin detection проверен регрессией.

## SQLite, WAL и read-only backup

Два настоящих контейнера разделяли новый одноразовый volume /data. Controller 0:1000 создал SQLite и удерживал соединение открытым на протяжении всей проверки. Наблюдаемые права: DB uid=0, gid=1000, mode=664; WAL mode=664.

```text
root:1000 init → uid1000 log + claim → root:1000 log → uid1000 log → root читает все записи
```

Последовательность выполнена без закрытия удерживаемого WAL-соединения. Это проверяет холодный старт controller раньше dashboard. Отдельный настоящий backup с volume /projects:ro проверил все три manifest/tar hashes. В полном E2E проверено 27 архивных файлов.

## Воспроизведение основных проверок

Команды из корня репозитория:

```powershell
$verifyConfig = Join-Path $env:TEMP 'codex-lab-verify-docker'
New-Item -ItemType Directory -Force -Path $verifyConfig | Out-Null
docker --config $verifyConfig build -f Dockerfile.service -t codex-lab-service:fix-test .
docker --config $verifyConfig build -f Dockerfile.worker -t codex-lab-worker:fix-test .
docker --config $verifyConfig run --rm --network none codex-lab-service:fix-test python3 -B -m unittest discover -s tests -v
```

CLI-parser regression на закреплённом бинарнике, исходники только для чтения:

```powershell
$repoPath = (Get-Location).Path
docker --config $verifyConfig run --rm --network none --mount "type=bind,source=$repoPath,target=/src,readonly" --workdir /src --env PYTHONPATH=/src --env LAB_VERIFY_PINNED_CLI=1 codex-lab-worker:fix-test python3 -B -m unittest discover -s tests -p test_runtime_regressions.py -v
```

Настоящие Docker-интеграции используют новый scratch bind для Docker Desktop host-path mapping. Только доверенный тестовый controller получает Engine socket:

```powershell
$scratchPath = Join-Path $env:TEMP ('codex-lab-verify-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratchPath | Out-Null
docker --config $verifyConfig run --rm --network none --user 0:1000 --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add CHOWN --cap-add FOWNER --security-opt no-new-privileges:true --mount "type=bind,source=$scratchPath,target=/verification" --mount 'type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock' --env LAB_DOCKER_TEST_ROOT=/verification --env LAB_RUNNER_OUTPUT=/verification/runner-output --env LAB_RUNNER_IMAGE=codex-lab-worker:fix-test --env LAB_RUN_DOCKER_TESTS=1 codex-lab-service:fix-test python3 -B -m unittest discover -s tests -p test_runtime_regressions.py -v
```

Для повторения cross-UID проверки нужны два контейнера codex-lab-service:fix-test с одним **новым** named volume /data, `--network none`, `LAB_DB=/data/lab.sqlite3`. Первый — `--user 0:1000 --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add FOWNER`; второй — `--user 1000:1000 --cap-drop ALL`. Первый вызывает db.create_project и держит `with db.connect() as held`, сделав SELECT; второй вызывает db.log и db.claim; первый затем db.log; второй снова db.log. Проверяются db.events_list и права DB/WAL. Для синхронизации использованы только файлы-маркеры этого volume; ожидания ограничены 30 секундами. После проверки контейнеры и volume удаляются.

Compose проверен установленным standalone executable `C:\Users\aimihail\AppData\Local\Programs\DockerDesktop\resources\cli-plugins\docker-compose.exe`, с DOCKER_CONFIG на пустой временный каталог. Эквивалентная команда при доступном Docker Compose plugin:

```powershell
docker compose --env-file NUL -f compose.yaml config --no-interpolate --no-env-resolution --quiet
```

Для повторения cold-start/E2E сначала нужна отдельная временная конфигурация: `config --no-interpolate --no-env-resolution --format json`. У всех bind mounts заменить source новыми scratch-каталогами, сохранив только /var/run/docker.sock; удалить фиксированные resource name у volumes/network; выбрать новый -p project name. Заменить образы на :fix-test, удалить build, оставить pull policy never. Dashboard ports заменить целиком на `{target:8080,published:"0",host_ip:"127.0.0.1",protocol:"tcp"}`. Задать LAB_RUNNER_IMAGE=codex-lab-worker:fix-test, EULA=false, пустые AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/YC_S3_BUCKET. Auth и S3 state должны быть новыми volumes.

```powershell
docker compose --env-file NUL -p $uniqueProject -f $scratchComposeFile up -d --no-build
# Проверить /healthz и /api/jobs=[]; для E2E сначала установить проверяемый fake Codex в agent.
# Upload synthetic.md с max_rounds=1; ждать FINISHED, pytest PASS и backup с совпадающими hashes.
docker compose --env-file NUL -p $uniqueProject -f $scratchComposeFile down --volumes --remove-orphans
```

Все созданные проверяющим Docker-контейнеры, named volumes и networks удалены. Исправленные образы оставлены под отдельными тегами :fix-test; действующие сервисы не обновлялись.

## Границы выводов

Рабочая .env, секреты, действующая очередь и проекты пользователя не читались и не менялись. EULA не принималась, codex login не выполнялся, модельные запросы не отправлялись.

Настоящие Minecraft-клиент, GameTests и Yandex S3 upload — **NOT_TESTED**. Облачная логика проверена с наблюдаемым fake S3. Успешный синтетический цикл не доказывает готовность конкретного игрового мода и его сценариев.