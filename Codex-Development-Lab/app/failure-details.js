/* Named verifier facts. All source messages are rendered as text, never HTML. */
(() => {
 'use strict';
 const make=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);if(cls)node.className=cls;return node};
 window.renderFailureDetails=(parent,value)=>{
  const key=JSON.stringify(value||null);if(parent.dataset.failureFingerprint===key)return;
  const expanded=new Set([...parent.querySelectorAll('details[open]')].map(n=>n.dataset.checkStatus));
  parent.dataset.failureFingerprint=key;parent.replaceChildren();if(!value)return;
  if(value.historical)parent.append(make('p','Последний неуспешный запуск · раунд '+value.source_round+'. Исторические данные; текущий статус проверки не изменён.','media-warning'));
  const counts=value.counts||{};
  parent.append(make('h4','Что подтверждено и что осталось проверить'),make('p',
   'Пройдено: '+(counts.passed||0)+' · Ошибки: '+(counts.failed||0)+' · Без результата: '+(counts.not_executed||0)+' · Пропущено: '+(counts.skipped||0)+' · Нет данных: '+(counts.unknown||0)));
  const groups=[['FAIL','Ошибка проверки','acceptance-fail'],['NOT_EXECUTED','Нет результата в отчёте','acceptance-gap'],
   ['SKIPPED','Пропущено тестом','acceptance-gap'],['UNKNOWN','Недостаточно данных','muted'],['TEST_PASSED','Подтверждено проверками','acceptance-pass']];
  for(const [status,label,cls] of groups){
   const rows=(value.checks||[]).filter(c=>c.status===status);if(!rows.length)continue;
   const group=make('details');group.dataset.checkStatus=status;group.open=expanded.has(status)||status==='FAIL'||status==='NOT_EXECUTED';
   group.append(make('summary',label+' · '+rows.length,cls));
   for(const check of rows){const row=make('div');row.append(make('p',check.name),make('p',check.reason,'muted'),make('p',check.classname,'foot'));group.append(row)}
   parent.append(group);
  }
  if(value.additional_failures?.length){const extra=make('details');extra.append(make('summary','Дополнительные ошибки запуска · '+value.additional_failures.length));for(const row of value.additional_failures)extra.append(make('p',row.name+': '+row.reason));parent.append(extra)}
  if(value.evidence_errors?.length)parent.append(make('p','Часть файлов доказательств недоступна или не прошла проверку.','media-warning'));
  if(value.truncated)parent.append(make('p','Список ограничен; полный отчёт доступен в журнале.','media-warning'));
  parent.append(make('p',value.scope_note||'Результаты отдельных проверок не подтверждают выполнение всего пункта ТЗ. Причина ошибки требует диагностики.','foot'));
 };
})();
