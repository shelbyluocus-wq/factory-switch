// Exercise bridge and refresh behavior in isolation; browser preview checks layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'app.js'), 'utf8');
const fields = ['inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheCreationTokens', 'thinkingTokens', 'factoryCredits'];
const usage = {
  checked_at: '2026-10-03T10:00:00Z', session_count: 2, used_session_count: 1,
  skipped_count: 1, duplicate_count: 1,
  total_tokens: null, total_session_count: 0,
  totals: {inputTokens: 0, outputTokens: 123, cacheReadTokens: null, cacheCreationTokens: null, thinkingTokens: null, factoryCredits: null},
  field_counts: {inputTokens: 2, outputTokens: 1, cacheReadTokens: 0, cacheCreationTokens: 0, thinkingTokens: 0, factoryCredits: 0},
  sessions: [{session_id: 'one', title: '<script>private-title</script>', models: ['model-one', 'model-two'],
    started_at: null, last_active_at: null, usage: Object.fromEntries(fields.map(field => [field, field === 'outputTokens' ? 123 : null]))}],
};
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };

function harness(stateFailure = false, data = usage) {
  const elements = new Map(), events = new Map(), windowEvents = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      id, disabled: false, hidden: false, open: false, innerHTML: '', textContent: '', dataset: {},
      attrs: {}, focus() {}, querySelectorAll() { return []; },
      getAttribute(name) { return this.attrs[name]; },
      setAttribute(name, value) { this.attrs[name] = value; },
      addEventListener(name, callback) { events.set(id + ':' + name, callback); },
      classList: {toggle() {}, add() {}, remove() {}},
    });
    return elements.get(id);
  }
  const periodButtons = ['today', '7d', '30d', 'all'].map(period => {
    const button = element('period-' + period);
    button.dataset.period = period;
    button.attrs['aria-pressed'] = String(period === 'all');
    return button;
  });
  const buttons = [...['tab-accounts', 'tab-local', 'add', 'save', 'refresh'].map(element), ...periodButtons];
  element('tab-accounts').attrs.role = element('tab-local').attrs.role = 'tab';
  const calls = {state: 0, local: 0};
  let localResponse = async () => ({ok: true, usage: data});
  const api = {
    async get_state() { calls.state++; return stateFailure ? {ok: false, error: 'Account unavailable'} : {ok: true, state: {local_identity: null, saved_accounts: []}}; },
    async get_local_usage() { calls.local++; return localResponse(); },
  };
  const document = {
    readyState: 'complete', visibilityState: 'visible', documentElement: {dataset: {}}, body: element('body'),
    getElementById: element,
    querySelector: element,
    querySelectorAll(selector) { return selector === 'main button' ? buttons : selector === '[data-period]' ? periodButtons : []; },
    addEventListener(name, callback) { events.set('document:' + name, callback); },
  };
  vm.runInNewContext(source, {
    document, window: {pywebview: {api}, addEventListener(name, callback) { windowEvents.set(name, callback); }},
    location: {search: ''}, URLSearchParams, setTimeout() {}, setInterval() {}, requestAnimationFrame(callback) { callback(); },
  });
  return {element, calls, events, windowEvents, respond(callback) { localResponse = callback; }};
}

(async () => {
  const app = harness();
  await flush();
  assert.equal(app.calls.state, 1);
  app.element('tab-local').onclick();
  await flush();
  assert.equal(app.calls.local, 1);
  assert.equal(app.element('accounts-panel').hidden, true);
  assert.equal(app.element('local-panel').hidden, false);
  assert.equal(app.element('add').hidden, true);
  assert.equal(app.element('save').hidden, true);
  assert.match(app.element('local-usage').innerHTML, /部分记录 · 1\/2 个会话/);
  assert.match(app.element('local-usage').innerHTML, /未记录/);
  assert.match(app.element('local-usage').innerHTML, /混合模型/);
  assert.match(app.element('local-usage').innerHTML, /暂时无法读取/);
  assert.match(app.element('local-usage').innerHTML, /重复会话快照/);
  assert.match(app.element('local-usage').innerHTML, /&lt;script&gt;private-title&lt;\/script&gt;/);
  assert.doesNotMatch(app.element('local-usage').innerHTML, /<script>/);
  assert.match(app.element('local-usage').innerHTML, /真实消耗 Tokens/);
  assert.match(app.element('local-usage').innerHTML, /没有完整的 Token 记录/);

  app.respond(async () => ({ok: false, error: 'Read failed'}));
  app.element('refresh').onclick();
  await flush();
  assert.equal(app.calls.state, 1, 'Local refresh must not request account state or quota');
  assert.match(app.element('local-usage').innerHTML, /上次数据 · Read failed/);
  assert.match(app.element('local-usage').innerHTML, />123</);
  assert.equal(app.element('refresh').disabled, false);

  let resolve;
  app.respond(() => new Promise(done => { resolve = done; }));
  app.element('refresh').onclick();
  assert.equal(app.element('refresh').disabled, true);
  const before = app.calls.local;
  app.element('refresh').onclick();
  assert.equal(app.calls.local, before, 'Overlapping local reads must be ignored');
  app.element('tab-accounts').onclick();
  assert.equal(app.element('accounts-panel').hidden, false);
  assert.equal(app.element('save').hidden, false);
  assert.equal(app.element('refresh').disabled, false, 'A local read must not block account refresh');
  app.element('refresh').onclick();
  await flush();
  assert.equal(app.calls.state, 2);
  resolve({ok: true, usage});
  await flush();

  const withoutAccount = harness(true);
  await flush();
  assert.equal(withoutAccount.element('tab-local').disabled, false);
  withoutAccount.element('tab-local').onclick();
  await flush();
  assert.equal(withoutAccount.calls.local, 1, 'Local statistics work even when account state is unavailable');
  assert.match(withoutAccount.element('local-usage').innerHTML, />123</);
  assert.equal(withoutAccount.element('period-today').disabled, false);
  withoutAccount.element('period-today').onclick();
  assert.match(withoutAccount.element('local-usage').innerHTML, /暂无可用请求日志/);

  for (const [value, formatted] of [[0, '0'], [9999, '9,999'], [10000, '1万'], [10001, '1万'],
      [1234567, '123.46万'], [99999949, '9999.99万'], [99999950, '1亿'], [100000000, '1亿'],
      [180715821, '1.81亿'], [null, '—']]) {
    const data = {...usage, total_tokens: value, total_session_count: value === null ? 0 : 1};
    const numbers = harness(false, data);
    await flush();
    numbers.element('tab-local').onclick();
    await flush();
    const html = numbers.element('local-usage').innerHTML;
    assert.equal(html.match(/class="local-total-value" title="[^"]*">([^<]*)</)[1], formatted);
    if (value === 180715821) {
      assert.match(html, /180,715,821 Tokens/);
      assert.match(html, /输入 \+ 输出 \+ 缓存创建 \+ 缓存读取 \+ 思考/);
      assert.match(html, /不含 Factory Credits/);
      assert.match(html, /部分记录 · 1\/2 个会话/);
    }
  }

  function period(value, available = true) {
    return {...usage, source: 'request_logs', available, reconciled: false,
      skipped_count: 0, duplicate_count: 0, range_start: '2026-09-27', range_end: '2026-10-03',
      request_count: value > 0 ? 4 : 0, session_count: value > 0 ? 1 : 0, total_tokens: value,
      total_session_count: value === null ? 0 : 1,
      totals: Object.fromEntries(fields.map(field => [field, field === 'factoryCredits' || !available ? null : field === 'inputTokens' ? value : 0])),
      field_counts: Object.fromEntries(fields.map(field => [field, field === 'factoryCredits' ? 0 : 1])),
      sessions: value > 0 ? [{...usage.sessions[0], request_count: 4, total_tokens: value,
        usage: Object.fromEntries(fields.map(field => [field, field === 'factoryCredits' ? null : field === 'inputTokens' ? value : 0]))}] : [],
    };
  }
  const ranged = harness(false, {...usage, total_tokens: 180715821,
    periods: {today: period(30233969), '7d': period(177316888), '30d': period(0)}});
  await flush();
  ranged.element('tab-local').onclick();
  await flush();
  ranged.element('period-today').onclick();
  assert.equal(ranged.calls.local, 1, 'Selecting cached periods does not make another bridge request');
  assert.match(ranged.element('local-usage').innerHTML, />3023.4万</);
  assert.match(ranged.element('local-usage').innerHTML, /今日 · 按请求日志统计/);
  assert.match(ranged.element('local-usage').innerHTML, /请求日志与会话累计有差异/);
  assert.match(ranged.element('local-usage').innerHTML, /请求日志未提供/);
  assert.match(ranged.element('local-usage').innerHTML, /4 次请求/);
  assert.equal(ranged.element('period-today').attrs['aria-pressed'], 'true');
  assert.equal(ranged.element('period-all').attrs['aria-pressed'], 'false');
  ranged.respond(async () => ({ok: false, error: 'Log unavailable'}));
  ranged.element('refresh').onclick();
  await flush();
  assert.match(ranged.element('local-usage').innerHTML, />3023.4万</);
  assert.match(ranged.element('local-usage').innerHTML, /上次数据 · Log unavailable/);
  ranged.element('tab-accounts').onclick();
  ranged.element('tab-local').onclick();
  assert.equal(ranged.element('period-today').attrs['aria-pressed'], 'true');
  ranged.element('period-7d').onclick();
  assert.match(ranged.element('local-usage').innerHTML, />1.77亿</);
  ranged.element('period-30d').onclick();
  assert.match(ranged.element('local-usage').innerHTML, /所选时间没有请求记录/);
  assert.match(ranged.element('local-usage').innerHTML, /class="local-total-value" title="[^"]*">0</);
  ranged.element('period-all').onclick();
  assert.match(ranged.element('local-usage').innerHTML, />1.81亿</);
  assert.doesNotMatch(ranged.element('local-usage').innerHTML, /请求日志未提供/);

  const unavailable = harness(false, {...usage, periods: {today: period(null, false)}});
  await flush();
  unavailable.element('tab-local').onclick();
  await flush();
  unavailable.element('period-today').onclick();
  assert.match(unavailable.element('local-usage').innerHTML, /暂无可用请求日志，无法按时间统计/);
  assert.match(unavailable.element('local-usage').innerHTML, /class="local-total-value" title="[^"]*">—</);
  console.log('Local usage UI: periods, missing logs, refresh, totals, Chinese units and independent reads: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
