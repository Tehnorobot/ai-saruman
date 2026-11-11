"use strict";
const providerNames = {codex:"Codex / ChatGPT",minimax:"MiniMax / M Plan",litellm:"LiteLLM",dslab:"DSLab / Claude"};
function chatProfile(){return state.data.models.profiles.lab_chat;}
function chatReady(){const p=chatProfile();return p.provider==='codex'?state.data.provider.connected:state.data.models.providers[p.provider].configured;}
function roleCard(role) {
  const d=state.data.models, meta=d.roles[role];
  const p=state.roleDrafts[role] || d.profiles[role];
  const options=(values,current)=>values.map(v=>'<option value="'+e(v)+'"'+(v===current?' selected':'')+'>'+e(providerNames[v]||v)+'</option>').join('');
  const status=d.providers[p.provider];
  const fields=[['max_output','Токенов ответа API / резерв Codex',256],['timeout','Тайм-аут запроса, с',5],['max_calls','Запросов за сессию',1],['token_budget','Бюджет токенов за сессию',1000]];
  return panel(e(meta.label),'<form class="panel-body role-form" data-role-form="'+role+'"><p class="tiny">'+e(meta.description)+'</p>'+
    '<div class="role-selectors"><label>Провайдер<select data-role="'+role+'" data-field="provider">'+options(Object.keys(d.catalogue),p.provider)+'</select></label>'+
    '<label>Модель<select data-role="'+role+'" data-field="model">'+options(Object.keys(d.catalogue[p.provider]),p.model)+'</select></label>'+
    '<label>Reasoning<select data-role="'+role+'" data-field="reasoning">'+options(d.catalogue[p.provider][p.model],p.reasoning)+'</select></label></div>'+
    '<p class="tiny">'+e(status.note)+(p.provider==='minimax'?' M3.1 Flash всегда использует thinking; default = max. Для быстрых игровых решений начните с low.':'')+'</p>'+
    '<details><summary>Лимиты запроса и сессии</summary><div class="role-limits">'+fields.map(([key,label,min])=>'<label>'+label+'<input data-role="'+role+'" data-field="'+key+'" type="number" step="1" min="'+min+'" value="'+e(p[key])+'" required></label>').join('')+'</div>'+
    '<p class="tiny">Лимит ответа API включает thinking. Слишком малое значение может обрезать ответ. Codex управляет длиной ответа сам; здесь значение служит резервом бюджета.</p></details>'+
    '<button class="button primary" type="submit"'+(state.savingRole===role?' disabled':'')+'>'+(state.savingRole===role?'Сохраняем…':'Сохранить')+'</button>'+
    '<span class="tiny role-revision">Версия '+e(d.profiles[role].revision)+'</span></form>');
}
function modelSettingsView() {
  const d=state.data.models, p=state.data.provider;
  const main=Object.keys(d.roles).filter(r=>!d.roles[r].optional).map(roleCard).join('');
  const extra=Object.keys(d.roles).filter(r=>d.roles[r].optional).map(roleCard).join('');
  const history=d.history.map(r=>'<tr><td>'+e(date(r.started*1000))+'</td><td>'+e(d.roles[r.role]?.label||r.role)+'</td><td>'+e(providerNames[r.provider]||r.provider)+'<br><span class="mono">'+e(r.model)+'</span></td><td>'+e(r.state)+'</td><td>'+number(r.usage.input_tokens)+'</td><td>'+number(r.usage.output_tokens)+'</td><td>'+number(r.usage.cached_input_tokens)+'</td><td>'+e(r.reason||'—')+'</td></tr>').join('');
  return '<div class="alert">Выберите модель отдельно для каждой задачи. Изменения действуют со следующего запроса. Если эта роль сейчас занята, сохранение попросит дождаться ответа. Скрытой замены провайдера нет.</div>'+
    '<div class="role-grid">'+main+'</div><details id="optional-roles"'+(state.showOptional?' open':'')+'><summary>Старый режим и дополнительные инструменты</summary><p class="tiny">Эти настройки не включают старых агентов. В режиме mods основные задачи выполняют Персонаж и Игровые действия. Моды, диспетчер команд, доставка Telegram и запись памяти работают без отдельных вызовов LLM.</p><div class="role-grid">'+extra+'</div></details>'+
    panel('Ключи и подключение','<div class="panel-body"><p>Ключ MiniMax M Plan: <code>integrations/saruman/.env.saruman</code></p><pre class="note-preview">MINIMAX_API_KEY=ваш_Subscription_Key\nMINIMAX_BASE_URL=https://api.minimax.io/anthropic</pre><p class="tiny">Используйте Subscription Key из Plan Details. Ключи не хранятся в браузере или базе настроек. После изменения .env.saruman перезапустите Lab и игровой runtime.</p><p>Codex: <code>codex login</code>. '+e(p.connected?'Аккаунт подключён.':p.error||'Аккаунт ещё не проверен.')+'</p>'+button(state.connecting?'Проверяем…':'Обновить каталог Codex','connect','','refresh')+'<p class="tiny">Проверка Codex читает аккаунт, модели и лимиты. Для остальных провайдеров показано наличие конфигурации; доступ подтверждается при реальном запросе. Открытие настроек не запускает генерацию.</p></div>')+
    panel('Запросы по задачам',history?'<div class="table-scroll"><table><thead><tr><th>Время</th><th>Задача</th><th>Провайдер / модель</th><th>Статус</th><th>Вход</th><th>Выход</th><th>Кэш</th><th>Причина</th></tr></thead><tbody>'+history+'</tbody></table></div>':'<div class="empty">Запросов пока нет.</div>')+
    panel('Личность · PERSONA.md','<div class="panel-body"><details><summary>Прочитать личность</summary><pre class="note-preview">'+e(state.data.persona)+'</pre></details></div>');
}
