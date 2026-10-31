'use strict';
const el = id => document.getElementById(id);
let current = null;
let busy = false;
let connected = false;
function buttons() {
  const computing = current?.verdict === 'computing';
  el('capture').disabled = busy || !connected || !current?.can_capture || (!current.count && !el('level').checked);
  el('compute').disabled = busy || !connected || computing || !current || current.count < 12;
  el('use').disabled = busy || !connected || current?.verdict !== 'good' || current.used;
  el('reset').disabled = busy || !connected || computing;
}
function render(state) {
  current = state;
  for (let i = 0; i < 2; i++) {
    const view = state.views[i];
    el(`view${i}`).hidden = !view;
    el(`empty${i}`).hidden = !!view;
    if (view) el(`view${i}`).src = `data:image/jpeg;base64,${view.image}`;
    const stale = view && Date.now() / 1000 - view.t > 1;
    el(`found${i}`).textContent = !view ? 'Unavailable' : stale ? 'Frame is stale' : view.found ? 'Board found' : 'Board not found';
  }
  el('progress').textContent = `${state.count} of 12 pairs · ${state.count ? 'vary the angle' : 'start with the level board'}`;
  el('level-label').hidden = state.count > 0;
  el('message').textContent = state.message;
  el('verdict').textContent = ({collecting: 'Collecting pairs', computing: 'Computing…', good: 'Good fit', redo: 'Redo'})[state.verdict];
  el('metrics').textContent = state.result
    ? `Reprojection error (RMS): stereo ${state.result.rms_px.toFixed(3)} px · left ${state.result.left_rms_px.toFixed(3)} px · right ${state.result.right_rms_px.toFixed(3)} px. Worst image: ${Math.max(...state.result.per_pair.flatMap(p => [p.left_rms_px, p.right_rms_px])).toFixed(3)} px.`
    : state.verdict === 'redo' ? 'Reprojection error: see the fit reason above; unavailable if the poses could not be fitted.' : 'Reprojection error: not computed yet.';
  buttons();
}
async function request(path, body) {
  const response = await fetch(`/calibrate/${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed. Refresh and try again.');
  return data;
}
async function act(path, body) {
  busy = true; buttons(); el('error').textContent = '';
  try { render(await request(path, body)); }
  catch (error) { el('error').textContent = error.message; }
  finally { busy = false; buttons(); }
}
el('level').addEventListener('change', buttons);
el('capture').addEventListener('click', () => act('capture', {revision: current.revision, level: el('level').checked}));
el('compute').addEventListener('click', () => act('compute'));
el('use').addEventListener('click', () => act('use'));
el('reset').addEventListener('click', () => { el('level').checked = false; act('reset'); });
async function poll() {
  if (!busy && !document.hidden) {
    busy = true; buttons();
    try {
      const response = await fetch('/calibrate/state', {cache: 'no-store'});
      if (!response.ok) throw new Error('Hub unavailable');
      connected = true; render(await response.json());
    } catch (_) {
      connected = false;
      el('message').textContent = 'Hub unavailable. Reconnecting…';
      for (let i = 0; i < 2; i++) { el(`view${i}`).hidden = true; el(`empty${i}`).hidden = false; el(`found${i}`).textContent = 'Unavailable'; }
    } finally { busy = false; buttons(); }
  }
  setTimeout(poll, 600);
}
poll();
