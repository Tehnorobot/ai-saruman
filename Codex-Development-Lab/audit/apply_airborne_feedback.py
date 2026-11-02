"""Apply the user's project-specific clarification without changing an active spec.

Run inside the existing worker. No model calls, job control or acceptance writes.
"""
import hashlib
import json
from pathlib import Path

from common import pipeline_memory as memory
from common.memory_archive import write_json

PROJECT = '20a4c385b26c4b'
JOB = 'f86241b7d5b24e'
FEEDBACK_ID = 'user-airborne-targets-2026-10-31'
EFFECTIVE_ROUND = 77
project = Path('/projects') / PROJECT
workspace = project / 'workspace'
output = Path('/artifacts') / PROJECT / JOB
receipt_path = 'pipeline/user-airborne-feedback-receipt.json'

clarification = '''# Уточнение пользователя: воздушные цели и снаряды

Применить с раунда 77 в текущем проекте Auto Defense. Это явное уточнение
пользователя к существующим требованиям, а не правило для других проектов.

## Требуемое поведение

Правила выбора цели действуют для наземных, водных и всех летающих мобов,
которые подходят под выбранный режим. Полёт сам по себе не делает моба врагом.
Сохранить радиус 32 блока, выбор ближайшего подходящего врага, исключение мирных
существ и агрессию именно против персонажа для нейтральных мобов/самозащиты.
Не ограничивать поиск одной веткой Java-наследования или одним видом моба.

Выбирать оружие с учётом фактической досягаемости цели и линии выстрела.
Если воздушная цель находится в пределах 8 блоков, но мечом до неё не достать,
использовать лук при наличии стрел и безопасной линии выстрела. Это уточняет
порог дальнего боя в исходном ТЗ. Проверять фактический выпуск стрелы и урон.
Без подходящего выстрела выбирать безопасное доступное действие; не повторять
бесконечно наземный маршрут к недостижимой воздушной цели.

Существующее требование защиты от снарядов относится и к снарядам воздушных
врагов: оценивать реальную угрозу персонажу, а не только близость стрелявшего
моба. Проверять применимость щита по правилам игры; не заявлять, что он
блокирует любую атаку. Новая способность отбивать огненные шары не требуется.

## Наблюдение и диагностика — разная степень подтверждения

Пользователь сообщил: «На гаста он почему-то не реагировал». Версия фактически
загруженного JAR и полный игровой лог этого эпизода пока не установлены.
Это ручное сообщение об ошибке, не независимый FAIL и не доказанная причина.

При просмотре исходников текущего проекта обнаружено:
- collectCandidates использует getEntitiesByClass(HostileEntity.class).
- В локальном pinned Minecraft 1.21.11 GhastEntity наследует MobEntity и
  реализует Monster; текущий фильтр его не перечисляет.
- BowEngagementPolicy выбирает MELEE при distance <= 8 без проверки
  досягаемости по высоте.
- ShieldGuardTask.canDefend ограничивается близостью выбранного HostileEntity
  (squaredDistance <= 20.25); самостоятельной оценки огненного шара в этой
  ветке при просмотре не обнаружено. Поведение самой зависимости исследовать
  отдельно; отсутствие ветки аддона не доказывает отсутствие любой защиты.

Диагностика относится к просмотренным исходникам; проверить актуальные файлы.
Не объявлять автоматически, что сообщение пользователя воспроизведено.

## Следующая работа и проверки

В следующем плане начать общий разбор классификации подходящих воздушных
врагов и исправление затронутых веток. Связать работу с существующими
требованиями/условиями выбора цели, оружия, щита и контроля маршрута; не
пересоздавать проект или граф только ради планирования. Размер связанной
работы выбирает планировщик, независимый функционал разрешено продолжать.

По фактам pinned API подобрать виды/группы мобов для проверок, не сводить
исправление к GhastEntity. Воспроизвести случай гаста; проверить высокую цель,
близкую цель вне досягаемости меча, движение, закрытую линию выстрела,
отсутствие стрел и сохранность исключения мирных/неагрессивных нейтральных.
Для снаряда проверить реальную защиту от источника за пределами ближнего боя.
Выбрать содержательные сценарии без искусственной квоты на их число.

Кодер реализует поведение и исполняемые проверки; ревьюер проверяет полноту
и смысл проверок; независимый runner устанавливает результат. Фото/видео
остаются дополнительными материалами и не блокируют работу.

Зафиксировать в notes_md, как учтено уточнение, что сделано и что осталось,
со ссылками на независимые результаты. Сохранять открытые случаи до проверки.
При уже существующем независимом подтверждении уточнённых случаев не
реализовывать их повторно: использовать историю, проверить затронутые
регрессии и продолжать следующую полезную работу.
'''

session = json.loads((output / 'round-76/pipeline/session.json').read_text(encoding='utf-8'))
scope = dict(session['scope'])
spec_before = (project / 'ТЗ.md').read_bytes()
if hashlib.sha256(spec_before.decode('utf-8').encode()).hexdigest() != scope['spec_revision']:
    raise RuntimeError('Specification changed; clarification needs scope review')
catalog_before = (output / 'pipeline/catalog.json').read_bytes()
graph_before = (output / 'pipeline/graph.json').read_bytes()
catalog = json.loads(catalog_before)
requirement_ids = ['R02', 'R03', 'R04', 'R08', 'R09', 'R10', 'R15', 'R31']
task_ids = ['T03', 'T04', 'T05', 'T06', 'T09', 'T11', 'T12', 'T17', 'T29']
if not set(requirement_ids) <= {r['id'] for r in catalog['requirements']}:
    raise RuntimeError('Requirement mapping is stale')
graph = json.loads(graph_before)
if not set(task_ids) <= {t['id'] for t in graph['tasks']}:
    raise RuntimeError('Task mapping is stale')

existing = None
for entry in reversed(memory.index_entries(output)):
    if entry['producer'] == 'user' and entry['kind'] == 'manual':
        payload = memory.read_payload(output, entry['record_id'])
        if payload.get('feedback_id') == FEEDBACK_ID:
            existing = memory.load_record(output, entry['record_id'])
            break
if existing is None:
    existing = memory.append_event(output, workspace, 'manual', {
        'feedback_id': FEEDBACK_ID,
        'artifact_type': 'UserClarification',
        'summary': 'С раунда 77: все подходящие воздушные враги, досягаемость оружия и защита от снарядов; сообщение о гасте ещё требует воспроизведения.',
        'effective_from_round': EFFECTIVE_ROUND,
        'user_observation': 'На гаста аддон не реагировал.',
        'observation_status': 'user_reported_not_independently_reproduced',
        'clarification_md': clarification,
        'related_condition_ids': sorted({cid for r in catalog['requirements'] if r['id'] in requirement_ids for cid in r['condition_ids']}),
        'does_not_establish_test_verdict': True,
    }, scope=scope, producer='user', task_ids=task_ids,
        requirement_ids=requirement_ids, tags=['untested'])

reference = existing['entry']['payload_ref']
marker = '## User clarification: airborne targets (effective from round 77)'
rules = workspace / 'AGENTS.md'
original_rules = rules.read_text(encoding='utf-8')
notice = f'''\n\n{marker}
This is an explicit user clarification for THIS project, not a global skill.
For scope.round >= 77, before choosing the next work or reviewing/coding it,
read {reference['path']} (immutable user memory, feedback_id={FEEDBACK_ID}).
Plan the airborne-target/weapon-reach/projectile findings with related existing
tasks; preserve existing work and independent verification. Record disposition
and remaining cases in notes_md. Do not replay already independently verified
fixes. No fixed package size, new pause gate, or graph/project reset is required.
The full clarification is also in project/AIRBORNE_FEEDBACK_RU.md for the owner.
For round 76, retain the already assigned scope; apply this clarification from 77.
'''
if marker not in original_rules:
    backup = output / 'pipeline/AGENTS.before-airborne-feedback.md'
    if not backup.exists():
        backup.write_text(original_rules, encoding='utf-8')
    # The running coder may edit sources, but must not lose its own rules edits.
    if rules.read_text(encoding='utf-8') != original_rules:
        raise RuntimeError('Workspace rules changed while preparing update')
    temporary = rules.with_name('.AGENTS.airborne-feedback.tmp')
    temporary.write_text(original_rules + notice, encoding='utf-8')
    temporary.replace(rules)

(project / 'AIRBORNE_FEEDBACK_RU.md').write_text(clarification, encoding='utf-8')
stored = memory.read_payload(output, existing['record_id'])
mirror = workspace / reference['path']
assert hashlib.sha256(mirror.read_bytes()).hexdigest() == reference['sha256']
assert stored['clarification_md'] == clarification
assert (project / 'ТЗ.md').read_bytes() == spec_before
assert (output / 'pipeline/catalog.json').read_bytes() == catalog_before
assert (output / 'pipeline/graph.json').read_bytes() == graph_before
assert len(rules.read_text(encoding='utf-8')) < 16000
assert rules.read_text(encoding='utf-8').count(marker) == 1
receipt = {
    'feedback_id': FEEDBACK_ID, 'effective_from_round': EFFECTIVE_ROUND,
    'record_id': existing['record_id'], 'payload_ref': reference,
    'rules_path': 'projects/' + PROJECT + '/workspace/AGENTS.md',
    'canonical_spec_unchanged': True, 'catalog_and_graph_unchanged': True,
    'test_verdicts_written': False, 'model_invoked': False,
    'service_restart': False,
    'note': 'API agents load workspace rules at launch; existing turn remains in progress.',
}
write_json(output, receipt_path, receipt)
print(json.dumps(receipt, ensure_ascii=False))
