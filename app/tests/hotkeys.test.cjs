const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/hotkeys.js'), 'utf8');

function harness() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      open: false, disabled: false, clicks: 0,
      click() { this.clicks++; this.onclick?.(); },
      addEventListener(name, fn) { this['on' + name] = fn; },
      showModal() { this.open = true; }, close() { this.open = false; }
    });
    return elements.get(id);
  };
  const h = {get, now: 0, windows: [], notices: []};
  const document = h.document = {
    getElementById: get, activeElement: null, fullscreenElement: null,
    documentElement: {requestFullscreen() { document.fullscreenElement = this; }},
    exitFullscreen() { document.fullscreenElement = null; },
    addEventListener(name, fn) { h[name] = fn; }
  };
  vm.runInNewContext(source, {document, performance: {now: () => h.now},
    window: {open: (...args) => h.windows.push(args)}, showNotice: s => h.notices.push(s)});
  h.press = (key, extra = {}) => {
    const event = {key, prevented: false, preventDefault() { this.prevented = true; }, ...extra};
    h.keydown(event); return event;
  };
  return h;
}

test('Space uses run control, guards rapid presses for 1000 ms and prevents native activation', () => {
  const h = harness();
  assert.equal(h.press(' ').prevented, true);
  h.now = 999; h.press(' ');
  assert.equal(h.get('run').clicks, 1);
  h.now = 1000; h.press(' ');
  assert.equal(h.get('run').clicks, 2);
  h.now = 2500; assert.equal(h.press(' ', {repeat:true}).prevented, true);
  assert.equal(h.get('run').clicks, 2);
  h.press(' '); assert.equal(h.get('run').clicks, 3);
});

test('disabled run control blocks disconnected/busy shortcuts without consuming the guard', () => {
  const h = harness(); h.get('run').disabled = true;
  h.press(' '); assert.equal(h.get('run').clicks, 0);
  h.get('run').disabled = false; h.press(' ');
  assert.equal(h.get('run').clicks, 1);
});

test('typing targets, focused editors and the whole protocol dialog keep their keys', () => {
  const h = harness();
  const editors = [{isContentEditable:true}, ...['input','textarea','select','[role="textbox"]','[role="combobox"]']
    .map(selector => ({closest: selectors => selectors.includes(selector) ? {} : null}))];
  for (const editor of editors) {
    for (const placement of [{target:editor}, {}]) {
      h.document.activeElement = placement.target ? null : editor;
      for (const key of [' ', 'n', 'p', 'f', 't', '?']) assert.equal(h.press(key, placement).prevented, false);
    }
  }
  h.document.activeElement = null; h.get('import-dialog').open = true;
  for (const key of [' ', 'n', 'p', 'f', 't', '?']) assert.equal(h.press(key).prevented, false);
  assert.equal(h.get('run').clicks, 0); assert.equal(h.windows.length, 0);
});

test('modifiers, IME and already handled events are ignored; unknown keys retain defaults', () => {
  const h = harness();
  for (const flag of ['ctrlKey','metaKey','altKey','isComposing','defaultPrevented']) {
    for (const key of [' ', 'n', 'p', 'f', 't', '?']) assert.equal(h.press(key, {[flag]:true}).prevented, false);
  }
  assert.equal(h.press('x').prevented, false);
});

test('N/P select tabs, F enters and exits full screen, T opens an isolated trainer tab', () => {
  const h = harness();
  h.press('N'); h.press('p');
  assert.equal(h.get('note-tab').clicks, 1); assert.equal(h.get('protocol-tab').clicks, 1);
  h.press('f'); assert.ok(h.document.fullscreenElement);
  h.press('F'); assert.equal(h.document.fullscreenElement, null);
  h.press('T'); assert.deepEqual(h.windows, [['/trainer', '_blank', 'noopener']]);
  for (const key of ['n','p','f','t','?']) h.press(key, {repeat:true});
  assert.equal(h.windows.length, 1); assert.equal(h.get('hotkeys-dialog').open, false);
});

test('help toggles by question mark or footer, blocks actions, and closes with its button', () => {
  const h = harness(); h.press('?', {shiftKey:true});
  assert.equal(h.get('hotkeys-dialog').open, true);
  for (const key of [' ', 'n', 'p', 'f', 't']) h.press(key);
  assert.equal(h.get('run').clicks, 0); assert.equal(h.get('note-tab').clicks, 0);
  assert.equal(h.windows.length, 0); assert.equal(h.document.fullscreenElement, null);
  h.press('?'); assert.equal(h.get('hotkeys-dialog').open, false);
  h.get('hotkeys-hint').click(); assert.equal(h.get('hotkeys-dialog').open, true);
  h.get('hotkeys-close').click(); assert.equal(h.get('hotkeys-dialog').open, false);
});

test('unsupported or rejected fullscreen requests show a notice without throwing', async () => {
  const h = harness(); h.document.documentElement.requestFullscreen = undefined;
  h.press('f'); assert.equal(h.notices.length, 1);
  h.document.documentElement.requestFullscreen = () => Promise.reject(new Error('denied'));
  h.press('f'); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.notices.length, 2);
});
