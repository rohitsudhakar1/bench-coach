const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const code = source.slice(source.indexOf('async function renderNote()'), source.indexOf('async function refresh()'));
test('notebook links follow the displayed run and hide for empty or inline notes', async () => {
  const elements = {};
  const element = () => ({hidden: false, append(){}, replaceChildren(){}, setAttribute(){}});
  const context = {noteAttempt: 0, noteOff: false, benchPrefix: '', state: {run: 'fake-run', note: {path: '/note/fake-run'}},
    $: id => elements[id] ||= element(), document: {createElement: element},
    fetch: async () => ({ok: true, text: async () => '<p>Note</p>'})};
  vm.createContext(context);
  vm.runInContext(code, context);
  await context.renderNote();
  for (const id of ['benchling','eln','csv']) {
    assert.equal(elements[id].hidden, false);
    assert.equal(elements[id].href, `/lab-note/fake-run?format=${id}`);
    assert.match(fs.readFileSync('app/static/index.html', 'utf8'), new RegExp(`id="${id}"`));
  }
  context.state.run = 'second-run';
  await context.renderNote();
  assert.equal(elements.eln.href, '/lab-note/second-run?format=eln');
  for (const note of [null, {text: 'Synthetic demo'}]) {
    context.state.note = note;
    await context.renderNote();
    for (const id of ['benchling','eln','csv']) assert.equal(elements[id].hidden, true);
  }
});
test('a stopped run without a note service says so instead of waiting forever', async () => {
  const elements = {}, texts = [];
  const element = () => ({hidden: false, append(child){ if (child?.textContent) texts.push(child.textContent); }, replaceChildren(){}, setAttribute(){}});
  const context = {noteAttempt: 0, noteOff: false, benchPrefix: '', state: {run: 'r1', recording: false, note: null},
    $: id => elements[id] ||= element(), document: {createElement: element}};
  vm.createContext(context);
  vm.runInContext(code, context);
  await context.renderNote();
  assert.match(texts.pop(), /Waiting for the note service/);
  context.noteOff = true;
  await context.renderNote();
  assert.match(texts.pop(), /note service is not running/);
});
