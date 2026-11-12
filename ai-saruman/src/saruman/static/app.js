"use strict";
const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const e = escapeHTML;
const number = (value) => value == null ? "—" : new Intl.NumberFormat("ru-RU").format(value);
const bytes = (value) => value < 1024 ? number(value) + " Б" : (value / 1024).toFixed(1) + " КБ";
const date = (value, timeOnly = false) => value ? new Date(value).toLocaleString("ru-RU", timeOnly ? {hour:"2-digit",minute:"2-digit"} : {day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"}) : "—";
const paths = {
  chat:'M4 4h16v12H9l-5 4V4Z', memory:'M8 3 3 6v6l5 3 5-3V6L8 3Zm8 6 5 3v6l-5 3-5-3v-6',
  history:'M4 7V3m0 4h4M4 7a9 9 0 1 1-1 7m9-8v6l4 2',
  analytics:'M4 20h17M7 16v-5m5 5V5m5 11v-8',
  settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm-8 4H2m20 0h-2M12 4V2m0 20v-2M6.3 6.3 4.9 4.9m14.2 14.2-1.4-1.4m0-11.4 1.4-1.4M4.9 19.1l1.4-1.4',
  plus:'M12 5v14M5 12h14', arrow:'M5 12h14m-6-6 6 6-6 6', refresh:'M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M4 12a8 8 0 0 0 14 6',
  shield:'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Zm-4 9 3 3 5-6',
  leaf:'M20 3C6 3 2 8 5 15c6 5 13 1 15-12ZM4 21l11-11',
  crown:'m3 7 5 4 4-7 4 7 5-4-2 12H5L3 7Z',
  bolt:'m13 2-8 12h6l-1 8 9-13h-7l1-7Z', export:'M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5',
  link:'m9 15 6-6m-7 8-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m-1-2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0'
};
const icon = (name) => '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="' + (paths[name] || paths.chat) + '"/></svg>';
const tower = '<svg viewBox="0 0 90 110" fill="none" aria-hidden="true"><path d="M20 92h50M26 86V42l9-9V18l10-9 10 9v15l9 9v44M35 86V47h20v39M39 41h12M41 57h8m-8 10h8m-8 10h8M26 43l-7-8m45 8 7-8" stroke="currentColor" stroke-width="1.5"/><path d="M38 24h14m-7-8v16M14 98h62" stroke="currentColor"/><circle cx="45" cy="56" r="42" stroke="currentColor" stroke-opacity=".12"/></svg>';
const scenarios = [
  {name:"Почтительное знакомство", icon:"crown", text:"Приветствую вас, мудрый Саруман. Позвольте представиться: меня зовут Алекс. Могу ли я рассчитывать на ваш совет?"},
  {name:"Неуважение и провокация", icon:"bolt", text:"Эй, старик, хватит умничать. Ты вообще на что-нибудь способен?"},
  {name:"Просьба о помощи", icon:"shield", text:"Ситуация для теста: возле деревни появились зомби. Я прошу тебя защитить жителей. Что ты ответишь?"},
  {name:"Спокойный день", icon:"leaf", text:"Ситуация для теста: вокруг тихо, рядом свободное поле и сундук с семенами. Чем ты хочешь заняться?"},
  {name:"Проверка памяти", icon:"memory", text:"Что ты помнишь обо мне из нашего диалога? Отдели известные тебе факты от предположений."},
  {name:"Неизвестные сведения", icon:"chat", text:"Сколько у меня сейчас алмазов в инвентаре? Если не знаешь, не выдумывай."}
];
const views = {control:"Управление",chat:"Тестирование", memory:"Память", history:"Диалоги", analytics:"Аналитика", telegram:"Telegram", settings:"Настройки"};
const state = {data:null, view:location.hash.slice(1) || "chat", session:null, messages:[], draft:"",
  remember:false, busy:false, connecting:false, memoryView:"graph", entity:null, file:null,
  database:"sessions", databaseData:null, model:"", error:null};
state.roleDrafts = {};
state.showOptional = false;
let toastTimer;
state.telegram = null;
state.telegramPage = 0;
state.telegramDraft = "";
state.runtimeBusy = false;
state.minecraftChoice = null;
state.filterChoice = null;
state.admin = null;state.adminDraft='';state.adminBusy=false;state.dialogues=null;state.gameForm=null;
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 7000);
}
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method:"POST",headers:{"Content-Type":"application/json","X-Saruman-Token":state.data.csrf_token},
    body:JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Ошибка запроса");
  return data;
}
function activeSession() { return state.data.sessions.find((item) => item.id === state.session); }
function selectedRun() { return state.data.analytics.runs.find((run) => run.session_id === state.session); }
function stat(label,value,foot,accent=false) {
  return '<div class="stat-card' + (accent ? ' accent' : '') + '"><div class="label">' + e(label) +
    '</div><div class="stat-number">' + e(value) + '</div><div class="stat-foot">' + e(foot) + '</div></div>';
}
function panel(title,body,extra="",className="") {
  return '<section class="panel ' + className + '"><div class="panel-head"><div class="panel-title">' +
    title + '</div>' + extra + '</div>' + body + '</section>';
}
function button(text,action,kind="",symbol="") {
  return '<button class="button ' + kind + '" data-action="' + action + '">' + (symbol ? icon(symbol) : "") + '<span>' + e(text) + '</span></button>';
}
function messageHTML(message) {
  const run = state.data.analytics.runs.find((item) => item.id === message.run_id);
  const assistant = message.role === "assistant";
  const usage = assistant && run ? '<div class="message-usage"><span>' + e(run.model) + '</span><span>' +
    (run.latency_ms == null ? '—' : (run.latency_ms/1000).toFixed(1) + ' с') + '</span><span>' +
    number(run.total_tokens) + ' токенов</span>' +
    (run.remembered ? '<span>+' + run.remembered + ' в память</span>' : '') + '</div>' : '';
  return '<article class="message ' + (assistant ? 'assistant' : 'user') + '"><div class="avatar">' +
    (assistant ? 'S' : 'ВЫ') + '</div><div class="message-main"><div class="message-meta"><strong>' +
    (assistant ? 'Саруман' : 'Вы') + '</strong><time>' + e(date(message.created_at,true)) +
    '</time></div><div class="bubble">' + e(message.content) + '</div>' + usage + '</div></article>';
}
function sidebar() {
  return '<aside class="sidebar"><div class="brand"><div class="brand-mark">' + tower +
    '</div><div><strong>ISENGARD</strong><small>CHARACTER LAB</small></div></div><div class="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div><nav class="nav" aria-label="Окна лаборатории">' +
    Object.entries(views).map(([id,title]) => '<button class="' + (state.view === id ? 'active' : '') + '" data-view="' + id + '" aria-current="' + (state.view === id ? 'page' : 'false') + '">' +
      icon(id) + '<span>' + title + '</span>' + (id === "memory" ? '<span class="count">' + state.data.memory.entities.length + '</span>' : '') + '</button>').join("") +
    '</nav><div class="sidebar-bottom"><div class="side-note"><strong>Характер рождается в диалоге.</strong>Наблюдайте, проверяйте,<br>сохраняйте важное.</div><div class="side-status"><i class="dot ' +
    (state.data.provider.connected ? 'online' : '') + '"></i>' +
    e(providerNames[chatProfile().provider]) +
    '</div><div class="version">LOCAL WORKSPACE / v0.1</div></div></aside>';
}
function heading() {
  const subtitles = {chat:"Проверьте голос, решения и последовательность характера.",memory:"Воспоминания, сущности и связи — без скрытых слоёв.",history:"Каждый эксперимент сохраняется в последовательном диалоге.",analytics:"Измеренный расход и прозрачная оценка контекста.",settings:"Провайдер, модель и reasoning для каждой задачи персонажа."};
  subtitles.telegram='Подтверждённые события, черновики и публикации в канале.';
  subtitles.control='Приватные команды, состояние и подключение к миру.';
  return '<div class="page-heading"><div><div class="eyebrow">САРУМАН / CHARACTER EXPERIMENTS</div><h1>' + views[state.view] +
    '</h1><p class="subtitle">' + subtitles[state.view] + '</p></div>' +
    (state.view === "chat" ? button("Новый диалог","new-session","","plus") : button("Обновить","refresh","refresh","refresh")) + '</div>';
}
function chatView() {
  const memory = state.data.memory, run = selectedRun();
  const messages = state.messages.length ? state.messages.map(messageHTML).join("") :
    '<div class="empty-chat"><div class="sigil">' + tower + '</div><h2>Поговорим, смертный?</h2><p>Начните диалог или выберите сценарий справа.<br>Ответы придут от модели, выбранной для чата Lab.</p><span class="pill">' +
    icon("shield") + 'Тестовая среда · без действий в игре</span></div>';
  const error = state.error ? '<div class="alert error">' + e(state.error) + '</div>' : '';
  const options = state.data.sessions.map((session) => '<option value="' + session.id + '"' +
    (session.id === state.session ? ' selected' : '') + '>' + e(session.title) + '</option>').join("");
  const chat = '<section class="panel chat-panel"><div class="session-toolbar"><span class="dot"></span><select id="session-select" aria-label="Текущий диалог"><option value="">Новый диалог</option>' +
    options + '</select><span class="eyebrow">CHAT / LIVE TEST</span></div><div class="messages" id="messages">' +
    messages + (state.busy ? '<div class="pending"><span class="dot"></span>Саруман готовит ответ…</div>' : '') +
    '</div><form id="chat-form" class="composer"><textarea id="message-input" aria-label="Сообщение персонажу" placeholder="Что вы хотите спросить у Сарумана?" maxlength="12000"' +
    (state.busy ? ' disabled' : '') + '>' + e(state.draft) + '</textarea><div class="composer-bottom"><label class="check-label"><input id="remember" type="checkbox"' +
    (state.remember ? ' checked' : '') + '>Запоминать факты</label><div class="composer-actions"><span class="keyboard-hint">Enter ↵ · Shift + Enter — строка</span><button type="submit" class="button primary"' +
    (state.busy || !chatReady() ? ' disabled' : '') + '>Отправить ' + icon("arrow") +
    '</button></div></div></form><div class="context-note">' + icon("shield") +
    'Память записывается только при включённом переключателе. Сценарии не создают игровых событий.</div></section>';
  const character = '<div class="panel character-card"><div class="eyebrow">АКТИВНЫЙ ПЕРСОНАЖ</div><div class="character-name">Саруман</div><p>Верховный маг. Холодная логика,<br>древняя мудрость и своя воля.</p><div class="tag-row"><span class="tag">Высокомерный</span><span class="tag">Хитрый</span><span class="tag">Нейтральный</span></div></div>';
  const context = panel(icon("memory") + 'Контекст ответа','<div class="panel-body"><div class="mini-stat"><span>Записей о сущностях</span><strong>' + memory.entities.length +
    '</strong></div><div class="mini-stat"><span>Память на диске</span><strong>' + bytes(memory.bytes) +
    '</strong></div><div class="mini-stat"><span>Память во входе, ≈</span><strong>' + number(run ? run.memory_input_est : memory.tokens_estimate) +
    ' ток.</strong></div><div class="meter violet"><span style="width:' +
    Math.min(100,memory.state_bytes / memory.state_guideline_bytes * 100) + '%"></span></div><div class="tiny">state.md: ' +
    bytes(memory.state_bytes) + ' / 16 КБ · рекомендация по сжатию, не лимит модели</div></div>');
  const runInfo = panel(icon("analytics") + 'Последний ответ','<div class="panel-body"><div class="mini-stat"><span>Вход / выход</span><strong>' +
    number(run && run.input_tokens) + ' / ' + number(run && run.output_tokens) +
    '</strong></div><div class="mini-stat"><span>Из кэша</span><strong>' + number(run && run.cached_input_tokens) +
    '</strong></div><div class="mini-stat"><span>Денежная цена</span><strong>Не предоставляется</strong></div><div class="tiny">Использование включено в лимиты подписки. Токены не равны денежному списанию.</div></div>');
  const scenarioPanel = panel('Попробуйте сценарий','<div class="panel-body scenario-list">' +
    scenarios.map((scenario,index) => '<button class="scenario" data-scenario="' + index + '">' + icon(scenario.icon) +
      '<span>' + scenario.name + '</span><span>↗</span></button>').join("") + '</div>','<span class="eyebrow">06</span>','scenarios');
  return error + '<div class="chat-layout">' + chat + '<aside class="inspector">' + character + context + runInfo + scenarioPanel + '</aside></div>';
}
function graphHTML() {
  const nodes = state.data.memory.nodes;
  const positions = {};
  nodes.forEach((node,index) => {
    if (!index) positions[node.id] = [450,225];
    else {
      const angle = (index - 1) / Math.max(1,nodes.length-1) * Math.PI * 2 - Math.PI/2;
      positions[node.id] = [450 + Math.cos(angle)*300,225 + Math.sin(angle)*160];
    }
  });
  const edges = state.data.memory.edges.map((edge) => {
    const a=positions[edge.source], b=positions[edge.target];
    return '<line class="edge" x1="' + a[0] + '" y1="' + a[1] + '" x2="' + b[0] + '" y2="' + b[1] + '"/>';
  }).join("");
  const elements = nodes.map((node,index) => {
    const p=positions[node.id], root=!index;
    return '<g class="node' + (state.entity === node.id ? ' selected' : '') + '" data-node="' + e(node.id) +
      '" tabindex="0" role="button" aria-label="' + e(node.name) + '" transform="translate(' + p[0] + ' ' + p[1] +
      ')"><circle r="' + (root ? 39 : 25) + '" fill="' + (root ? '#e7f1d6' : node.kind === 'unresolved' ? '#f0eee9' : '#f0ecf8') +
      '" stroke="' + (root ? '#bed399' : '#ccc3de') + '" stroke-width="1.5"/><text text-anchor="middle" y="5" font-size="' +
      (root ? 20 : 15) + '">' + (root ? 'S' : e(node.name.slice(0,1).toUpperCase())) + '</text><text text-anchor="middle" y="' +
      (root ? 65 : 48) + '" class="' + (root ? 'root-label' : '') + '">' + e(node.name.length > 22 ? node.name.slice(0,20)+'…' : node.name) + '</text></g>';
  }).join("");
  return '<div class="graph"><svg viewBox="0 0 900 470" role="img" aria-label="Граф связей памяти">' + edges + elements +
    '</svg></div><div class="graph-legend"><span><i class="legend-dot"></i>Персонаж</span><span><i class="legend-dot violet"></i>Сущность</span><span><i class="legend-dot grey"></i>Связь из заметки</span></div>';
}
function entityInspector() {
  const entity = state.data.memory.entities.find((item) => item.id === state.entity);
  if (!entity) return panel('Инспектор сущности','<div class="panel-body"><div class="entity-kind">CHARACTER</div><h2 class="entity-title">Саруман</h2><p class="subtitle">Нажмите на сущность в графе или таблице, чтобы прочитать её историю.</p><div class="note-preview">' +
    (state.data.memory.entities.length ? 'В графе показаны связи из [[ссылок]] в файлах памяти.' : 'Встреч ещё нет. Включите «Запоминать факты» в чате и представьтесь персонажу. Здесь появятся только сохранённые сведения.') + '</div></div>');
  return panel('Инспектор сущности','<div class="panel-body"><div class="entity-kind">' + e(entity.kind) +
    '</div><h2 class="entity-title">' + e(entity.name) + '</h2><div class="tiny mono">' + e(entity.id) +
    '</div><pre class="note-preview">' + e(entity.body) + '</pre></div>');
}
function memoryView() {
  const memory=state.data.memory;
  const stats='<div class="stats-grid">' +
    stat('Память на диске',bytes(memory.bytes),'Markdown-файлы персонажа',true) +
    stat('Сущности',number(memory.entities.length),'Игроки, места и события') +
    stat('Связи',number(memory.edges.length),'Из ссылок в заметках') +
    stat('Размер контекста, ≈',number(memory.tokens_estimate),'Токены · оценка по символам') + '</div>';
  const tabs='<div class="subtabs" role="tablist" aria-label="Представление памяти">' +
    [["graph","Граф связей"],["entities","Сущности"],["files","Файлы памяти"],["database","База данных"]].map(([id,title]) =>
      '<button role="tab" aria-selected="' + (state.memoryView === id) + '" class="' + (state.memoryView === id ? 'active' : '') + '" data-memory-view="' + id + '">' + title + '</button>').join("") + '</div>';
  let content;
  if (state.memoryView === "graph") {
    content='<div class="memory-layout">' + panel(icon("link")+'Карта воспоминаний',graphHTML(),'<span class="tiny">Нажмите на узел</span>') + entityInspector() + '</div>';
  } else if (state.memoryView === "entities") {
    const rows=memory.entities.map((entity) => '<tr data-node="' + e(entity.id) + '"><td><strong>' + e(entity.name) +
      '</strong></td><td>' + e(entity.kind) + '</td><td class="mono">' + e(entity.id) + '</td><td>' + entity.links.length + '</td><td>' + bytes(entity.bytes) + '</td></tr>').join("");
    content='<div class="memory-layout">' + panel('Сущности в памяти', rows ? '<div class="table-scroll"><table><thead><tr><th>Имя</th><th>Тип</th><th>ID</th><th>Ссылки</th><th>Размер</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>' : '<div class="empty"><strong>Знакомства ещё впереди</strong>Сохранённые факты из чата появятся здесь.</div>') + entityInspector() + '</div>';
  } else if (state.memoryView === "files") {
    const file=memory.files.find((item) => item.path === state.file) || memory.files[0];
    content=panel('Исходные файлы памяти','<div class="file-layout"><div class="file-list">' +
      memory.files.map((item) => '<button class="file-button ' + (file && file.path === item.path ? 'active' : '') +
        '" data-file="' + e(item.path) + '">' + e(item.path.replace("memory/","")) + '</button>').join("") +
      '</div><pre class="file-content">' + e(file ? file.body : 'Нет файлов памяти') + '</pre></div>');
  } else {
    const table=state.databaseData;
    const head='<div class="db-toolbar"><select id="database-table" aria-label="Таблица БД">' +
      ["sessions","messages","runs"].map((name) => '<option' + (state.database === name ? ' selected' : '') + '>' + name + '</option>').join("") +
      '</select><span class="tiny">SQLite · .saruman/lab.sqlite3</span></div>';
    content=panel(head, !table ? '<div class="empty">Загрузка таблицы…</div>' : !table.rows.length ?
      '<div class="empty"><strong>Таблица пока пуста</strong>Создайте тестовый диалог.</div>' :
      '<div class="table-scroll"><table><thead><tr>' + table.columns.map((col) => '<th>' + e(col) + '</th>').join("") +
      '</tr></thead><tbody>' + table.rows.map((row) => '<tr>' + table.columns.map((col) => '<td title="' + e(row[col]) + '">' +
        e(row[col] == null ? 'NULL' : row[col]) + '</td>').join("") + '</tr>').join("") + '</tbody></table></div>',
      table ? '<span class="tiny">' + table.rows.length + ' из ' + table.total + ' записей</span>' : "");
    content+='<p class="tiny">Сущности хранятся в Markdown и доступны во вкладке «Сущности». SQLite содержит диалоги и статистику; произвольные SQL-запросы не выполняются.</p>';
  }
  return stats+tabs+content;
}
function historyView() {
  const sessions=state.data.sessions;
  const list=panel('Сохранённые диалоги','<div class="history-list">' + (sessions.length ? sessions.map((session) => '<button class="history-item ' +
    (state.session === session.id ? 'active' : '') + '" data-session="' + session.id + '"><strong>' + e(session.title) +
    '</strong><small>' + date(session.created_at) + ' · ' + session.message_count + ' сообщений</small></button>').join("") :
    '<div class="empty">История пока пуста.</div>') + '</div>');
  const current=activeSession();
  const detail=panel(e(current ? current.title : 'Последовательный диалог'),'<div class="history-messages">' +
    (state.messages.length ? state.messages.map(messageHTML).join("") :
      '<div class="empty"><strong>Каждый ответ — часть истории</strong>Выберите диалог или начните первый тест в чате.</div>') +
    '</div>',current ? button('Экспорт JSON','export','','export') : "");
  return '<div class="history-layout">'+list+detail+'</div>';
}
function usageChart() {
  const days=state.data.analytics.daily.slice(-14);
  if (!days.length) return '<div class="empty"><strong>Пока нет измеренных запросов</strong>Здесь появится расход входных и выходных токенов по дням.</div>';
  const max=Math.max(...days.map((row) => row.input+row.output),1);
  const width=660,base=185, step=width/days.length;
  return '<svg class="chart" viewBox="0 0 720 230" role="img" aria-label="Входные и выходные токены по дням">' +
    [0,.5,1].map((n) => '<line x1="40" y1="' + (base-n*150) + '" x2="710" y2="' + (base-n*150) + '"/><text x="0" y="' +
      (base-n*150+4) + '">' + number(Math.round(max*n)) + '</text>').join("") +
    days.map((row,index) => {
      const x=45+index*step, w=Math.min(step*.5,62), h=row.input/max*150, o=row.output/max*150;
      return '<g><title>' + e(row.day)+': '+number(row.input)+' вход / '+number(row.output)+' выход</title><rect x="' + x + '" y="' +
        (base-h) + '" width="' + w + '" height="' + h + '" fill="#b8d28f"/><rect x="' + x + '" y="' + (base-h-o) +
        '" width="' + w + '" height="' + o + '" fill="#aaa0c3" rx="3"/><text x="' + x + '" y="211">' +
        e(row.day.slice(5).replace("-",".")) + '</text></g>';
    }).join("") + '</svg>';
}
function limitsHTML() {
  const provider=state.data.provider, limits=provider.limits;
  if (!limits) return '<div class="tiny">' + e(provider.limit_error || 'Нажмите «Подключить Codex», чтобы запросить лимиты аккаунта. Недоступные показатели не заменяются нулём.') + '</div>';
  const buckets=limits.rateLimitsByLimitId || (limits.rateLimits ? {codex:limits.rateLimits} : {});
  return Object.entries(buckets).map(([id,bucket]) => '<div class="tiny mono">' + e(bucket.limitName || id) + '</div>' +
    ["primary","secondary"].map((key) => {
      const window=bucket[key];
      if (!window) return '';
      const used=Math.max(0,Math.min(100,Number(window.usedPercent)));
      const hours=window.windowDurationMins ? window.windowDurationMins/60 : null;
      const label=hours == null ? key : hours < 24 ? hours+' ч' : (hours/24).toFixed(0)+' дн';
      return '<div class="mini-stat"><span>Окно ' + e(label) + '</span><strong>' + e(window.usedPercent) + '% использовано</strong></div><div class="meter"><span style="width:' +
        used + '%"></span></div><div class="tiny">Сброс: ' + e(window.resetsAt ? date(window.resetsAt*1000) : 'неизвестно') + '</div>';
    }).join("")).join("") || '<div class="tiny">Провайдер не вернул окна лимитов.</div>';
}
function analyticsView() {
  const analytics=state.data.analytics, totals=analytics.totals;
  const latest=selectedRun() || analytics.runs[0];
  const stats='<div class="stats-grid">' + stat('Запросов',number(analytics.request_count),analytics.measured_count+' с измеренными токенами',true) +
    stat('Входные токены',number(totals.input_tokens),'Включая закэшированный вход') +
    stat('Выходные токены',number(totals.output_tokens),'Ответ + запись памяти + reasoning') +
    stat('Денежные затраты','—','Цена запроса по подписке не предоставляется')+'</div>';
  const chart=panel('Использование по дням · UTC','<div class="panel-body">'+usageChart()+
    '<div class="usage-legend"><span><i class="legend-dot"></i>Вход</span><span><i class="legend-dot violet"></i>Выход</span></div></div>');
  const limits=panel(icon("shield")+'Лимиты Codex / ChatGPT','<div class="panel-body">'+limitsHTML()+
    '<p class="tiny">Окна относятся к аккаунту и могут включать расход других приложений. Обновляйте подключение для свежих данных.</p><a class="button small" href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Управление в ChatGPT ↗</a></div>');
  let split;
  if (latest) {
    const parts=[["Инструкции",latest.persona_input_est||0,"#a2bc7f"],["Память",latest.memory_input_est||0,"#aba0c6"],
      ["История",latest.history_input_est||0,"#c5d0b7"],["Сообщение",latest.message_input_est||0,"#dfcea0"]];
    const sum=parts.reduce((a,item)=>a+item[1],0)||1;
    split='<div class="panel-body"><div class="bar-split">' + parts.map((item)=>'<span style="width:'+item[1]/sum*100+'%;background:'+item[2]+'"></span>').join("")+
      '</div>'+parts.map((item)=>'<div class="mini-stat"><span>'+item[0]+'</span><strong>≈ '+number(item[1])+' ток.</strong></div>').join("")+
      '<div class="tiny">Запись новых фактов: ≈ '+number(latest.memory_output_est)+' выходных токенов. Это оценка пользовательского контекста по символам. Служебный контекст Codex сюда не входит; это не отдельный счёт провайдера.</div></div>';
  } else split='<div class="empty">После первого запроса покажем вклад памяти, истории и инструкций.</div>';
  const rows=analytics.runs.map((run)=>'<tr><td>'+e(date(run.created_at))+'</td><td class="mono">'+e(run.model)+'</td><td class="status-'+e(run.status)+'">'+
    ({completed:"Готово",failed:"Ошибка",running:"В работе"}[run.status]||e(run.status))+'</td><td>'+number(run.input_tokens)+'</td><td>'+number(run.cached_input_tokens)+
    '</td><td>'+number(run.output_tokens)+'</td><td>≈ '+number(run.memory_input_est)+'</td><td>'+number(run.remembered)+
    '</td><td>'+(run.latency_ms==null?'—':(run.latency_ms/1000).toFixed(1)+' с')+'</td><td title="'+e(run.error)+'">'+e(run.error||'—')+'</td></tr>').join("");
  return stats+'<div class="analytics-layout">'+chart+limits+'</div>'+panel('Из чего состоит вход · последний запрос',split)+
    '<div class="alert" style="margin-top:18px">'+e(analytics.money_note)+' '+e(analytics.estimate_note)+
    ' Reasoning — часть выходных токенов, кэш — часть входных; они не прибавляются к сумме повторно.</div>'+
    panel('Запросы и вклад памяти',rows?'<div class="table-scroll"><table><thead><tr><th>Время</th><th>Модель</th><th>Статус</th><th>Вход</th><th>Кэш</th><th>Выход</th><th>Память, оценка</th><th>Фактов</th><th>Задержка</th><th>Ошибка / примечание</th></tr></thead><tbody>'+
      rows+'</tbody></table></div>':'<div class="empty">Запросов ещё нет.</div>');
}
function settingsView(){
  const g=state.gameForm||state.data.runtime.game_settings;
  return modelSettingsView()+panel('Подключение к Minecraft','<form id="game-settings" class="panel-body role-selectors">'+
    '<label>Тип<select id="game-mode"><option value="server" '+(g.mode==='server'?'selected':'')+'>Сервер</option><option value="world" '+(g.mode==='world'?'selected':'')+'>Существующий одиночный мир</option></select></label>'+
    '<label>Адрес сервера или точное имя папки в saves<input id="game-target" value="'+e(g.target)+'" required></label>'+
    '<label>Лаунчер .exe (необязательно)<input id="game-launcher" value="'+e(g.launcher)+'"></label>'+
    '<label>Аргументы лаунчера — JSON-массив<textarea id="game-arguments">'+e(JSON.stringify(g.arguments))+'</textarea></label>'+
    '<p class="tiny">Для одиночного мира укажите имя его папки в saves запущенного профиля. Если совпадения нет, Lab попросит точное имя; другой мир автоматически не выбирается. Minecraft 1.21.11 с UnionClef. Лаунчер должен уметь запускать выбранный профиль с вашими аргументами. Без настройки лаунчера сначала откройте клиент вручную. Вход подтверждается игрой; авторизация и предупреждения игры требуют вашего участия.</p>'+
    '<button class="button primary">Сохранить подключение</button></form>');
}
function controlView(){
  const d=state.admin||{messages:[],game_result:{}};
  const labels={accepted:'Принято',answered:'Ответ',interpreting:'Разбирает команду',needs_input:'Нужно уточнение',
    no_draft:'Нет готового поста',draft:'Готов к публикации',waiting_start:'Ожидает Start',queued:'В очереди',preparing:'Готовится',sending:'Отправляется',
    sent:'Опубликовано',already_sent:'Уже опубликовано',failed:'Ошибка',error:'Ошибка',unknown:'Проверьте доставку',cancelled:'Отменено',
    running:'Выполняется',completed:'Завершено',pending:'Ожидает'};
  const steps={connect:'Вход в мир',game_task:'Игровое действие',screenshot:'Скриншот Minecraft',draft:'Подготовка поста',publish:'Публикация в канале'};
  const messages=d.messages.slice().reverse().map(m=>{
    let result={};try{const parsed=JSON.parse(m.result);if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed))result=parsed;}catch(error){}
    return '<article class="panel-body"><strong>Вы</strong><p class="bubble">'+e(m.input)+'</p><strong>Управление · '+e(labels[m.status]||m.status)+
      '</strong><pre class="note-preview">'+e(result.detail||result.error||m.result)+'</pre>'+
      (result.steps?'<ol class="workflow-steps">'+result.steps.map(s=>'<li><strong>'+e(steps[s.operation]||s.operation)+'</strong> · '+e(labels[s.status]||s.status)+
        (s.text?'<p class="tiny">'+e(s.text)+'</p>':'')+
        (s.result?.error||s.result?.reason||s.result?.detail?'<p class="tiny">'+e(s.result.error||s.result.reason||s.result.detail)+'</p>':'')+
        (s.operation==='screenshot'&&s.result?.sha256?'<a href="/api/media/'+e(s.result.sha256)+'" target="_blank" rel="noreferrer"><img class="workflow-photo" src="/api/media/'+e(s.result.sha256)+'" alt="Снимок Minecraft"></a>':'')+
        (s.result?.message_id?'<p class="tiny">Telegram: сообщение '+e(s.result.message_id)+'</p>':'')+'</li>').join('')+'</ol>':'')+
      (result.operation==='publish'&&m.status==='needs_input'?'<a href="#telegram">Выбрать пост в Telegram</a>':'')+'</article>';
  }).join('');
  return panel('Приватный административный чат','<div class="panel-body tiny">Этот чат управляет доступными операциями. Он отделён от публичных диалогов и памяти персонажа. Принятие поручения ещё не означает его выполнение.</div>'+
    '<div class="admin-messages">'+(messages||'<div class="empty">Попросите проверить состояние, подготовить пост или выполнить игровое поручение.</div>')+'</div>'+
    '<form id="admin-form" class="composer"><textarea id="admin-text" placeholder="Например: посади дерево, затем опубликуй пост со скриншотом" maxlength="12000">'+e(state.adminDraft)+'</textarea><button class="button primary" '+(state.adminBusy?'disabled':'')+'>'+(state.adminBusy?'Обрабатываем…':'Отправить')+'</button></form>',
    button('Войти в настроенный мир','game-connect'))+
    panel('Состояние подключения','<div class="panel-body">'+e(state.data.runtime.connection.reason)+'</div>')+
    (d.game_result.command_id?panel('Результат игрового поручения','<pre class="note-preview">'+e(JSON.stringify(d.game_result,null,2))+'</pre>'):'');
}
function runtimeControls() {
  const runtime=state.data.runtime||{active:false,components:{},options:{minecraft:true}};
  const names={running:'Работает',stopped:'Остановлен',stopping:'Останавливается',starting:'Запуск',restarting:'Перезапуск',checking:'Проверка',ready:'Готов',
    waiting_world:'Ожидает мир',waiting_services:'Ожидает службы',paused:'Пауза',retrying:'Повтор подключения',
    disabled:'Выключен',unavailable:'Недоступен',not_configured:'Нужна настройка',external:'Отдельный процесс'};
  const minecraft=state.minecraftChoice===null?runtime.options.minecraft:state.minecraftChoice;
  const filter=state.filterChoice===null?runtime.options.filter_enabled!==false:state.filterChoice;
  const cards=Object.entries(runtime.components).map(([key,item])=>'<div class="runtime-component"><strong>'+e(key==='minecraft'?'Minecraft':'Telegram')+
    ' <span class="tag">'+e(names[item.status]||item.status)+'</span></strong><small>'+e(item.reason||'')+'</small></div>').join('');
  return '<section id="runtime-controls" class="runtime-controls" aria-label="Управление персонажем"><div class="runtime-actions"><strong>Персонаж</strong>'+
    '<label class="check-label"><input id="runtime-minecraft" type="checkbox" '+(minecraft?'checked':'')+'> Minecraft</label>'+
    '<label class="check-label"><input id="runtime-filter" type="checkbox" '+(filter?'checked':'')+' '+(runtime.active?'disabled':'')+'> Фильтр речи и игрового чата</label>'+
    '<button class="button primary" data-action="runtime-start" '+(state.runtimeBusy?'disabled':'')+'>Start</button>'+
    '<button class="button" data-action="runtime-stop" '+(state.runtimeBusy?'disabled':'')+'>Stop</button>'+
    '<small>'+e(runtime.active?'Запущен из Lab':'Остановлен')+'</small></div><div class="runtime-components">'+cards+'</div>'+
    '<small>Фильтр применяется при Start. Для изменения нажмите Stop. При выключении речь и игровой чат проходят без модерации.</small>'+
    (runtime.error?'<div class="alert error">'+e(runtime.error)+'</div>':'')+'</section>';
}
function telegramView() {
  const d=state.telegram;
  if(!d)return panel('Telegram','<div class="empty">Загрузка…</div>');
  const names={draft:'На просмотре',preparing:'Готовится',queued:'В очереди',sending:'Отправляется',sent:'Опубликовано',failed:'Ошибка',unknown:'Доставка неизвестна',cancelled:'Отменено'};
  const online=d.connection.checked_at && Date.now()/1000-d.connection.checked_at<90 && d.connection.can_post;
  const form=state.telegramForm||d.settings;
  const tunnelNames={running:'подключён',external:'внешний туннель или HTTPS',connecting:'подключение',retrying:'повтор подключения',not_configured:'требуется настройка',stopped:'отключён'};
  const updateNames={not_checked:'ещё не проверено',receiving:'подключено'};
  const postModel=state.data.models.profiles.telegram;
  const character=state.data.runtime;
  const stats='<div class="stats-grid">'+stat('Шлюз',online?'Доступен':'Нет подтверждения',d.connection.channel_title||d.connection.error||'Нажмите «Проверить связь»',online)+
    stat('Подготовка',d.counts.draft?'Ждёт просмотра':d.draft_status.status==='generating'?'Пишет пост':character&&character.active?'По расписанию':'Остановлена','Telegram работает и при закрытом Minecraft')+
    stat('Опубликовано',number(d.counts.sent||0),'В локальном журнале')+stat('События',number(d.pending_events),'Ещё не включены в пост')+'</div>';
  const controls=panel('Публикации','<form id="telegram-settings" class="panel-body">'+
    '<label><input id="tg-enabled" type="checkbox" '+(form.enabled?'checked':'')+'> Включить Telegram</label><br><br>'+
    '<label><input id="tg-auto" type="checkbox" '+(form.auto_post?'checked':'')+'> Публиковать подготовленные посты автоматически</label><br><br>'+
    '<label><input id="tg-replies" type="checkbox" '+(form.reply_enabled?'checked':'')+'> Отвечать на личные сообщения и комментарии</label><br><br>'+
    '<p class="tiny">Ответы требуют gateway.py API v3 на VPS. Для комментариев нужна связанная группа обсуждения и доступ бота к её сообщениям.</p>'+
    '<label>Интервал подготовки постов, секунд <input id="tg-interval" type="number" min="1" value="'+e(form.interval_seconds)+'"></label><br><br>'+
    '<label>Событий в одном запросе модели <input id="tg-batch" type="number" min="1" value="'+e(form.batch_size)+'"></label><br><br>'+
    '<button class="button lime" type="submit">Сохранить</button> '+button('Проверить связь','tg-check','','refresh')+
    '<p class="tiny">Без автопубликации тексты остаются на просмотре. Их готовит отдельная модель роли Telegram. Выберите её в разделе «Настройки». Доставка постов не использует LLM.</p>'+
    '<p class="tiny">Модель постов: '+e(postModel?postModel.provider+' / '+postModel.model+' · '+postModel.reasoning:'выберите в настройках')+'.</p>'+
    '<p class="tiny">Следующая подготовка: '+e(character&&character.next_draft?new Date(character.next_draft*1000).toLocaleString('ru-RU'):'по расписанию после Start')+'. Без новых игровых событий Саруман может написать размышление.</p>'+
    '<p class="tiny">Ключи: integrations/telegram-gateway/.env.client. SSH: '+e(tunnelNames[d.tunnel.status]||d.tunnel.status||'не проверялся')+'. При отключении Telegram очередь сохраняется.</p>'+
    (d.tunnel.detail?'<p class="tiny">'+e(d.tunnel.detail)+'</p>':'')+
    (d.draft_status.error?'<p class="tiny">Подготовка: '+e(d.draft_status.error)+'</p>':'')+
    (d.updates_status==='receiving'?'':'<p class="tiny">Чтение канала: '+e(updateNames[d.updates_status]||d.updates_status)+'</p>')+'</form>');
  const manual=panel('Свой черновик','<form id="telegram-draft" class="panel-body"><textarea id="tg-text" rows="4" placeholder="Текст для канала" aria-label="Текст поста">'+e(state.telegramDraft)+
    '</textarea><br><button class="button" type="submit">Сохранить черновик</button><p class="tiny">Для отправки нажмите «В очередь» у поста, включите Telegram и нажмите Start сверху.</p></form>');
  const posts=d.posts.map(p=>'<article class="panel-body"><strong>'+e(names[p.status]||p.status)+'</strong> · '+e(new Date(p.created*1000).toLocaleString('ru-RU'))+
    '<p style="white-space:pre-wrap">'+e(p.text||'Подготовка текста…')+'</p><div class="tiny">ID: '+e(p.id)+' · попыток: '+number(p.attempts)+(p.message_id?' · Telegram #'+e(p.message_id):'')+'</div>'+
    (p.error?'<p class="tiny">'+e(p.error)+(p.status==='unknown'?' — проверьте канал; автоматической повторной отправки нет.':'')+'</p>':'')+
    (['draft','failed'].includes(p.status)?'<button class="button" data-tg-id="'+e(p.id)+'" data-tg-action="queue">В очередь</button> ':'')+
    (['draft','queued','failed'].includes(p.status)?'<button class="button" data-tg-id="'+e(p.id)+'" data-tg-action="cancel">Отменить</button>':'')+
    '<details><summary>Источники событий</summary><pre class="note-preview">'+e(JSON.stringify(p.event_ids,null,2))+'</pre></details></article>').join('');
  const records=(rows,label)=>rows.map(r=>'<details class="panel-body"><summary>'+e(r.kind||r.stage||('Telegram update '+r.update_id))+'</summary><pre class="note-preview">'+e(JSON.stringify(r,null,2))+'</pre></details>').join('')||'<div class="empty">'+label+'</div>';
  const dialogue=state.dialogues?'<div class="panel-body"><p class="tiny">Память разделена по Telegram user_id и чату; личные переписки не включаются в посты.</p>'+state.dialogues.people.map(p=>'<details><summary>'+e(p.name)+' · user '+e(p.user_id)+' · '+e(p.scope)+'</summary><p>'+e(p.memory||'Фактов пока нет')+'</p></details>').join('')+state.dialogues.messages.map(m=>'<details><summary>'+e(m.scope)+' · user '+e(m.user_id)+' · '+e(m.status)+'</summary><p>'+e(m.input)+'</p><p>'+e(m.reply||m.error||'Ожидание')+'</p></details>').join('')+(state.dialogues.next_before?button('Более ранние сообщения','tg-dialogues-before'):'')+'</div>':'<div class="panel-body">'+button('Посмотреть переписки и память','tg-dialogues')+'</div>';
  return stats+'<div class="settings-layout">'+controls+manual+'</div>'+panel('Ответы людям',dialogue)+panel('История постов',posts||'<div class="empty">Постов пока нет. Подтверждённые события или размышления появятся по расписанию.</div>')+
    '<details>'+ '<summary>Память событий и диагностика</summary>'+panel('Игровые события',records(d.events,'Игровых событий пока нет'))+
    panel('Входящие посты канала',records(d.inbox,'Полученных обновлений пока нет'))+panel('Попытки подготовки и отправки',records(d.attempts,'Попыток пока нет'))+'</details>'+
    '<div class="panel-body">'+button('← Назад','tg-prev')+' <span>Страница '+(state.telegramPage+1)+'</span> '+button('Далее →','tg-next')+'</div>';
}
async function refreshTelegram() {
  state.telegram=await api('/api/telegram?page='+state.telegramPage);
}
function render() {
  if (!state.data) return;
  if (!views[state.view]) state.view="chat";
  const provider=state.data.provider;
  const bodies={control:controlView,chat:chatView,memory:memoryView,history:historyView,analytics:analyticsView,telegram:telegramView,settings:settingsView};
  $("#app").innerHTML='<div class="shell">'+sidebar()+'<main class="main"><header class="topbar"><div class="breadcrumb"><span>Рабочее пространство</span><span>/</span><strong>Саруман</strong></div><div class="top-actions"><span class="pill"><span class="dot '+
    (chatReady()?'online':'')+'"></span>'+e(providerNames[chatProfile().provider])+' / '+e(chatProfile().model)+
    '</span>'+(state.view==='settings'?'':button(views.settings,'models-settings'))+
    '</div></header>'+heading()+runtimeControls()+bodies[state.view]()+'<footer class="page-foot"><span>ISENGARD LAB · Реальные файлы, наблюдаемые данные</span><span>Память: Markdown · История: SQLite · Модели: по задачам</span></footer></main></div>';
}
async function refresh() {
  state.data=await api("/api/overview");
  state.model=chatProfile().model;
}
async function loadSession(id) {
  state.session=id||null; state.error=null;
  const selected=state.session;
  const messages=selected?await api("/api/messages?session_id="+encodeURIComponent(selected)):[];
  if(state.session===selected) state.messages=messages;
}
async function database() {
  state.databaseData=null;render();
  const name=state.database;
  const data=await api("/api/database?table="+encodeURIComponent(name));
  if(state.database===name) state.databaseData=data;
  render();
}
async function sendMessage() {
  if(state.busy||!state.draft.trim()) return;
  if(!chatReady()){toast("Проверьте подключение выбранного провайдера в настройках.");return;}
  const text=state.draft.trim();
  state.busy=true;state.error=null;
  try {
    if(!state.session) {
      const session=await api("/api/sessions",{});
      await refresh();state.session=session.id;
    }
    state.messages.push({role:"user",content:text,created_at:new Date().toISOString()});
    state.draft="";render();
    if($("#messages")) $("#messages").scrollTop=$("#messages").scrollHeight;
    const result=await api("/api/chat",{session_id:state.session,model:state.model,text,remember:state.remember});
    if(result.warning) toast(result.warning);
    else if(result.remembered) toast("Сохранено фактов: "+result.remembered+". Граф памяти обновлён.");
  } catch(error) {
    state.error=error.message;toast(error.message);
  } finally {
    state.busy=false;
    try {await refresh();await loadSession(state.session);} catch(error){toast(error.message);}
    // loadSession clears stale errors; the failed run remains visible in analytics.
    const run=selectedRun();
    if(run&&run.status==="failed") state.error=run.error;
    render();
    if($("#messages")) $("#messages").scrollTop=$("#messages").scrollHeight;
    if($("#message-input")) $("#message-input").focus();
  }
}
document.addEventListener("input",(event)=>{
  if(event.target.dataset.role && event.target.type==='number') {
    const role=event.target.dataset.role;
    const p=state.roleDrafts[role] ||= {...state.data.models.profiles[role]};
    p[event.target.dataset.field]=Number(event.target.value);
  }
  if(event.target.id==="message-input") state.draft=event.target.value;
  if(event.target.id==="tg-text") state.telegramDraft=event.target.value;
  if(event.target.closest('#telegram-settings')) state.telegramForm={
    enabled:$('#tg-enabled').checked,auto_post:$('#tg-auto').checked,reply_enabled:$('#tg-replies').checked,
    interval_seconds:Number($('#tg-interval').value),batch_size:Number($('#tg-batch').value)};
});
document.addEventListener("change",async(event)=>{
  try {
    if(event.target.dataset.role && event.target.tagName==='SELECT') {
      const role=event.target.dataset.role, field=event.target.dataset.field;
      const p=state.roleDrafts[role] ||= {...state.data.models.profiles[role]};
      state.showOptional=!!$('#optional-roles')?.open;
      p[field]=event.target.value;
      if(field==='provider'){
        p.model=Object.keys(state.data.models.catalogue[p.provider])[0];
        if(p.provider==='minimax')p.max_output=Math.max(p.max_output,32768);
      }
      if(field==='provider'||field==='model')p.reasoning='default';
      render();return;
    }
    if(event.target.id==="remember") state.remember=event.target.checked;
    if(event.target.id==="model-select") state.model=event.target.value;
    if(event.target.id==="session-select") {
      if(state.busy) return;
      await loadSession(event.target.value);render();
    }
    if(event.target.id==="database-table") {state.database=event.target.value;await database();}
  } catch(error){toast(error.message);}
});
document.addEventListener("submit",(event)=>{
  if(event.target.id==='game-settings'){
    event.preventDefault();
    try{
      const value={mode:$('#game-mode').value,target:$('#game-target').value,launcher:$('#game-launcher').value,arguments:JSON.parse($('#game-arguments').value)};
      api('/api/runtime/connection',value).then(async()=>{state.gameForm=null;await refresh();render();toast('Подключение сохранено.');}).catch(error=>toast(error.message));
    }catch(error){toast('Аргументы должны быть JSON-массивом строк.');}
  }
  if(event.target.id==='admin-form'){
    event.preventDefault();if(state.adminBusy)return;
    const text=$('#admin-text').value;if(!text.trim())return;
    state.adminBusy=true;state.adminDraft=text;render();
    api('/api/admin',{text,request_id:crypto.randomUUID()}).then(async()=>{state.adminDraft='';state.admin=await api('/api/admin');await refresh();})
      .catch(error=>toast(error.message)).finally(()=>{state.adminBusy=false;render();});
  }
  if(event.target.dataset.roleForm){
    event.preventDefault();
    const role=event.target.dataset.roleForm;
    const {revision,...profile}=state.roleDrafts[role]||state.data.models.profiles[role];
    state.showOptional=!!$('#optional-roles')?.open;
    state.savingRole=role;render();
    api('/api/models',{role,profile,revision}).then(data=>{
      state.data.models=data;delete state.roleDrafts[role];state.model=chatProfile().model;
      toast('Настройки сохранены. Применятся со следующего запроса.');
    }).catch(error=>toast(error.message)).finally(()=>{state.savingRole=null;render();});
  }
  if(event.target.id==="chat-form"){event.preventDefault();sendMessage();}
  if(event.target.id==='telegram-settings'){
    event.preventDefault();
    const values={enabled:$('#tg-enabled').checked,auto_post:$('#tg-auto').checked,reply_enabled:$('#tg-replies').checked,
      interval_seconds:Number($('#tg-interval').value),batch_size:Number($('#tg-batch').value)};
    api('/api/telegram/settings',values).then(async()=>{state.telegramForm=null;await refreshTelegram();render();toast('Настройки Telegram сохранены.');}).catch(error=>toast(error.message));
  }
  if(event.target.id==='telegram-draft'){
    event.preventDefault();api('/api/telegram/draft',{text:state.telegramDraft}).then(async()=>{state.telegramDraft='';await refreshTelegram();render();}).catch(error=>toast(error.message));
  }
});
document.addEventListener("keydown",(event)=>{
  if(event.target.id==="message-input"&&event.key==="Enter"&&!event.shiftKey&&!event.isComposing){
    event.preventDefault();sendMessage();
  }
  if(event.target.matches("[data-node]")&&(event.key==="Enter"||event.key===" ")){
    event.preventDefault();event.target.dispatchEvent(new MouseEvent("click",{bubbles:true}));
  }
});
document.addEventListener("click",async(event)=>{
  const target=event.target.closest("button,[data-node]");
  if(!target)return;
  try {
    if(target.dataset.tgAction){await api('/api/telegram/action',{id:target.dataset.tgId,action:target.dataset.tgAction});await refreshTelegram();render();return;}
    if(target.dataset.view){location.hash=target.dataset.view;return;}
    if(target.dataset.memoryView){state.memoryView=target.dataset.memoryView;render();if(state.memoryView==="database")await database();return;}
    if(target.dataset.node){state.entity=target.dataset.node;render();return;}
    if(target.dataset.file){state.file=target.dataset.file;render();return;}
    if(target.dataset.session){if(state.busy)return;await loadSession(target.dataset.session);render();return;}
    if(target.dataset.scenario!==undefined){
      if(state.busy)return;
      state.draft=scenarios[Number(target.dataset.scenario)].text;
      if(state.view!=="chat")location.hash="chat";else render();
      $("#message-input").focus();return;
    }
    const action=target.dataset.action;
    if(action==='game-connect'){
      await api('/api/admin',{text:'Войти в настроенный мир',operation:'connect',request_id:crypto.randomUUID()});
      state.admin=await api('/api/admin');await refresh();render();return;
    }
    if(action==='tg-dialogues'){state.dialogues=await api('/api/telegram/dialogues');render();return;}
    if(action==='tg-dialogues-before'){state.dialogues=await api('/api/telegram/dialogues?before='+state.dialogues.next_before);render();return;}
    if(action==='runtime-start'||action==='runtime-stop'){
      if(state.runtimeBusy)return;
      const choice=state.minecraftChoice===null?state.data.runtime.options.minecraft:state.minecraftChoice;
      const filter=state.filterChoice===null?state.data.runtime.options.filter_enabled!==false:state.filterChoice;
      state.runtimeBusy=true;render();
      try{
        state.data.runtime=await api('/api/runtime/'+(action==='runtime-start'?'start':'stop'),action==='runtime-start'?{minecraft:choice,filter_enabled:filter}:{});
        state.minecraftChoice=null;
        state.filterChoice=null;
        toast(action==='runtime-start'?'Запуск персонажа запрошен. Состояния компонентов показаны сверху.':'Остановка запрошена. Очередь Telegram сохранена.');
      }finally{state.runtimeBusy=false;render();}
      return;
    }
    if(action==='models-settings'){location.hash="settings";return;}
    if(action==='tg-check'){const r=await api('/api/telegram/check',{});await refreshTelegram();render();toast(r.can_post?'Бот имеет право публикации.':r.error||'Проверьте подключение и права.');return;}
    if(action==='tg-prev'||action==='tg-next'){state.telegramPage=Math.max(0,state.telegramPage+(action==='tg-next'?1:-1));await refreshTelegram();render();return;}
    if(action==="new-session"){if(state.busy)return;await loadSession(null);state.draft="";render();}
    if(action==="refresh"){await refresh();render();if(state.memoryView==="database"&&state.view==="memory")await database();toast("Данные обновлены.");}
    if(action==="connect"){
      if(state.connecting)return;
      state.connecting=true;render();
      try{
        const provider=await api("/api/provider",{});
        await refresh();render();
        toast(provider.connected?"Codex подключён. Выберите модель и начните тест.":provider.error||"Подключение недоступно.");
      } finally {state.connecting=false;render();}
    }
    if(action==="export"){
      if(!state.session)return;
      const data=await api("/api/export?session_id="+state.session);
      const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
      const link=document.createElement("a");link.href=url;link.download="saruman-dialogue-"+state.session+".json";link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  } catch(error){toast(error.message);}
});
window.addEventListener("hashchange",async()=>{state.view=location.hash.slice(1);render();try{if(state.view==='telegram')await refreshTelegram();if(state.view==='control')state.admin=await api('/api/admin');render();}catch(error){toast(error.message);}});
(async()=>{
  try{await refresh();if(state.data.sessions.length)await loadSession(state.data.sessions[0].id);if(state.view==='telegram')await refreshTelegram();if(state.view==='control')state.admin=await api('/api/admin');render();}
  catch(error){$("#app").innerHTML='<div class="boot"><strong>Лаборатория недоступна</strong><p>'+e(error.message)+'</p><p>Запустите сервер и обновите страницу.</p></div>';}
})();
setInterval(async()=>{
  if(state.view!=='telegram'||!state.data)return;
  try{await refreshTelegram();if(!document.activeElement.closest('form')&&!document.querySelector('details[open]')&&!String(window.getSelection()).trim())render();}
  catch(error){toast(error.message);}
},5000);
setInterval(async()=>{
  if(state.view!=='control'||!state.data||state.adminBusy)return;
  try{state.admin=await api('/api/admin');if(!document.activeElement.closest('form')&&!String(window.getSelection()).trim())render();}
  catch(error){/* Preserve the last confirmed workflow state during reconnect. */}
},3000);
document.addEventListener('change',event=>{
  if(event.target.id==='runtime-minecraft')state.minecraftChoice=event.target.checked;
  if(event.target.id==='runtime-filter')state.filterChoice=event.target.checked;
});
document.addEventListener('input',event=>{
  if(event.target.id==='admin-text')state.adminDraft=event.target.value;
});
setInterval(async()=>{
  if(!state.data||state.runtimeBusy)return;
  try{
    state.data.runtime=await api('/api/runtime');
    const controls=$('#runtime-controls');
    if(controls&&!controls.contains(document.activeElement))controls.outerHTML=runtimeControls();
  }catch(error){/* Keep the last confirmed state; the refresh action reports connection errors. */}
},3000);
