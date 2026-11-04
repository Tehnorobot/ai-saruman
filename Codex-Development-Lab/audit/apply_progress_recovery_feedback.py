"""Preserve a reviewable maintenance handoff; no job start or verdict edits."""
import hashlib
import json
from pathlib import Path

from common import acceptance,pipeline_memory as memory
from common.memory_archive import write_json

project=Path('/projects/20a4c385b26c4b');ws=project/'workspace'
out=Path('/artifacts/20a4c385b26c4b/f86241b7d5b24e')
feedback_id='progress-recovery-v1-2026-11-02'
scope=json.loads((out/'round-81/pipeline/session.json').read_text(encoding='utf-8'))['scope']
spec_before=(project/'ТЗ.md').read_bytes()
catalog_before=(out/'pipeline/catalog.json').read_bytes()
graph_before=(out/'pipeline/graph.json').read_bytes()
note='''# Изменение процесса: восстановление, реестр и фикстуры

Это передача выполненного обслуживания текущего проекта, а не новые требования
к игровому поведению. ТЗ, каталог, граф и прежние вердикты не изменены.
Пользовательскую паузу сохранить до явного продолжения.

## Что изменено

- Контроллер синхронизирует только несовпадающие копии памяти; полный архив
  остаётся доступным. Статус различает модель, сохранение ответа и память.
- JUnit Launcher до запуска обнаруживает addon/management/delivery тесты.
  Gradle сравнивает результаты с обнаруженным набором вместо второго списка
  методов. См. tools/UNIT_TEST_REGISTRY.md и tools/sync-unit-registry.py.
- Добавлены отдельные именованные проверки готовности мира до действий
  аддона в сценарии жемчуга, маршрута, подводного и подземного прохода.
  Существующие продуктовые проверки/связи не удалены и не ослаблены.
- tools/apply-fixture-preconditions.py зарегистрировал эти проверки только
  в предложении кодера (.lab-pipeline/scenarios-proposed.json). Для поздней
  платформы и ямы указан только зависимый CP2. Проверка airborne уже имела
  самостоятельное предусловие без продуктового условия.
- Новый необязательный execution.precondition_tests позволяет независимому
  runner отличить недостоверную фикстуру от ошибки продукта, сохранив исходный
  JUnit. Невалидная фикстура не даёт PASS и не закрывает прошлый провал.

## Как продолжить принятую работу

Сначала просмотреть текущие частичные правки и это обслуживание. Не начинать
проект заново, не перечитывать весь архив и не повторять уже выполненный реестр.
Сохранить исходный план раунда 81 и исходную базу задания в отчёте; проверить
актуальные файлы перед продолжением. Потребуется актуальный AgentReport и
AgentScenarios/ScenarioManifest для фактической новой ревизии исходников.
Канонический манифест остаётся старым до принятия реального отчёта контроллером.

После новых @Test: сначала discovery, затем sync-unit-registry для предложения;
никогда не генерировать ожидаемые тесты только из успешно исполненного отчёта.
Новые самопроверки реестра не доказывают игровые условия.

Кодер/ревьюер должны сохранить реальный смысл проверок. Предусловие — только
блоки/сущности/инвентарь и синхронизация до соответствующего действия продукта.
Не превращать обнаружение цели, выстрел, урон, лечение или остановку боя в
предусловие. Отдельно исправлять дефект фикстуры прежде вывода о продукте.

В плане группировать изменения по общей причине и независимому наблюдению.
Для повторного провала указать, какая гипотеза не подтвердилась и каким
экспериментом различить причины; независимый функционал разрешено продолжать.
Фиксированной квоты задач, новых агентов и пауз по счётчику нет.

## Граница подтверждения

В обслуживании выполнены реальные офлайн Gradle/unit проверки и компиляция
игровых классов. Minecraft не запускался; исправность игрового поведения,
предусловий в живом клиенте и закрытие условий ТЗ ещё должен подтвердить runner.
Старые FAIL остаются в истории и не переписываются.
'''
record=None
for entry in reversed(memory.index_entries(out)):
    if entry['kind']=='reflection' and entry['producer']=='controller':
        payload=memory.read_payload(out,entry['record_id'])
        if payload.get('maintenance_id')==feedback_id:
            record=memory.load_record(out,entry['record_id']);break
if record is None:
    record=memory.append_event(out,ws,'reflection',{
        'maintenance_id':feedback_id,'artifact_type':'MaintenanceHandoff',
        'summary':'Процесс обновлён: инкрементальная память, ранний статус ответа, единый реестр и проверяемые предусловия фикстур; игровой PASS не назначен.',
        'notes_md':note,'does_not_establish_test_verdict':True},scope=scope,
        producer='controller',tags=['untested'])
(project/'PROGRESS_RECOVERY_RU.md').write_text(note,encoding='utf-8')
rules=ws/'AGENTS.md';text=rules.read_text(encoding='utf-8')
marker='## Maintenance handoff: progress-recovery-v1'
if marker not in text:
    addition='\n\n'+marker+'\nBefore continuing the saved round, read '+record['entry']['payload_ref']['path']+'''.
This is a maintenance record, not a new product requirement. Reuse the current
sources and accepted plan; do not redo registry/fixture work or reset the graph.
Refresh the coder proposal from pre-execution discovery when tests change;
preserve product checkpoint meanings and independent verification. The prior
model turn may have partial edits; inspect them before continuing. See
tools/UNIT_TEST_REGISTRY.md and tools/apply-fixture-preconditions.py on demand.
'''
    backup=out/'maintenance/progress-recovery/AGENTS.before-maintenance.md'
    backup.parent.mkdir(parents=True,exist_ok=True)
    if not backup.exists():backup.write_text(text,encoding='utf-8')
    temporary=rules.with_name('.AGENTS.progress-recovery.tmp')
    temporary.write_text(text+addition,encoding='utf-8');temporary.replace(rules)
assert len(rules.read_text(encoding='utf-8'))<16000
assert (project/'ТЗ.md').read_bytes()==spec_before
assert (out/'pipeline/catalog.json').read_bytes()==catalog_before
assert (out/'pipeline/graph.json').read_bytes()==graph_before
assert hashlib.sha256((ws/record['entry']['payload_ref']['path']).read_bytes()).hexdigest()==record['entry']['payload_ref']['sha256']
receipt={'maintenance_id':feedback_id,'record_id':record['record_id'],'payload_ref':record['entry']['payload_ref'],
    'project_spec_unchanged':True,'catalog_and_graph_unchanged':True,'verdicts_written':False,'project_resumed':False}
write_json(out,'maintenance/progress-recovery/handoff-receipt.json',receipt)
print(json.dumps(receipt,ensure_ascii=False))
