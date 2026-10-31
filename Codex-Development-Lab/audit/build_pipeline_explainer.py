"""Build local architecture documentation. Does not import or change the worker."""
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'pipeline'
OUT.mkdir(parents=True, exist_ok=True)
S = {'type': 'string', 'minLength': 1}
I = {'type': 'integer', 'minimum': 0}
B = {'type': 'boolean'}
def enum(*values): return {'type': 'string', 'enum': list(values)}
def arr(item): return {'type': 'array', 'items': item}
def ref(name): return {'$ref': '#/$defs/' + name}
def obj(**props):
    return {'type': 'object', 'properties': {key:deepcopy(value) for key,value in props.items()}, 'required': list(props), 'additionalProperties': False}
def optional(schema, *names):
    schema['required'] = [k for k in schema['required'] if k not in names]
    return schema

D = {}
D['Hash'] = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
D['ArtifactRef'] = obj(path=S, sha256=ref('Hash'), media_type=S)
D['SourceRef'] = obj(artifact=ref('ArtifactRef'), start_line={'type':'integer','minimum':1}, end_line={'type':'integer','minimum':1}, excerpt=S)
D['Scope'] = obj(project_id=S, spec_revision=ref('Hash'), source_revision=ref('Hash'), round=I)
D['RoleConfig'] = obj(role=enum('planner','coder','reviewer'), provider=S, model=S, reasoning=S)
D['Budget'] = obj(remaining_tokens=I, remaining_seconds=I)
D['Requirement'] = obj(id=S, statement=S, source_refs=arr(ref('SourceRef')), condition_ids=arr(S))
D['Condition'] = obj(id=S, requirement_id=S, expected=S, verification=enum('automated','manual','mixed'))
D['Task'] = obj(id=S, title=S, requirement_ids=arr(S), condition_ids=arr(S), depends_on=arr(S), outcome=S, verification_approach=S)
D['TaskState'] = obj(task_id=S, status=enum('pending','in_progress','verified','needs_retest','manual_pending','blocked'), result_refs=arr(ref('ArtifactRef')))
D['Action'] = obj(id=S, task_ids=arr(S), kind=enum('implement','repair','diagnose','verify','manual'), goal=S, expected_observation=S, depends_on_action_ids=arr(S), condition_ids=arr(S))
D['Hypothesis'] = obj(id=S, statement=S, observation_refs=arr(ref('SourceRef')), experiment=S, prediction=S)
D['DeferredTask'] = obj(task_id=S, reason=S, return_when=S)
D['ManualCheck'] = obj(condition_ids=arr(S), steps=arr(S), expected=S)
D['Issue'] = obj(path=S, code=S, expected=S, received_type=S, example_json=S, instruction=S)
D['Checkpoint'] = obj(id=S, condition_ids=arr(S), assertion=S, observable=S, comparator=enum('eq','ne','lt','le','gt','ge','contains','event'), expected={'type':['string','number','boolean']})
D['Scenario'] = obj(id=S, task_ids=arr(S), condition_ids=arr(S), setup=arr(S), steps=arr(S), checkpoints=arr(ref('Checkpoint')), test_file=ref('ArtifactRef'), runner_profile=S, media=arr(enum('video','screenshot')))
D['Finding'] = obj(id=S, kind=enum('requirement_violation','technical_risk','suggestion'), severity=enum('high','medium','low'), requirement_ids=arr(S), code_refs=arr(ref('SourceRef')), observation=S, consequence=S, suggested_check=S)
D['FindingResponse'] = obj(finding_id=S, disposition=enum('fixed','rejected_with_reason','unverified'), explanation=S, evidence_refs=arr(ref('SourceRef')))
D['Observation'] = obj(checkpoint_id=S, status=enum('PASS','FAIL','NOT_EXECUTED','SKIPPED','ERROR'), actual=S, evidence_refs=arr(ref('SourceRef')))
D['ScenarioResult'] = obj(scenario_id=S, status=enum('PASS','FAIL','NOT_EXECUTED','SKIPPED','ERROR'), checkpoints=arr(ref('Observation')), media_refs=arr(ref('ArtifactRef')))
D['Usage'] = obj(input_tokens=I, output_tokens=I, cache_read_tokens=I, cache_write_tokens=I, measurement=enum('provider','estimated','unavailable'))
D['Message'] = obj(role=enum('system','user','assistant','tool'), content=S)
D['ToolDefinition'] = obj(name=S, description=S, input_schema=ref('ArtifactRef'))
D['Coverage'] = obj(condition_id=S, status=enum('verified','failed','untested','manual_pending','needs_retest'), result_refs=arr(ref('ArtifactRef')))
D['MemoryLink'] = obj(relation=enum('responds_to','tests_hypothesis','changes','verifies','supersedes','returns_after'), record_id=S)

EX = {}
DESCRIPTIONS = {}
def contract(name, description, example, **fields):
    D[name] = obj(contract={'const':name}, version={'const':'proposal-v1'}, **fields)
    EX[name] = {'contract':name, 'version':'proposal-v1', **example}
    DESCRIPTIONS[name] = description
    D[name]['description'] = description

def artifact(path, media='application/json', char='a'):
    return {'path':path, 'sha256':char*64, 'media_type':media}
scope = {'project_id':'demo-trees','spec_revision':'a'*64,'source_revision':'b'*64,'round':3}
review_scope = {**scope, 'source_revision':'d'*64}
post_scope = {**scope, 'source_revision':'e'*64}
previous_scope = {**scope, 'round':2}
spec = artifact('input/spec.md','text/markdown')
spec_ref = {'artifact':spec,'start_line':1,'end_line':2,'excerpt':'Посадить саженец.\nВырастить дерево костной мукой.'}
log = artifact('runs/2/game.log','text/plain','c')
hit = {'artifact':log,'start_line':18,'end_line':18,'excerpt':'sapling_placed=false'}
catalog = artifact('planning/requirements.json')
graph = artifact('planning/tasks.json')
plan = artifact('rounds/3/plan.json')
report = artifact('rounds/3/coder.json')
manifest = artifact('tests/scenarios.json')
testfile = artifact('src/gametest/PlantTest.java','text/x-java-source')
memory = artifact('memory/index.json')
result = artifact('rounds/2/verification.json')
roles = [{'role':r,'provider':'selected-provider','model':'selected-model','reasoning':'default'} for r in ('planner','coder','reviewer')]
budget = {'remaining_tokens':100000,'remaining_seconds':3600}
task1 = {'id':'T1','title':'Посадка саженца','requirement_ids':['R1'],'condition_ids':['C1'],'depends_on':[],'outcome':'Саженец установлен в мире','verification_approach':'Измерить блок после посадки'}
task2 = {'id':'T2','title':'Выращивание дерева','requirement_ids':['R2'],'condition_ids':['C2'],'depends_on':['T1'],'outcome':'Саженец заменён деревом','verification_approach':'Проверить ствол после применения костной муки'}
checkpoint = {'id':'CP2','condition_ids':['C1'],'assertion':'Саженец установлен','observable':'sapling_placed','comparator':'eq','expected':True}
scenario = {'id':'S1','task_ids':['T1','T2'],'condition_ids':['C1','C2'],'setup':['Плоская площадка; саженец и костная мука доступны'],'steps':['Запустить посадку','Запустить выращивание'], 'checkpoints':[{'id':'CP1','condition_ids':[],'assertion':'Площадка найдена','observable':'site_found','comparator':'eq','expected':True},checkpoint,{'id':'CP3','condition_ids':['C2'],'assertion':'Дерево выросло','observable':'tree_present','comparator':'eq','expected':True}], 'test_file':testfile,'runner_profile':'minecraft-client-gametest','media':['video']}

contract('ProjectInput','Панель → приём ТЗ. Файл Markdown и явные уточнения.',
 {'project_id':'demo-trees','spec_file':spec,'clarifications':[],'roles':roles,'budget':budget},
 project_id=S,spec_file=ref('ArtifactRef'),clarifications=arr(ref('ArtifactRef')),roles=arr(ref('RoleConfig')),budget=ref('Budget'))
contract('ProjectSnapshot','Сохранённый вход. Контроллер вычисляет ревизии, затем фиксирует снимок.',
 {'scope':scope,'spec_file':spec,'clarifications':[],'roles':roles,'budget':budget},
 scope=ref('Scope'),spec_file=ref('ArtifactRef'),clarifications=arr(ref('ArtifactRef')),roles=arr(ref('RoleConfig')),budget=ref('Budget'))
contract('DecompositionInput','Контроллер → LLM-планировщик. Исходное ТЗ доступно целиком по ссылке.',
 {'scope':scope,'spec_file':spec,'clarifications':[],'previous_catalog_refs':[],'instructions':['Выдели функции без новых требований. Сохрани ссылки на исходник.']},
 scope=ref('Scope'),spec_file=ref('ArtifactRef'),clarifications=arr(ref('ArtifactRef')),previous_catalog_refs=arr(ref('ArtifactRef')),instructions=arr(S))
contract('RequirementsCatalog','LLM → контроллер и ревьюер. Смысловые требования и измеримые условия.',
 {'scope':scope,'requirements':[{'id':'R1','statement':'Посадить саженец','source_refs':[spec_ref],'condition_ids':['C1']},{'id':'R2','statement':'Вырастить дерево','source_refs':[spec_ref],'condition_ids':['C2']}], 'conditions':[{'id':'C1','requirement_id':'R1','expected':'В мире установлен саженец','verification':'automated'},{'id':'C2','requirement_id':'R2','expected':'Дерево выросло','verification':'automated'}], 'open_questions':[],'unclassified_source_refs':[]},
 scope=ref('Scope'),requirements=arr(ref('Requirement')),conditions=arr(ref('Condition')),open_questions=arr(S),unclassified_source_refs=arr(ref('SourceRef')))
contract('TaskGraph','LLM → контроллер. Зависимости задач; смысл проверяет LLM, циклы — алгоритм.',
 {'scope':scope,'catalog_ref':catalog,'tasks':[task1,task2]},
 scope=ref('Scope'),catalog_ref=ref('ArtifactRef'),tasks=arr(ref('Task')))
contract('PlannerInput','Контроллер → планировщик. Текущий снимок, состояния и доступная память.',
 {'scope':scope,'catalog_ref':catalog,'graph_ref':graph,'task_states':[{'task_id':'T1','status':'in_progress','result_refs':[result]},{'task_id':'T2','status':'pending','result_refs':[]}], 'latest_result_refs':[result],'memory_index_ref':memory,'reflection_requested':True,'budget':budget},
 scope=ref('Scope'),catalog_ref=ref('ArtifactRef'),graph_ref=ref('ArtifactRef'),task_states=arr(ref('TaskState')),latest_result_refs=arr(ref('ArtifactRef')),memory_index_ref=ref('ArtifactRef'),reflection_requested=B,budget=ref('Budget'))
action = {'id':'A1','task_ids':['T1'],'kind':'repair','goal':'Устранить отсутствие посадки','expected_observation':'sapling_placed=true','depends_on_action_ids':[],'condition_ids':['C1']}
contract('PlannerDecision','Планировщик → контроллер. Адаптивный пакет, гипотезы, отложенные задачи.',
 {'scope':scope,'strategy':'repair','rationale':'Место найдено, но посадка не подтверждена. Проверить действие установки.', 'actions':[action], 'hypotheses':[{'id':'H1','statement':'Команда посадки не вызывается','observation_refs':[hit],'experiment':'Записать событие вызова посадки','prediction':'Событие отсутствует при воспроизведении'}], 'deferred':[{'task_id':'T2','reason':'Нужен установленный саженец','return_when':'C1 подтверждён либо T1 реализован перед T2 в общем пакете'}], 'manual_checks':[], 'replacement_graph_ref':None},
 scope=ref('Scope'),strategy=enum('implement','repair','diagnose','reflect','verify','manual','wait'),rationale=S,actions=arr(ref('Action')),hypotheses=arr(ref('Hypothesis')),deferred=arr(ref('DeferredTask')),manual_checks=arr(ref('ManualCheck')),replacement_graph_ref={'anyOf':[ref('ArtifactRef'),{'type':'null'}]})
contract('ValidationRequest','Внутренний вызов контроллера. Проверяемый JSON и доверенная схема.',
 {'scope':scope,'payload_ref':plan,'schema_name':'PlannerDecision','catalog_ref':catalog,'graph_ref':graph},
 scope=ref('Scope'),payload_ref=ref('ArtifactRef'),schema_name={'type':'string','enum':['RequirementsCatalog','TaskGraph','PlannerDecision','CoderReport','ScenarioManifest','ReviewResult','ReviewResponse']},catalog_ref={'anyOf':[ref('ArtifactRef'),{'type':'null'}]},graph_ref={'anyOf':[ref('ArtifactRef'),{'type':'null'}]})
contract('ValidationResult','Контроллер → автор сообщения. Точная ошибка и материал для коррекции.',
 {'scope':scope,'submission_ref':plan,'valid':False,'checks_passed':['json_syntax','known_task_ids'],'issues':[{'path':'$.actions[0].task_ids','code':'type','expected':'array<string>','received_type':'string','example_json':'["T1"]','instruction':'Передай массив строк. Не перечитывай проект ради этой правки.'}], 'schema_ref':artifact('contracts/PlannerDecision.schema.json'),'example_ref':artifact('contracts/examples/PlannerDecision.json')},
 scope=ref('Scope'),submission_ref=ref('ArtifactRef'),valid=B,checks_passed=arr(S),issues=arr(ref('Issue')),schema_ref=ref('ArtifactRef'),example_ref=ref('ArtifactRef'))
contract('CoderAssignment','Контроллер → кодер. Принятый план и границы рабочей области.',
 {'scope':scope,'plan_ref':plan,'catalog_ref':catalog,'graph_ref':graph,'memory_index_ref':memory,'writable_roots':['workspace/'],'instructions':['Реализуй A1. Добавь измеримый сценарий. Не объявляй PASS.']},
 scope=ref('Scope'),plan_ref=ref('ArtifactRef'),catalog_ref=ref('ArtifactRef'),graph_ref=ref('ArtifactRef'),memory_index_ref=ref('ArtifactRef'),writable_roots=arr(S),instructions=arr(S))
contract('CoderReport','Кодер → контроллер. Отчёт о правках; это утверждение агента, ещё не PASS.',
 {'scope':post_scope,'base_source_revision':scope['source_revision'],'plan_ref':plan,'action_ids':['A1'],'summary':'Добавлен вызов посадки','changed_files':[artifact('src/main/java/PlantTask.java','text/x-java-source','e')], 'scenario_manifest_ref':manifest,'review_response_refs':[artifact('rounds/3/review-response.json')],'unverified_claims':['Посадка должна завершаться'],'open_issues':[]},
 scope=ref('Scope'),base_source_revision=ref('Hash'),plan_ref=ref('ArtifactRef'),action_ids=arr(S),summary=S,changed_files=arr(ref('ArtifactRef')),scenario_manifest_ref=ref('ArtifactRef'),review_response_refs=arr(ref('ArtifactRef')),unverified_claims=arr(S),open_issues=arr(S))
contract('ScenarioManifest','Кодер → ревьюер → исполнитель. Исполняемые тесты плюс явные утверждения.',
 {'scope':post_scope,'scenarios':[scenario]},scope=ref('Scope'),scenarios=arr(ref('Scenario')))
contract('ReviewRequest','Контроллер → ревьюер. Независимое чтение кода, условий и тестов.',
 {'scope':review_scope,'subject':'code','context_ref':artifact('rounds/3/assignment.json'),'catalog_ref':catalog,'subject_refs':[artifact('rounds/3/coder-initial.json'),artifact('rounds/3/scenarios-initial.json')],'diff_ref':artifact('rounds/3/changes-initial.diff','text/plain'),'memory_index_ref':memory,'focus':['Переход от выбора места к посадке','Тест действительно проверяет блок в мире']},
 scope=ref('Scope'),subject=enum('decomposition','plan','code','scenarios'),context_ref=ref('ArtifactRef'),catalog_ref={'anyOf':[ref('ArtifactRef'),{'type':'null'}]},subject_refs=arr(ref('ArtifactRef')),diff_ref={'anyOf':[ref('ArtifactRef'),{'type':'null'}]},memory_index_ref=ref('ArtifactRef'),focus=arr(S))
code_ref = {'artifact':artifact('src/main/java/PlantTask.java','text/x-java-source','d'),'start_line':40,'end_line':40,'excerpt':'if (site != null) { moveTo(site); }'}
fixed_ref = {'artifact':artifact('src/main/java/PlantTask.java','text/x-java-source','e'),'start_line':41,'end_line':41,'excerpt':'if (arrivedAt(site)) { plantSapling(site); }'}
contract('ReviewResult','Ревьюер → контроллер и кодер. Вердикт о ревью, не о прохождении игры.',
 {'scope':review_scope,'request_ref':artifact('rounds/3/review-request.json'),'disposition':'changes_requested','findings':[{'id':'F1','kind':'requirement_violation','severity':'high','requirement_ids':['R1'],'code_refs':[code_ref],'observation':'После движения нет действия посадки','consequence':'C1 может не выполняться','suggested_check':'Проверить появление саженца в мире'}],'unknowns':['Поведение игры ещё не проверено']},
 scope=ref('Scope'),request_ref=ref('ArtifactRef'),disposition=enum('changes_requested','ready_for_verification','needs_experiment'),findings=arr(ref('Finding')),unknowns=arr(S))
contract('ReviewResponse','Кодер → ревьюер. Ответ на каждое замечание с подтверждением.',
 {'scope':post_scope,'review_ref':artifact('rounds/3/review.json'),'responses':[{'finding_id':'F1','disposition':'fixed','explanation':'После прибытия вызывается посадка. Независимый запуск ещё не выполнен.','evidence_refs':[fixed_ref]}]},
 scope=ref('Scope'),review_ref=ref('ArtifactRef'),responses=arr(ref('FindingResponse')))
contract('VerificationRequest','Контроллер → независимый исполнитель. Снимок кода и выбранные проверки.',
 {'scope':post_scope,'source_snapshot_ref':artifact('snapshots/source-e.tar','application/x-tar','e'),'scenario_manifest_ref':manifest,'scenario_ids':['S1'],'regression_scenario_ids':[],'run_kind':'targeted','runner_profile':'minecraft-client-gametest'},
 scope=ref('Scope'),source_snapshot_ref=ref('ArtifactRef'),scenario_manifest_ref=ref('ArtifactRef'),scenario_ids=arr(S),regression_scenario_ids=arr(S),run_kind=enum('targeted','regression','full'),runner_profile=S)
observations = [{'checkpoint_id':'CP1','status':'PASS','actual':'site_found=true','evidence_refs':[{'artifact':log,'start_line':17,'end_line':17,'excerpt':'site_found=true'}]}, {'checkpoint_id':'CP2','status':'FAIL','actual':'sapling_placed=false','evidence_refs':[hit]}, {'checkpoint_id':'CP3','status':'NOT_EXECUTED','actual':'До выращивания выполнение не дошло','evidence_refs':[]}]
contract('VerificationResult','Исполнитель → контроллер, планировщик и память. Наблюдения конкретного снимка.',
 {'scope':previous_scope,'request_ref':artifact('rounds/2/verification-request.json'),'runner_run_id':'RUN-2','build_status':'PASS','scenario_results':[{'scenario_id':'S1','status':'FAIL','checkpoints':observations,'media_refs':[artifact('rounds/2/S1.mp4','video/mp4')]}], 'infrastructure_errors':[], 'raw_report_refs':[artifact('rounds/2/junit.xml','application/xml'),log]},
 scope=ref('Scope'),request_ref=ref('ArtifactRef'),runner_run_id=S,build_status=enum('PASS','FAIL','NOT_EXECUTED','ERROR'),scenario_results=arr(ref('ScenarioResult')),infrastructure_errors=arr(S),raw_report_refs=arr(ref('ArtifactRef')))
contract('MemoryWrite','Контроллер → архив. Публичные оригиналы и связи; агенты не назначают успех.',
 {'scope':previous_scope,'kind':'verification','producer':'runner','payload_ref':result,'task_ids':['T1'],'requirement_ids':['R1'],'tags':['verified_fail'],'links':[{'relation':'verifies','record_id':'M-plan-2'}]},
 scope=ref('Scope'),kind=enum('spec','catalog','graph','plan','coder','review','verification','reflection','validation','usage','manual'),producer=enum('user','controller','planner','coder','reviewer','runner','gateway'),payload_ref=ref('ArtifactRef'),task_ids=arr(S),requirement_ids=arr(S),tags=arr(enum('proposed','verified_pass','verified_fail','untested','blocked','superseded')),links=arr(ref('MemoryLink')))
contract('MemoryRecord','Архив → читатели. Неизменяемая запись, время, исходник и связи.',
 {'record_id':'M-result-3','created_at':'2026-10-26T12:00:00Z','entry':EX['MemoryWrite'],'index_ref':memory},
 record_id=S,created_at={'type':'string','format':'date-time'},entry=ref('MemoryWrite'),index_ref=ref('ArtifactRef'))
contract('MemoryQuery','Агент → инструмент чтения. Поиск записей или чтение полного фрагмента.',
 {'project_id':'demo-trees','task_ids':['T1'],'record_ids':[],'query':'ошибка посадки','cursor':None},
 project_id=S,task_ids=arr(S),record_ids=arr(S),query={'type':'string'},cursor={'type':['string','null']})
contract('MemoryResult','Индекс → агент. Краткие результаты и ссылки на полные записи. Есть продолжение.',
 {'records':[{'record_id':'M-result-3','summary':'Место найдено; посадка не выполнена','full_record_ref':artifact('memory/records/M-result-3.json')}],'next_cursor':None,'omitted_records':0},
 records=arr(obj(record_id=S,summary=S,full_record_ref=ref('ArtifactRef'))),next_cursor={'type':['string','null']},omitted_records=I)
contract('GatewayRequest','Любая LLM-роль → шлюз. Нормализованный запрос, без ключей API.',
 {'request_id':'Q-3','scope':scope,'role_config':roles[0],'messages':[{'role':'user','content':'Выбери следующий пакет по PlannerInput.'}], 'attachment_refs':[artifact('rounds/3/planner-input.json')],'tools':[{'name':'read_file','description':'Читает разрешённый файл','input_schema':artifact('tools/read_file.schema.json')}],'output_contract':'PlannerDecision','budget':budget},
 request_id=S,scope=ref('Scope'),role_config=ref('RoleConfig'),messages=arr(ref('Message')),attachment_refs=arr(ref('ArtifactRef')),tools=arr(ref('ToolDefinition')),output_contract=S,budget=ref('Budget'))
contract('GatewayResult','Шлюз → контроллер. Публичный ответ, расход и фактическая модель.',
 {'request_id':'Q-3','outcome':'completed','actual_role_config':roles[0],'model_changed':False,'model_change_reason':None,'output_ref':plan,'public_transcript_ref':artifact('rounds/3/planner-public.jsonl','application/x-ndjson'),'usage':{'input_tokens':12000,'output_tokens':800,'cache_read_tokens':9000,'cache_write_tokens':0,'measurement':'provider'},'error':None},
 request_id=S,outcome=enum('completed','incomplete','error'),actual_role_config=ref('RoleConfig'),model_changed=B,model_change_reason={'type':['string','null']},output_ref={'anyOf':[ref('ArtifactRef'),{'type':'null'}]},public_transcript_ref=ref('ArtifactRef'),usage=ref('Usage'),error={'anyOf':[obj(code=S,message=S,retryable=B),{'type':'null'}]})
contract('ProjectStatus','Контроллер → панель. Фактический этап и состояние условий, не самооценка модели.',
 {'scope':scope,'stage':'planning','active_task_ids':['T1'],'coverage':[{'condition_id':'C1','status':'failed','result_refs':[result]},{'condition_id':'C2','status':'untested','result_refs':[]}], 'manual_pending':[], 'ready':False,'readiness_reasons':['C1 не пройден','C2 не проверен'],'latest_memory_ref':artifact('memory/records/M-result-3.json')},
 scope=ref('Scope'),stage=enum('ingest','decompose','planning','coding','review','verification','reflection','waiting','paused','done'),active_task_ids=arr(S),coverage=arr(ref('Coverage')),manual_pending=arr(ref('ManualCheck')),ready=B,readiness_reasons=arr(S),latest_memory_ref=ref('ArtifactRef'))

FIELD_HELP = {
 'contract':'Имя типа сообщения; выбирает схему.', 'version':'Версия формата; здесь только proposal-v1.',
 'scope':'Проект, версия ТЗ, версия кода и раунд.', 'project_id':'Устойчивый ID проекта.',
 'spec_revision':'SHA-256 канонического манифеста ТЗ и явных уточнений.', 'source_revision':'SHA-256 канонического манифеста исходников снимка.',
 'base_source_revision':'Версия исходников до правок кодера.', 'round':'Номер попытки; не процент готовности.',
 'path':'Для ArtifactRef: путь относительно корня проекта; для Issue: путь поля в JSON.',
 'sha256':'Хеш точного содержимого файла; проверяется при чтении.', 'media_type':'MIME-тип файла.',
 'start_line':'Первая строка фрагмента, с 1.', 'end_line':'Последняя строка фрагмента включительно.', 'excerpt':'Точная цитата из указанной версии файла.',
 'artifact':'Файл, из которого взята цитата.', 'spec_file':'Сохранённый исходный Markdown.', 'clarifications':'Явные уточнения пользователя; отдельные файлы.',
 'roles':'Настройки моделей по ролям.', 'budget':'Оставшийся пользовательский бюджет на момент запроса.',
 'role':'Роль автора сообщения модели.', 'provider':'Выбранный API-провайдер.', 'model':'Точное имя модели у провайдера.',
 'reasoning':'Значение из возможностей выбранной модели; default означает штатное.',
 'remaining_tokens':'Остаток доступных токенов; пример не задаёт лимит проекта.', 'remaining_seconds':'Остаток разрешённого времени.',
 'previous_catalog_refs':'Предыдущие каталоги при изменении ТЗ.', 'instructions':'Явные инструкции этапа.',
 'requirements':'Смысловые требования из ТЗ.', 'conditions':'Наблюдаемые условия выполнения требований.',
 'open_questions':'Неясности, требующие уточнения; нельзя выдумывать ответ.',
 'unclassified_source_refs':'Фрагменты ТЗ, смысл которых ещё не классифицирован.',
 'id':'Уникальный ID внутри соответствующего каталога.', 'statement':'Утверждение или проверяемая гипотеза.',
 'source_refs':'Фрагменты исходного ТЗ, из которых выведено требование.',
 'requirement_id':'ID родительского требования.', 'requirement_ids':'Связанные требования; ID должны существовать.',
 'condition_ids':'Проверяемые условия; ID должны существовать.',
 'expected':'Ожидаемый результат; в Checkpoint это сравниваемое значение.',
 'verification':'Способ проверки: автоматический, ручной или смешанный.',
 'catalog_ref':'Принятый каталог требований; null допустим при начальной проверке.', 'graph_ref':'Принятый граф задач; null допустим при начальной проверке.',
 'tasks':'Компактные функциональные задачи; число не фиксировано.', 'title':'Краткое имя функции для пользователя.',
 'depends_on':'Задачи-предпосылки; связи должны образовать DAG.', 'outcome':'Для Task: результат функции; для шлюза: завершение или ошибка.',
 'verification_approach':'Как наблюдать результат; затем превращается в исполняемый тест.',
 'task_states':'Состояния задач, полученные контроллером из результатов.', 'task_id':'ID задачи.',
 'status':'Наблюдаемый статус; допустимые значения заданы enum.', 'result_refs':'Доказательства статуса, а не текстовая самооценка агента.',
 'latest_result_refs':'Свежие отчёты для выбора следующей работы.', 'memory_index_ref':'Индекс полного архива; записи читаются по необходимости.',
 'reflection_requested':'Сигнал о необходимости пересмотреть подход; не команда остановиться.',
 'strategy':'Выбранный режим следующей работы.', 'rationale':'Краткое основание выбора пакета.',
 'actions':'Адаптивный пакет работ, без фиксированного числа действий.', 'task_ids':'Задачи, затронутые сообщением.',
 'kind':'В Action: вид работы; в Finding: класс замечания; в памяти: тип записи.', 'goal':'Что изменить или выяснить.',
 'expected_observation':'Как отличить полезный результат от повторения попытки.',
 'depends_on_action_ids':'Порядок зависимых действий внутри пакета.',
 'hypotheses':'Предположения, отделённые от установленных фактов.', 'observation_refs':'Реальные наблюдения, на которых основана гипотеза.',
 'experiment':'Проверка, способная подтвердить или опровергнуть гипотезу.', 'prediction':'Результат, ожидаемый при верной гипотезе.',
 'deferred':'Задачи, перенесённые с объяснением.', 'reason':'Причина переноса.', 'return_when':'Условия возвращения; не безусловный повтор.',
 'manual_checks':'Ручные проверки с инструкцией; не дают автоматического PASS.', 'steps':'Последовательность действий.',
 'replacement_graph_ref':'Полный новый TaskGraph; null, если граф не меняется.',
 'payload_ref':'Оригинальное сообщение в файле; сохраняется без смысловой правки.',
 'schema_name':'Схема из доверенного реестра.', 'submission_ref':'Точная версия проверенного сообщения.',
 'valid':'Результат формальных проверок; не означает смысловую правильность.',
 'checks_passed':'Имена успешно выполненных формальных проверок.', 'issues':'Точные ошибки формата и связей.',
 'code':'Код ошибки для обработки программой.', 'received_type':'Фактически полученный тип поля.',
 'example_json':'Готовый пример значения, представленный строкой JSON.', 'instruction':'Как исправить конкретное нарушение.',
 'schema_ref':'Доверенная схема для исправления.', 'example_ref':'Соответствующий схеме пример.',
 'plan_ref':'Принятый план раунда.', 'writable_roots':'Разрешённые области записи; физически задаются исполнителем.',
 'action_ids':'Действия принятого плана, по которым кодер отчитывается.', 'summary':'Краткое описание записи или изменения.',
 'changed_files':'Версии изменённых файлов; контроллер сверяет с реальным diff.',
 'scenario_manifest_ref':'Версия описания сценариев для данного снимка.',
 'review_response_refs':'Полные ответы кодера на ревью.', 'unverified_claims':'Заявления кодера, которые ещё надо подтвердить.',
 'open_issues':'Известные нерешённые вопросы.', 'scenarios':'Сценарии и их измеримые контрольные точки.',
 'setup':'Исходные условия воспроизводимого сценария.', 'checkpoints':'Контрольные точки и проверяемые утверждения.',
 'assertion':'Какое свойство проверяется.', 'observable':'Имя измеряемого состояния или события.',
 'comparator':'Операция сравнения: eq равно, ne не равно; lt/le/gt/ge; contains; event.',
 'test_file':'Исполняемый тест. Текстовый сценарий сам не запускается.',
 'runner_profile':'Имя доверенного профиля; не произвольная shell-команда модели.',
 'media':'Какие медиа нужны сценарию; пустой массив разрешён.',
 'subject':'Что рассматривает ревьюер: разбиение, план, код или тесты.',
 'context_ref':'Вход рассматриваемого этапа: DecompositionInput, PlannerInput или CoderAssignment.',
 'subject_refs':'Документы и файлы, которые нужно проверить.', 'diff_ref':'Изменения относительно базы; null для этапа без diff.',
 'focus':'Риски и вопросы, на которых сосредоточить обзор.',
 'request_ref':'Исходный запрос этого результата.', 'disposition':'Вердикт ревью или ответ кодера; не статус теста.',
 'findings':'Замечания с местом, причиной и способом проверки.', 'severity':'Риск замечания, не новое требование продукта.',
 'code_refs':'Точные места в исходниках.', 'observation':'Что ревьюер наблюдает в коде.', 'consequence':'Почему это может нарушить задачу.',
 'suggested_check':'Как проверить предполагаемую проблему.', 'unknowns':'Что обзор не подтвердил.',
 'review_ref':'Ревью, на которое отвечает кодер.', 'responses':'Ответ на каждое замечание.',
 'finding_id':'ID замечания ревьюера.', 'explanation':'Обоснование исправления, отклонения или неизвестности.',
 'evidence_refs':'Цитаты из файлов; доверие к выводу проверяется отдельно.',
 'source_snapshot_ref':'Архив снимка исходников; состав сверяется с source_revision.',
 'scenario_ids':'Целевые сценарии.', 'regression_scenario_ids':'Проверки ранее работавших затронутых функций.',
 'run_kind':'Целевой, регрессионный или полный запуск.', 'runner_run_id':'ID независимого запуска, назначенный исполнителем.',
 'build_status':'Сборка: PASS, FAIL, не выполнялась либо ошибка среды.',
 'scenario_results':'Независимые результаты сценариев.', 'scenario_id':'ID сценария.',
 'checkpoint_id':'ID измеряемой точки.', 'actual':'Фактическое наблюдение либо причина отсутствия выполнения.',
 'media_refs':'Файлы видео и изображений; не замена измерениям.',
 'infrastructure_errors':'Ошибки среды, отдельно от неверного поведения мода.', 'raw_report_refs':'Оригинальные отчёты и журналы.',
 'producer':'Источник записи; подтверждается каналом, не принимается на веру.',
 'tags':'Теги, назначаемые контроллером по доказательствам.', 'links':'Связи с предыдущими записями.',
 'relation':'Тип связи в истории.', 'record_id':'Устойчивый ID записи памяти.',
 'created_at':'Время сохранения в UTC, ISO 8601.', 'entry':'Полная запись MemoryWrite.', 'index_ref':'Версия индекса архива.',
 'record_ids':'Явно запрошенные записи.', 'query':'Поисковая строка; может быть пустой.',
 'cursor':'Позиция продолжения; null для первой страницы.', 'records':'Найденные записи с кратким описанием.',
 'full_record_ref':'Ссылка на полный MemoryRecord без сокращения.',
 'next_cursor':'Продолжение выдачи; null означает последнюю страницу.', 'omitted_records':'Записи вне этой страницы; остаются в архиве.',
 'request_id':'ID запроса модели.', 'role_config':'Запрошенные роль, провайдер, модель и reasoning.',
 'messages':'Публичная текстовая часть диалога.', 'content':'Текст сообщения; специальные поля провайдера хранит адаптер.',
 'attachment_refs':'Дополнительные файлы, которые адаптер читает или даёт прочитать инструментом.',
 'tools':'Разрешённые инструменты; права задаёт контроллер.', 'name':'Имя инструмента.', 'description':'Назначение инструмента.',
 'input_schema':'Файл схемы аргументов инструмента.', 'output_contract':'Имя ожидаемого типа результата LLM.',
 'actual_role_config':'Фактические роль, провайдер и модель.', 'model_changed':'Была ли замена выбранной модели.',
 'model_change_reason':'Причина явной замены; null, если её не было.', 'output_ref':'Публичный ответ; null, если результата нет.',
 'public_transcript_ref':'Публичный журнал запросов и инструментов, без секретов.',
 'usage':'Нормализованный расход; кэш входит в input_tokens.', 'input_tokens':'Весь вход, включая чтение и запись кэша.',
 'output_tokens':'Выход модели.', 'cache_read_tokens':'Часть входа, прочитанная из кэша.', 'cache_write_tokens':'Часть входа, записанная в кэш.',
 'measurement':'provider: измерено; estimated: оценено; unavailable: измерения нет.',
 'error':'Структурированная ошибка, либо null.', 'message':'Сообщение ошибки.', 'retryable':'Имеет ли смысл повтор при изменении условий или после ожидания.',
 'stage':'Текущий этап контроллера.', 'active_task_ids':'Задачи активного пакета.',
 'coverage':'Статусы условий для текущей версии кода.', 'condition_id':'ID проверяемого условия.',
 'manual_pending':'Ручные пункты с инструкциями; всё ещё не подтверждены.',
 'ready':'Есть ли доказательства готовности; вычисляет контроллер.',
 'readiness_reasons':'Почему проект ещё не признан готовым.', 'latest_memory_ref':'Последняя полная запись истории.'
}
def describe_fields(node):
    if isinstance(node, dict):
        for name, field in node.get('properties', {}).items():
            if name in FIELD_HELP: field['description'] = FIELD_HELP[name]
        for value in node.values(): describe_fields(value)
    elif isinstance(node, list):
        for value in node: describe_fields(value)
for definition in D.values(): describe_fields(definition)

SCHEMA = {'$schema':'https://json-schema.org/draft/2020-12/schema', '$id':'urn:codex-lab:pipeline:proposal-v1', 'title':'Предлагаемые контракты новой архитектуры — не runtime API', '$defs':D, 'oneOf':[ref(n) for n in EX]}
def needed_definitions(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        if '$ref' in node:
            name = node['$ref'].rsplit('/', 1)[1]
            if name not in found:
                found.add(name)
                needed_definitions(D[name], found)
        for value in node.values(): needed_definitions(value, found)
    elif isinstance(node, list):
        for value in node: needed_definitions(value, found)
    return found

for name in EX:
    # Each standalone file is self-contained: local refs remain resolvable offline.
    names = needed_definitions(ref(name))
    standalone = {'$schema':SCHEMA['$schema'], 'title':name+' — proposal-v1', '$ref':'#/$defs/'+name, '$defs':{n:D[n] for n in sorted(names)}}
    target = OUT / 'contracts' / (name+'.schema.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(standalone, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    sample = OUT / 'contracts' / 'examples' / (name+'.json')
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(json.dumps(EX[name], ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
(OUT/'contracts.schema.json').write_text(json.dumps(SCHEMA,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(OUT/'examples.json').write_text(json.dumps(EX,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

BLOCKS = [
 ('input','01','Приём ТЗ','SCRIPT','Сохранить исходник. Вычислить версии. Не менять смысл.', ['ProjectInput'],['ProjectSnapshot','DecompositionInput'],'Markdown остаётся файлом. Уточнения хранятся отдельно. SHA-256 связывает дальнейшие результаты с точной версией.','Человек загружает: «Посадить саженец. Вырастить дерево костной мукой».','Есть приём файла. Новые унифицированные контракты предложены.'),
 ('decompose','02','Задачи и зависимости','LLM','Выделить функции. Связать их с исходным текстом.', ['DecompositionInput'],['RequirementsCatalog','TaskGraph'],'Это режим планировщика, а не обязательная четвёртая модель. Он читает ТЗ, выделяет проверяемые условия и компактные задачи. Ревьюер проверяет смысл разбиения. Неясные места остаются вопросами.','T1: посадка → T2: выращивание. Один класс Java не становится отдельной функцией.','Сейчас группировка зависит от Markdown. Глобальный LLM-граф ещё нужно внедрить.'),
 ('planner','03','Следующий пакет','LLM','Выбрать полезную работу по графу, ошибкам и памяти.', ['PlannerInput','MemoryResult','VerificationResult'],['PlannerDecision'],'Размер пакета выбирает планировщик. Можно объединять связанные задачи и реализовывать зависимость первой внутри пакета. Нет квоты «одна новая функция» или «ровно четыре задачи». Ожидаемый результат должен быть наблюдаемым.','Посадка сломана: проверить H1, исправить T1. T2 отложить с явным условием возврата.','Планировщик есть. Свободный выбор по всему графу и адаптивный пакет ещё нужно внедрить.'),
 ('controller','04','Контроллер','SCRIPT','Проверить контракт и состояние. Выполнить допустимое назначение.', ['ValidationRequest','PlannerDecision','GatewayResult','VerificationResult'],['ValidationResult','CoderAssignment','ReviewRequest','VerificationRequest','MemoryWrite','ProjectStatus'],'JSON Schema проверяет поля. Таблицы ID проверяют ссылки. Топологическая сортировка проверяет граф. Хеши проверяют версии. Политика прав ограничивает доступ. Контроллер не доказывает смысл текста.','task_ids="T1" → ошибка типа; task_ids=["T1"] → правильная форма. Достаточность задачи оценивает LLM.','Контроллер и схема текущего планировщика есть. Показанные здесь сообщения — новый проект контрактов.'),
 ('coder','05','Кодер','LLM','Реализовать назначение. Написать исполняемые сценарии.', ['CoderAssignment','ReviewResult','MemoryResult'],['CoderReport','ScenarioManifest','ReviewResponse'],'Кодер пишет файлы только в разрешённой области. JSON хранит отчёт и ссылки, исходники остаются файлами. Его запуск теста — диагностика; итоговый статус приходит от независимого исполнителя.','Добавить действие посадки после прибытия. В тесте проверить реальный блок мира.','Кодер и тесты есть. Единые отчёты и управляемый обмен с ревьюером ещё нужно внедрить.'),
 ('reviewer','06','Ревьюер','LLM · READ ONLY','Проверить смысл кода и способность тестов находить ошибки.', ['ReviewRequest','ReviewResponse'],['ReviewResult'],'Ревьюер сам читает нужные файлы. Отличает нарушение ТЗ, технический риск и необязательное улучшение. Только кодер меняет код. Несогласие требует обоснования или эксперимента; согласие не означает PASS.','«Код доходит до места, но не вызывает посадку». Ссылка на строки → замечание F1 → ответ кодера.','Делегированный обзор доступен. Постоянного управляемого цикла ревью пока нет.'),
 ('runner','07','Сборка и игровые тесты','SCRIPT','Запустить точный снимок. Измерить контрольные точки.', ['VerificationRequest','ScenarioManifest'],['VerificationResult'],'Исполнитель запускает разрешённый профиль тестирования в изоляции. Сохраняет JUnit, журнал, скриншоты и видео. ERROR среды отличается от FAIL поведения. Видео дополняет измерения; оно не устанавливает успех.','Площадка: PASS. Посадка: FAIL. Выращивание: NOT_EXECUTED. Причина отсутствия посадки пока неизвестна.','Независимые тесты и медиа есть. Детальные контрольные точки каждого сложного сценария ещё нужно расширить.'),
 ('reflection','08','Рефлексия','LLM','При повторе изменить эксперимент или выбрать независимую работу.', ['PlannerInput','VerificationResult','MemoryResult'],['PlannerDecision'],'Это режим планировщика. Он отделяет факт от гипотезы, сравнивает попытки и меняет действие. Возврат к отложенной задаче зависит от новых условий. Если вся работа реально заблокирована, сохранить состояние и указать внешнюю причину.','Не повторять «попробовать посадить». Измерить вызов API; затем проверить новую гипотезу.','Рефлексия есть частично. Выбор независимой работы ограничен текущей группой.'),
 ('memory','09','Память и поиск','SCRIPT','Хранить полные публичные записи. Возвращать нужные фрагменты.', ['MemoryWrite','MemoryQuery'],['MemoryRecord','MemoryResult'],'Архив хранит оригинал, ревизию, происхождение и связи. Контекст содержит краткий индекс и ссылки. Пагинация не удаляет архив. Старый PASS остаётся историей после изменения кода. Секреты и приватные поля провайдера в публичный архив не входят.','Ошибка → H1 → эксперимент → правка → результат. У каждой записи есть ID и ссылка на полный JSON.','Архив ошибок и заметок есть. Полный каталог планов и обсуждений с этими контрактами ещё нужно внедрить.'),
 ('gateway','10','Шлюз моделей','SCRIPT','Адаптировать провайдера. Учитывать запросы, модели и токены.', ['GatewayRequest'],['GatewayResult'],'Роли выбирают провайдера и модель отдельно. Шлюз преобразует сообщения и инструменты в API провайдера. Ответ LLM всё равно проверяется. Ключи берутся из окружения и не входят в JSON. Любая смена модели должна быть явно отражена.','Планировщик и кодер могут использовать разные модели. В расходе записывается фактическая роль и модель.','Провайдеры и учёт работают. Нормализованные контракты этой страницы пока проектные.'),
 ('ui','11','Панель и готовность','SCRIPT','Показать этап, условия, доказательства и ручные проверки.', ['ProjectStatus','MemoryResult','GatewayResult'],['ProjectInput','MemoryQuery'],'Готовность требует актуальных доказательств всех обязательных условий и полной финальной проверки. Ручной пункт можно отложить с инструкцией; его нельзя автоматически назвать пройденным. Номер раунда не определяет готовность.','C1: failed; C2: untested → ready=false. Открыть результат или полную запись памяти.','Панель существует. Эта страница — отдельное объяснение новой архитектуры.')
]
data = {'contracts':EX,'schemas':{n:D[n] for n in EX},'defs':D,'descriptions':DESCRIPTIONS,'blocks':[dict(zip(('id','number','title','kind','purpose','inputs','outputs','mechanism','example','state'),b)) for b in BLOCKS]}
template = (ROOT/'audit'/'pipeline_explainer.template.html').read_text(encoding='utf-8')
assert template.count('/*__DATA__*/') == 1
html = template.replace('/*__DATA__*/',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
(OUT/'index.html').write_text(html,encoding='utf-8')
print('Built docs/pipeline/index.html; contracts:', len(EX), '; definitions:',len(D))
