const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/step.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function harness() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {textContent: '', hidden: false, disabled: id === 'done',
      addEventListener(name, fn) { this[name] = fn; }});
    return elements.get(id);
  };
  const h = {get, posts: [], timers: [], sockets: [], fail: false, reject: false,
    state: {run: 'test', recording: true, current_step: 0, steps: {}, warnings: [],
      protocol: {name: 'Teaching', steps: [{text: 'Fit tip.'}, {text: 'Draw sample.'}]}}};
  class Socket {
    constructor(url) { this.url = url; h.sockets.push(this); }
    close() { this.onclose(); }
  }
  vm.runInNewContext(source, {
    document: {getElementById: get}, location: {protocol: 'https:', host: 'bench.test'},
    WebSocket: Socket, AbortSignal, Date,
    setTimeout: fn => h.timers.push(fn),
    fetch: async (url, options) => {
      if (url === '/events') {
        h.posts.push(JSON.parse(options.body));
        if (h.fail) throw new Error('lost response');
        return {ok: !h.reject};
      }
      return {ok: true, json: async () => JSON.parse(JSON.stringify(h.state))};
    }
  });
  h.open = async () => { h.sockets.at(-1).onopen(); await flush(); };
  h.update = async () => { h.sockets.at(-1).onmessage(); await flush(); };
  return h;
}

test('current, next, last warning, text safety, final and stopped states', async () => {
  const h = harness(); await h.open();
  assert.equal(h.sockets[0].url, 'wss://bench.test/ws');
  assert.equal(h.get('current').textContent, 'Fit tip.');
  assert.equal(h.get('next').textContent, 'Draw sample.');
  h.state.warnings = [{detail: {text: 'old'}}, {detail: {text: '<img onerror=alert(1)>'}}];
  await h.update();
  assert.equal(h.get('warning').textContent, '<img onerror=alert(1)>');
  assert.equal(h.get('warning-box').hidden, false);
  h.state.current_step = 1; await h.update();
  assert.equal(h.get('next').textContent, 'This is the last step.');
  h.state.current_step = null; h.state.steps = {0: {status: 'done'}, 1: {status: 'unsure'}};
  await h.update();
  assert.match(h.get('current').textContent, /Review the run/);
  assert.equal(h.get('done').disabled, true);
  h.state.recording = false; await h.update();
  assert.equal(h.get('current').textContent, 'Run stopped.');
  assert.equal(h.get('warning-box').hidden, true);
});

test('Done sends exactly one speech event and waits for a coach step', async () => {
  const h = harness(); await h.open();
  const first = h.get('done').click();
  await h.get('done').click(); await first; await flush();
  assert.equal(h.posts.length, 1);
  assert.deepEqual({...h.posts[0], t: 0}, {t: 0, source: 'app', kind: 'speech', step: 0, detail: {text: 'done'}});
  assert.ok(h.posts[0].t > 1e9);
  assert.equal(h.get('done').disabled, true);
  assert.equal(h.get('current').textContent, 'Fit tip.');
  await h.update();
  assert.equal(h.get('done').disabled, true);
  h.state.current_step = 1; await h.update();
  assert.equal(h.get('done').disabled, false);
});

test('disconnect disables Done; reconnect refreshes shared state', async () => {
  const h = harness(); await h.open();
  h.sockets[0].close();
  assert.equal(h.get('done').disabled, true);
  await h.get('done').click();
  assert.equal(h.posts.length, 0);
  h.state.current_step = 1;
  h.timers.shift()(); await h.open();
  assert.equal(h.get('current').textContent, 'Draw sample.');
  assert.equal(h.get('done').disabled, false);
});

test('tap rechecks state and never confirms a newly changed step', async () => {
  const h = harness(); await h.open();
  h.state.current_step = 1; // Changed on server before websocket notification arrives.
  await h.get('done').click();
  assert.equal(h.posts.length, 0);
  assert.equal(h.get('current').textContent, 'Draw sample.');
});

test('uncertain delivery never automatically resends; rejected posts allow retry', async () => {
  const h = harness(); await h.open(); h.fail = true;
  await h.get('done').click(); await flush();
  assert.equal(h.get('done').disabled, true);
  assert.match(h.get('feedback').textContent, /Delivery uncertain/);
  await h.get('done').click(); assert.equal(h.posts.length, 1);
  const rejected = harness(); await rejected.open(); rejected.reject = true;
  await rejected.get('done').click(); await flush();
  assert.equal(rejected.get('done').disabled, false);
  assert.match(rejected.get('feedback').textContent, /Confirmation rejected/);
});
