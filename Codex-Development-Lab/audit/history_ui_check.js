// Independent DOM-state checks of the actual inline dashboard JavaScript.
// Run: node audit/history_ui_check.js [app/index.html]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2] || 'app/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // Check the complete shipped script, including startup.

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this._text = ''; this.value = ''; this.disabled = false; this.scrollTop = 0; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  set innerHTML(_value) { throw Error('Untrusted HTML insertion'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; this._text = ''; }
  get options() { return this.children; }
  get firstChild() { return this.children[0]; }
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function environment() {
  const elements = new Map();
  for (const match of html.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
  const pending = [];
  const context = vm.createContext({
    document: {getElementById: id => elements.get(id), createElement: tag => new Element(tag)},
    fetch: path => new Promise(resolve => pending.push({path, resolve: data => resolve({ok: true, text: async () => JSON.stringify(data)})})),
    console, setInterval: () => {}, FormData: class {}, alert() {}, window: {},
  });
  vm.runInContext(script.replace(/setInterval\(\(\)=>refresh\(\)[\s\S]*$/, ''), context);
  const run = expression => vm.runInContext(expression, context);
  function show(jid, tid) {
    run(`active=${JSON.stringify(jid)};historySwitchJob(active);historyJobState(active).selected=${JSON.stringify(tid)};historyJobState(active).attempts=[{id:${JSON.stringify(tid)},round:1,status:'RUNNING'}];renderHistory(active,${JSON.stringify(tid)},true)`);
  }
  return {elements, pending, run, show};
}
function event(offset, text, extra = {}) {
  return {offset, type: 'item.completed', kind: 'agent_message', agent: 'main', agents: ['main'], data: {item: {type: 'agent_message', text}}, ...extra};
}
function page(events, extra = {}) {
  return {attempt: {round: 1, status: 'RUNNING', prompt_available: true, log_available: true}, request: {prompt: 'actual prompt'}, events, next_offset: events.length ? events.at(-1).offset + 10 : 0, stream: 'stream-1', has_more: false, ...extra};
}
async function deliver(env, response, request = 0) {
  env.pending.splice(request, 1)[0].resolve(response);
  await flush();
}
const tests = [];
const test = (name, body) => tests.push([name, body]);

test('untrusted prompt, event and agent identifiers remain literal text', async () => {
  const env = environment(); env.show('A', 'one');
  const promise = env.run("fetchHistoryPage('A','one')");
  const hostile = '<img src=x onerror=alert(1)><script>attack()</script>';
  await deliver(env, page([event(0, hostile, {agent: hostile, agents: [hostile]})], {request: {prompt: hostile}})); await promise;
  assert.equal(env.elements.get('historyPrompt').textContent, hostile);
  assert.ok(env.elements.get('historyEvents').textContent.includes(hostile));
  assert.ok(env.elements.get('historyAgent').options.some(option => option.textContent === hostile));
  assert.equal(env.elements.get('historyEvents').children[0].tagName, 'article');
});

test('child-agent filter includes collaboration requests addressed to that child', async () => {
  const env = environment(); env.show('A', 'one');
  const promise = env.run("fetchHistoryPage('A','one')");
  await deliver(env, page([event(0, 'parent only'), event(10, 'review request', {agents: ['main', 'child-42']})])); await promise;
  env.elements.get('historyAgent').value = 'child-42'; env.elements.get('historyAgent').onchange();
  const cards = env.elements.get('historyEvents').children;
  assert.equal(cards.length, 1); assert.ok(cards[0].textContent.includes('review request'));
  assert.ok(!cards[0].textContent.includes('parent only'));
});

test('append preserves existing cards, scroll, cursor and avoids duplicate offsets', async () => {
  const env = environment(); env.show('A', 'one');
  let promise = env.run("fetchHistoryPage('A','one')");
  await deliver(env, page([event(0, 'first')], {next_offset: 100, has_more: true})); await promise;
  const original = env.elements.get('historyEvents').children[0]; original.children.at(-1).open = true;
  env.elements.get('historyEvents').scrollTop = 56;
  promise = env.run("fetchHistoryPage('A','one')");
  assert.ok(env.pending[0].path.includes('after=100')); assert.ok(env.pending[0].path.includes('stream=stream-1'));
  await deliver(env, page([event(0, 'duplicate'), event(100, 'second')], {next_offset: 200})); await promise;
  const list = env.elements.get('historyEvents'); assert.equal(list.children.length, 2);
  assert.equal(list.children[0], original); assert.equal(original.children.at(-1).open, true); assert.equal(list.scrollTop, 56);
  assert.ok(!list.textContent.includes('duplicate'));
});

test('in-flight response for A cannot paint B; returning to A shows its complete cached history', async () => {
  const env = environment(); env.show('A', 'one');
  const promise = env.run("fetchHistoryPage('A','one')"); env.show('B', 'two');
  await deliver(env, page([event(0, 'A MESSAGE')])); await promise;
  assert.ok(!env.elements.get('historyEvents').textContent.includes('A MESSAGE'));
  env.show('A', 'one'); assert.ok(env.elements.get('historyEvents').textContent.includes('A MESSAGE'));
});

test('A to B to A while A request is pending does not lose cards or its byte cursor', async () => {
  const env = environment(); env.show('A', 'one');
  const promise = env.run("fetchHistoryPage('A','one')"); env.show('B', 'two'); env.show('A', 'one');
  await deliver(env, page([event(0, 'A DELAYED')], {next_offset: 123})); await promise;
  // Next poll renders cached data whose old view generation was invalidated.
  env.run("renderHistory('A','one')"); assert.ok(env.elements.get('historyEvents').textContent.includes('A DELAYED'));
  assert.equal(env.run("historyState('A','one').offset"), 123);
});

test('attempt switch prevents a stale response overwriting the selected attempt', async () => {
  const env = environment(); env.show('A', 'one');
  const promise = env.run("fetchHistoryPage('A','one')");
  env.run("historyJobState('A').attempts.push({id:'two',round:2});historySelect('A','two')");
  await deliver(env, page([event(0, 'SECOND ATTEMPT')]), 1);
  await deliver(env, page([event(0, 'STALE FIRST ATTEMPT')])); await promise;
  assert.ok(env.elements.get('historyEvents').textContent.includes('SECOND ATTEMPT'));
  assert.ok(!env.elements.get('historyEvents').textContent.includes('STALE FIRST ATTEMPT'));
});

test('stream replacement resets old offsets, contents, and duplicate suppression', async () => {
  const env = environment(); env.show('A', 'one');
  let promise = env.run("fetchHistoryPage('A','one')");
  await deliver(env, page([event(0, 'OLD STREAM')], {next_offset: 100})); await promise;
  promise = env.run("fetchHistoryPage('A','one')");
  await deliver(env, page([event(0, 'NEW STREAM')], {stream: 'stream-2', reset: true, next_offset: 30})); await promise;
  assert.ok(env.elements.get('historyEvents').textContent.includes('NEW STREAM'));
  assert.ok(!env.elements.get('historyEvents').textContent.includes('OLD STREAM'));
  assert.equal(env.run("historyState('A','one').offset"), 30);
});

test('bounded display retains byte cursor and start-over requests the beginning', async () => {
  const env = environment(); env.show('A', 'one');
  let promise = env.run("fetchHistoryPage('A','one')");
  await deliver(env, page(Array.from({length: 1002}, (_, i) => event(i * 10, 'message-' + i)), {next_offset: 12000, has_more: true})); await promise;
  assert.equal(env.elements.get('historyEvents').children.length, 1000);
  assert.equal(env.run("historyState('A','one').offset"), 12000);
  assert.equal(env.run("historyState('A','one').discarded"), 2);
  assert.equal(env.elements.get('historyMore').disabled, false);
  env.elements.get('historyStart').onclick(); assert.ok(env.pending[0].path.includes('after=0')); assert.ok(!env.pending[0].path.includes('stream='));
  await deliver(env, page([event(0, 'BEGINNING')]));
  assert.equal(env.elements.get('historyEvents').children.length, 1);
  assert.ok(env.elements.get('historyEvents').textContent.includes('BEGINNING'));
});

(async () => {
  for (const [name, body] of tests) { await body(); process.stdout.write('ok ' + name + '\n'); }
  process.stdout.write('PASS: complete inline JavaScript syntax and ' + tests.length + ' independent DOM-state scenarios\n');
})().catch(error => {console.error(error); process.exitCode = 1;});
