"""Read-only, bounded activity labels from controller state and current CLI actions."""
from datetime import datetime, timezone
import json
from itertools import islice
import os
from pathlib import Path
import re
import shlex
import time

from common.history import _open

LABELS = {
    'memory_sync': ('Синхронизирует память', 'Обработчик проверяет архивные копии; модель в этот момент не пишет код.'),
    'processing_agent_result': ('Сохраняет результат агента', 'Запрос к модели завершён; обработчик сохраняет журнал и проверяет ответ.'),
    'provider_wait': ('Ожидает восстановления квоты', 'Повторные попытки включены; код и текущая фаза сохранены.'),
    'retrying_provider': ('Ожидает повторного запроса', 'Ответ провайдера прерван; обработчик продолжит сохранённую фазу.'),
    'decomposing': ('Выделяет требования и задачи', 'Планировщик читает ТЗ и строит смысловой граф зависимостей.'),
    'reviewing': ('Проверяет код и сценарии', 'Независимый ревьюер читает исходники; вердикт тестов ещё не установлен.'),
    'reflecting': ('Пересматривает подход', 'Планировщик сравнивает ошибки, гипотезы и эксперименты, затем выбирает полезную работу.'),
    'pipeline_waiting': ('Ожидает внешние условия', 'Причина ожидания и сохранённый пакет доступны в окне пайплайна.'),
    'acceptance_verified': ('ТЗ проверено', 'Все условия имеют актуальные независимые результаты после полного финального запуска.'),
    'planning': ('Планирует следующий шаг', 'Отдельный планировщик анализирует ошибки, требования и следующие задачи. Код проекта не изменяет.'),
    'queued': ('Ожидает запуска', 'Задание находится в очереди.'),
    'paused': ('На паузе', 'Разработка не возобновится без команды «Продолжить».'),
    'quota_wait': ('Ожидает восстановления квоты', 'Обработчик повторит проверку доступности по расписанию.'),
    'auth_required': ('Требуется авторизация', 'Для продолжения нужно восстановить вход Codex.'),
    'failed': ('Остановлено из-за ошибки', 'Подробности доступны в ходе разработки и журналах.'),
    'stopped': ('Задание остановлено', 'Разработка остановлена пользователем.'),
    'budget_finished': ('Лимит раундов достигнут', 'Завершение бюджета не подтверждает выполнение ТЗ.'),
    'developing': ('Разрабатывает мод', 'Агент анализирует требования, пишет код или исправляет ошибки.'),
    'reading_spec': ('Изучает ТЗ', 'В журнале агента выполняется команда чтения исходных требований.'),
    'acceptance_planning': ('Работает с планом приёмки', 'В журнале агента выполняется операция с каталогом или планом проверок ТЗ.'),
    'writing_code': ('Пишет код и исправляет ошибки', 'В журнале агента выполняется операция изменения файлов.'),
    'agent_checks': ('Проверяет код внутри хода агента', 'Агент запустил команду сборки или тестов; это не независимый вердикт.'),
    'agent_minecraft': ('Агент выполняет команду игровых тестов', 'Зафиксирован запуск игровой команды. Готовность Minecraft и успех теста ещё не подтверждены.'),
    'waiting_agent': ('Ожидает свободного агента', 'Запрос передан в очередь агента.'),
    'verifying': ('Сборка и независимые тесты', 'Этап включает сборку, автотесты и доступные игровые сценарии. Точный подэтап обработчик пока не передаёт.'),
    'recovering': ('Ожидает восстановления обработчика', 'Lease истёк; сохранённое задание ожидает подхвата обработчиком.'),
    'pausing': ('Обрабатывает запрос паузы', 'Ожидается остановка текущих процессов и сохранение состояния.'),
    'stopping': ('Останавливает задание', 'Ожидается подтверждение остановки текущих процессов.'),
    'unknown': ('Этап уточняется', 'Обработчик не передал распознаваемый этап. Подробности доступны в журнале.')
}


def _command_stage(command):
    value=command[:16000].lower()
    try:
        tokens=shlex.split(command[:16000])
        for _ in range(2):
            if tokens and Path(tokens[0]).name.lower() in ('bash','sh','pwsh','powershell'):
                index=next((i for i,t in enumerate(tokens) if t.lower() in ('-c','-lc','-command')),None)
                if index is None or index+1>=len(tokens):break
                tokens=shlex.split(tokens[index+1])
        while tokens and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*=.*',tokens[0]):tokens.pop(0)
    except ValueError:return 'developing'
    if not tokens:return 'developing'
    executable=Path(tokens[0]).name.lower(); arguments=tokens[1:]
    if executable in ('echo','printf','write-output'):return 'developing'
    if executable in ('gradlew','gradle','gradlew.bat'):
        if not any(t in arguments for t in ('--dry-run','-m','--help','-h','tasks')) and any(
                t.rsplit(':',1)[-1] in ('runClientGameTest','runClient','runServerGameTest') for t in arguments):
            return 'agent_minecraft'
        return 'agent_checks'
    if executable in ('mvn','pytest') or (executable in ('python','python3') and any(t in arguments for t in ('pytest','unittest'))):return 'agent_checks'
    if executable in ('npm','pnpm','yarn') and 'test' in arguments:return 'agent_checks'
    if 'acceptance-plan.json' in value or 'acceptance-catalog.json' in value:return 'acceptance_planning'
    if re.search(r'apply_patch|write_text\(|write_bytes\(|set-content\b|out-file\b',value):return 'writing_code'
    if ('тз.md' in value or 'tz.md' in value) and re.search(r'cat\b|rg\b|sed\b|read_text\(|get-content\b|type\b',value):return 'reading_spec'
    return 'developing'


def _agent_stage(job, mailbox, number, now):
    if not mailbox or not re.fullmatch('[0-9a-f]{14}',job.get('id','')):return None,False
    root=Path(mailbox); prefix=job['id']+f'-round{number:02d}'
    for directory in ('running','requests'):
        try:
            candidates=sorted((p for p in islice((root/directory).iterdir(),512)
                               if p.name.startswith(prefix) and p.suffix=='.json'))[:32]
        except OSError:continue
        for path in candidates:
            tid=path.stem
            if not re.fullmatch(re.escape(prefix)+r'(?:-[0-9a-f]{32})?',tid):continue
            try:
                with _open(root,(directory,path.name)) as source:
                    raw=source.read(256*1024+1)
                if len(raw)>256*1024:continue
                task=json.loads(raw)
                if not isinstance(task,dict) or (task.get('id'),task.get('job_id'),task.get('project_id'))!=(tid,job['id'],job.get('project_id')):continue
                if directory=='requests':return 'waiting_agent',False
                with _open(root,('logs',tid+'.jsonl')) as source:
                    info=os.fstat(source.fileno()); offset=max(0,info.st_size-128*1024)
                    source.seek(offset)
                    if offset:source.readline(65536)
                    data=source.read(128*1024)
                stale=now-info.st_mtime>120
                if stale:return None,True
                pending={}
                for line in data.splitlines():
                    if len(line)>65536:continue
                    try:event=json.loads(line)
                    except (ValueError,UnicodeError,RecursionError):continue
                    if not isinstance(event,dict) or event.get('type') not in ('item.started','item.updated','item.completed'):continue
                    item=event.get('item')
                    if not isinstance(item,dict) or item.get('type')!='command_execution':continue
                    key=item.get('id')
                    if not isinstance(key,str):continue
                    if event['type']=='item.completed':pending.pop(key,None)
                    elif isinstance(item.get('command'),str):pending[key]=item['command']
                if pending:return _command_stage(list(pending.values())[-1]),False
            except (OSError,ValueError,TypeError,UnicodeError,RecursionError):continue
    return None,False


def snapshot(job, events=(), mailbox=None, now=None, pipeline_state=None):
    now=time.time() if now is None else now
    status=job.get('status'); phase=job.get('phase','')
    number=job.get('round',0)+(1 if status=='RUNNING' and phase in ('codex_development','planning') else 0)
    stage={'QUEUED':'queued','USER_PAUSED':'paused','QUOTA_PAUSED':'quota_wait',
           'FAILED':'failed','STOPPED':'stopped','FINISHED':'budget_finished'}.get(status,'unknown')
    if status=='USER_PAUSED' and phase=='auth_required':stage='auth_required'
    source='controller'; stale=False
    if status=='FINISHED' and phase=='acceptance_verified':stage='acceptance_verified'
    if status=='RUNNING':
        if job.get('cancel_requested'):stage='stopping'
        elif job.get('pause_requested'):stage='pausing'
        elif job.get('lease_until',now+1)<=now:stage='recovering';stale=True
        elif phase=='verifying':stage='verifying'
        elif phase=='planning':stage='planning'
        elif phase in ('decomposing','reviewing','reflecting','pipeline_waiting'):stage=phase
        elif phase in ('processing_agent_result','provider_wait','retrying_provider'):stage=phase
        elif phase=='codex_development':
            stage='developing'; operation,stale=_agent_stage(job,mailbox,number,now)
            if operation:stage=operation;source='agent_cli'
        if not job.get('pause_requested') and not job.get('cancel_requested') and stage!='recovering' and isinstance(pipeline_state,dict):
            service=pipeline_state.get('service_operation') or {}
            operation=pipeline_state.get('operation') or {}
            if service.get('active') and service.get('stage')=='memory_sync':
                stage='memory_sync';source='controller';number=service.get('round',number)
            elif phase!='verifying' and operation.get('status')=='processing_result':stage='processing_agent_result'
            elif phase!='verifying' and operation.get('status')=='retrying':
                stage='provider_wait' if operation.get('retry_category')=='rate_limit' else 'retrying_provider'
    started=None
    for event in sorted(events,key=lambda e:e.get('id',0),reverse=True):
        message=event.get('message','')
        if not isinstance(message,str):continue
        if ((phase=='codex_development' and message.startswith(f'Раунд {number}/')
                and ': Codex создаёт или исправляет код;' in message)
                or (phase=='verifying' and message.startswith('Независимые тесты:'))):
            started=event.get('created_at');break
    title,detail=LABELS[stage]
    service=(pipeline_state or {}).get('service_operation') or {}
    if stage=='memory_sync' and type(service.get('total')) is int:
        detail=f"Проверено записей: {service.get('completed',0)} из {service['total']}. Полная история сохраняется."
    observed_stage=(pipeline_state or {}).get('operation') or {}
    service_started=service.get('started_at') if stage=='memory_sync' else observed_stage.get('result_received_at') if stage=='processing_agent_result' else observed_stage.get('retry_started_at') if stage in ('provider_wait','retrying_provider') else None
    if type(service_started) in (int,float):
        started=datetime.fromtimestamp(service_started,timezone.utc).isoformat()
    return {'job_id':job['id'],'status':status,'phase':phase,'stage':stage,'title':title,'detail':detail,
        'current_round':number,'max_rounds':job.get('max_rounds'), 'started_at':started,
        'observed_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
        'source':source,'stale':stale}
