'use strict';
const benchPrefix = (location.pathname || '').match(/^\/benches\/[^/]+/)?.[0] || '';
const $ = id => document.getElementById(id);
let state = null, connected = false, busy = false, previousRun = null, previousRecording = null;
let noteKey = '', chatKey = '', noteAttempt = 0, refreshActive = false, refreshAgain = false;
const names = ['app', 'capture', 'vision', 'angle', 'coach', 'note'];
for (const name of names) {
  const item = document.createElement('span'); item.className = 'part off'; item.id = `part-${name}`;
  const dot = document.createElement('i'); dot.className = 'dot';
  item.append(dot, document.createTextNode(name === 'app' ? 'hub' : name)); $('parts').append(item);
}
async function api(path, options) {
  const response = await fetch(benchPrefix + path, {cache: 'no-store', ...options});
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function showNotice(message) { $('notice').textContent = message || ''; $('notice').hidden = !message; }
function tab(which) {
  for (const name of ['protocol', 'note']) {
    $(name + '-tab').setAttribute('aria-selected', String(name === which));
    $(name + '-tab').tabIndex = name === which ? 0 : -1;
    $(name + '-panel').hidden = name !== which;
  }
}
$('protocol-tab').onclick = () => tab('protocol');
$('note-tab').onclick = () => tab('note');
document.querySelector('.tabs').onkeydown = event => {
  if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
    event.preventDefault(); const which = event.key === 'Home' ? 'protocol' : event.key === 'End' ? 'note' : $('protocol-panel').hidden ? 'protocol' : 'note';
    tab(which); $(which + '-tab').focus();
  }
};
function controls() {
  $('run').disabled = !connected || busy || !state;
  $('student').disabled = busy || !!state?.recording;
  $('load').disabled = !connected || busy || !!state?.recording;
  $('run').textContent = busy ? 'Please wait…' : state?.recording ? 'Stop run' : 'Start run';
  $('run').classList.toggle('stop', !!state?.recording);
}
function render() {
  if (!state) return;
  const stopped = !state.recording && state.run && (previousRecording === true || previousRun !== state.run || previousRecording === null);
  const started = state.recording && (previousRun !== state.run || previousRecording === false);
  if (started) tab('protocol');
  if (stopped) tab('note');
  previousRun = state.run; previousRecording = state.recording;
  const steps = state.protocol?.steps || [];
  const index = state.current_step;
  const current = Number.isInteger(index) ? state.steps[String(index)]?.text || steps[index]?.text : null;
  $('current').textContent = state.recording ? current || 'Following your bench work.' : state.run ? 'Run complete. Review your lab note.' : 'Ready at the bench.';
  $('current').title = $('current').textContent;
  $('progress').textContent = state.recording ? Number.isInteger(index) ? `STEP ${index + 1} OF ${steps.length} / IN PROGRESS` : 'BENCH MONITOR / RECORDING' : 'BENCH MONITOR / ' + (state.run ? 'COMPLETE' : 'READY');
  $('run-label').textContent = state.run || 'No active run';
  $('protocol-name').textContent = state.protocol?.name || 'Protocol';
  $('steps').replaceChildren();
  steps.forEach((step, i) => {
    const detail = state.steps[String(i)] || {}, status = detail.status || 'pending';
    const li = document.createElement('li'); li.className = ['current','done','skipped','unsure'].includes(status) ? status : '';
    if (i === index) li.setAttribute('aria-current', 'step');
    const number = document.createElement('span'); number.className = 'number'; number.textContent = status === 'done' ? '✓' : i + 1;
    const body = document.createElement('span'); body.className = 'step-body'; body.textContent = step.text;
    const evidence = document.createElement('span'); evidence.className = 'evidence';
    const label = {'vision':'seen','speech':'said','hint+speech':'seen + said','seen+said':'seen + said'}[detail.evidence] || {'seen':'seen','said':'said','seen+said':'seen + said'}[detail.source];
    evidence.textContent = [status !== 'pending' ? status : '', label || (!step.expect?.length ? 'spoken confirmation' : '')].filter(Boolean).join(' · ');
    if (evidence.textContent) body.append(evidence);
    li.append(number, body); $('steps').append(li);
  });
  $('question').textContent = state.recording && typeof state.question === 'string' ? state.question : '';
  $('question').hidden = !$('question').textContent;
  const angle = state.angle, valid = state.recording && Number.isFinite(angle?.deg);
  $('degrees').replaceChildren(document.createTextNode(valid ? angle.deg.toFixed(1) : '—'));
  const degree = document.createElement('small'); degree.textContent = '°'; $('degrees').append(degree);
  $('needle').style.transform = `rotate(${valid ? Math.max(-90, Math.min(90, angle.deg)) : 0}deg)`;
  $('needle').style.opacity = valid ? 1 : .2;
  document.querySelector('.angle').classList.toggle('bad', valid && angle.ok === false);
  $('angle-status').textContent = valid ? angle.ok === false ? 'Check technique' : 'In range' : 'No reading';
  $('angle-caption').textContent = valid ? `${angle.mode === 'stereo' || angle.calibrated ? 'Measured tilt' : 'Camera estimate'} · limit ${angle.limit ?? 20}°` : 'Waiting for a measurement';
  const nextChat = JSON.stringify(state.chat);
  if (chatKey !== nextChat) {
    chatKey = nextChat;
    $('chat').replaceChildren();
    for (const event of state.chat || []) {
      const p = document.createElement('p'); p.className = 'message' + (event.kind === 'speech' ? ' person' : '');
      const label = document.createElement('small'); label.textContent = `${event.kind === 'speech' ? 'YOU' : 'COACH'} · ${new Date(event.t * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'})}`;
      p.append(label, document.createTextNode(event.detail.text || '')); $('chat').append(p);
    }
    if (!state.chat?.length) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = 'Your questions and the coach’s guidance appear here.'; $('chat').append(p); }
    $('chat').scrollTop = $('chat').scrollHeight;
  }
  if (state.capture?.ok === false) showNotice(state.capture.error);
  else if (!busy) showNotice('');
  const key = JSON.stringify([state.run, state.recording, state.note]);
  if (key !== noteKey) { noteKey = key; renderNote(); }
  controls(); transient(); updateNvidia();
}
function transient() {
  if (!state) return;
  const warning = state.recording && [...(state.warnings || [])].reverse().find(e => Date.now()/1000 - e.t < 15);
  $('warning').textContent = warning?.detail?.text || '';
  $('warning').hidden = !warning;
  for (let cam = 0; cam < 2; cam++) {
    const svg = $('boxes' + cam), detail = state.objects?.[String(cam)], image = $('cam' + cam);
    svg.replaceChildren();
    if (!detail || Date.now()/1000 - detail.t > 3 || image.hidden) continue;
    const width = detail.width || image.naturalWidth || 640, height = detail.height || image.naturalHeight || 480;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    for (const box of detail.boxes || []) {
      if (!Array.isArray(box.box) || box.box.length !== 4 || !box.box.every(Number.isFinite)) continue;
      const [x1,y1,x2,y2] = box.box;
      const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
      for (const [k,v] of Object.entries({x:x1,y:y1,width:Math.max(0,x2-x1),height:Math.max(0,y2-y1),fill:'none',stroke:'#77e6bd','stroke-width':2,'vector-effect':'non-scaling-stroke'})) rect.setAttribute(k,v);
      const label = document.createElementNS(svg.namespaceURI,'text');
      label.setAttribute('x',x1+4); label.setAttribute('y',Math.max(16,y1-6)); label.setAttribute('fill','#b5ffdf'); label.setAttribute('font-size',Math.max(14,width/45)); label.setAttribute('paint-order','stroke'); label.setAttribute('stroke','#0b1016'); label.setAttribute('stroke-width',3); label.textContent = box.label || 'object';
      svg.append(rect,label);
    }
  }
}
async function renderNote() {
  const attempt = ++noteAttempt, run = state.run, note = state.note;
  $('note-badge').hidden = !note;
  for (const id of ['full-note','pdf','zip','benchling','eln','csv']) $(id).hidden = true;
  $('note-content').replaceChildren();
  const message = text => { const p = document.createElement('p'); p.className = 'empty'; p.textContent = text; $('note-content').append(p); };
  if (!note) { message(run && !state.recording ? 'Waiting for the note service to finish this run…' : 'Stop a run to prepare its lab note.'); return; }
  const inline = note.text || note.markdown || note.content;
  if (typeof inline === 'string' && !note.url && !note.path) { const pre = document.createElement('pre'); pre.textContent = inline; $('note-content').append(pre); return; }
  const base = `${benchPrefix}/lab-note/${encodeURIComponent(run)}`;
  for (const [id,format] of [['full-note','html'],['pdf','pdf'],['zip','zip'],['benchling','benchling'],['eln','eln'],['csv','csv']]) { $(id).hidden = false; $(id).href = base + '?format=' + format; }
  message('Loading the lab note…');
  try {
    const response = await fetch(base + '?format=html', {cache:'no-store'});
    if (!response.ok) throw new Error('The note is not available yet.');
    const content = await response.text();
    if (attempt !== noteAttempt) return;
    const frame = document.createElement('iframe'); frame.title = 'Lab note and synchronized recordings'; frame.setAttribute('sandbox','allow-scripts allow-popups'); frame.srcdoc = content;
    $('note-content').replaceChildren(frame);
  } catch {
    if (attempt !== noteAttempt) return;
    $('note-content').replaceChildren(); message('The note is not available yet. The run is saved.');
    const retry = document.createElement('button'); retry.textContent = 'Retry note'; retry.onclick = renderNote; $('note-content').append(retry);
  }
}
async function refresh() {
  if (refreshActive) { refreshAgain = true; return; }
  refreshActive = true;
  try { do { refreshAgain = false; state = await api('/state'); render(); } while (refreshAgain); }
  catch { showNotice('Cannot load hub state. Reconnecting…'); }
  finally { refreshActive = false; }
}
function socket() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${benchPrefix}/ws`);
  ws.onopen = () => { connected = true; $('connection').textContent = 'Hub connected'; refresh(); controls(); };
  ws.onmessage = () => refresh();
  ws.onclose = () => { connected = false; $('connection').textContent = 'Hub disconnected'; controls(); setTimeout(socket,1500); };
  ws.onerror = () => ws.close();
}
async function pollParts() {
  try {
    const data = await api('/parts');
    for (const name of names) { const value = data.parts[name], element = $('part-' + name); element.className = 'part ' + (value?.status || 'off'); element.title = `${name}: ${value?.message || 'Status unavailable.'}`; element.setAttribute('aria-label',element.title); }
  } catch {
    for (const name of names) { const element = $('part-' + name); element.className = 'part off'; element.title = `${name}: Hub unavailable; cannot check status.`; element.setAttribute('aria-label',element.title); }
  }
  setTimeout(pollParts,2000);
}
$('run').onclick = async () => {
  busy = true; controls();
  try { const result = await api(state.recording ? '/run/stop' : '/run/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(state.recording ? {} : {student:$('student').value.trim()})}); await refresh(); showNotice(result.capture?.ok === false ? result.capture.error : ''); }
  catch (error) { await refresh(); showNotice(error.message); }
  finally { busy = false; controls(); }
};
$('load').onclick = () => { $('import-error').textContent = ''; $('import-dialog').showModal(); };
$('close-dialog').onclick = () => $('import-dialog').close();
$('import-form').onsubmit = async event => {
  event.preventDefault(); $('convert').disabled = true; $('convert').textContent = 'Converting…'; $('import-error').textContent = '';
  try { const result = await api('/protocol', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('protocol-title').value,text:$('protocol-text').value})}); $('import-dialog').close(); await refresh(); tab('protocol'); showNotice(`Protocol loaded using ${result.conversion}. Review the steps before starting.`); }
  catch (error) { $('import-error').textContent = error.message; }
  finally { $('convert').disabled = false; $('convert').textContent = 'Load steps'; }
};
async function cameraConfig() {
  try {
    const config = await api('/config');
    for (let i=0;i<2;i++) {
      const img = $('cam'+i), label = img.closest('.camera').querySelector('.camera-state');
      const connect = () => { img.src = `${config.capture_url}/cam/${i}/mjpeg?view=${Date.now()}`; };
      img.onload = () => { img.hidden = false; label.textContent = 'Live feed'; };
      img.onerror = () => { img.hidden = true; label.textContent = 'Capture unavailable'; setTimeout(connect,5000); };
      connect();
    }
  } catch { setTimeout(cameraConfig,5000); }
}
setInterval(transient,500);
// A periodic snapshot also catches capture results written after the run event.
setInterval(() => { if (connected) refresh(); },5000);
tab('protocol'); socket(); pollParts(); cameraConfig();

let nvidiaRun = null, nvidiaPending = false, nvidiaKey = '';
function updateNvidia() {
  const run = state?.run && !state.recording ? state.run : null;
  if (run !== nvidiaRun) {
    nvidiaRun = run; nvidiaKey = '';
    $('nvidia-tasks').replaceChildren(); $('nvidia-comparison').replaceChildren();
    $('nvidia-status').textContent = run ? 'Checking task export…' : 'Stop a run to export training tasks.';
    $('nvidia-synth-status').textContent = ''; $('nvidia-synth').disabled = true;
    $('nvidia-export').hidden = true;
  }
  pollNvidia();
}
function nvidiaRender(data) {
  const key = JSON.stringify(data);
  if (key === nvidiaKey) return;
  nvidiaKey = key;
  const exp = data.export, synth = data.synth;
  $('nvidia-status').textContent = exp.status === 'ready'
    ? `Rules solver: ${exp.score.mean ?? '—'} / 1 across ${exp.score.tasks} tasks.`
    : exp.status === 'error' ? exp.message : exp.status === 'idle' ? 'No export yet.' : `Task export ${exp.status}…`;
  $('nvidia-export').hidden = !['idle','error'].includes(exp.status);
  $('nvidia-tasks').replaceChildren();
  for (const task of exp.tasks || []) {
    const p = document.createElement('p'), checker = document.createElement('small'), link = document.createElement('a');
    checker.textContent = `Checker: ${task.kind} — ${task.checker}`;
    link.textContent = 'Download task ZIP';
    link.href = `${benchPrefix}/nvidia/${encodeURIComponent(nvidiaRun)}/tasks/${encodeURIComponent(task.id)}.zip`;
    p.append(document.createTextNode(task.question), checker, link); $('nvidia-tasks').append(p);
  }
  $('nvidia-synth').disabled = ['queued','running','ready'].includes(synth.status);
  $('nvidia-synth-status').textContent = synth.status === 'ready'
    ? `${synth.manifest.synthetic_runs} synthetic runs. ${synth.manifest.engine_detail}. ${synth.manifest.notes}.`
    : synth.status === 'error' ? synth.message : synth.status === 'idle' ? '' : `Synthetic runs ${synth.status}…`;
  $('nvidia-comparison').replaceChildren();
  for (const [name, recorded] of Object.entries(synth.recorded || {})) {
    const title = document.createElement('p'); title.textContent = `${name}: skip rates (recorded uses prior ${synth.manifest.prior}; ${recorded.runs} recorded run).`;
    const table = document.createElement('table'), head = document.createElement('tr');
    for (const label of ['Step','Recorded','Synthetic']) { const th = document.createElement('th'); th.scope = 'col'; th.textContent = label; head.append(th); }
    table.append(head);
    recorded.skip_rate.forEach((value, index) => {
      const row = document.createElement('tr');
      for (const text of [index + 1, `${(value*100).toFixed(1)}%`, `${((synth.synthetic[name]?.skip_rate[index] ?? 0)*100).toFixed(1)}%`]) {
        const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
      }
      table.append(row);
    });
    $('nvidia-comparison').append(title, table);
  }
}
async function pollNvidia() {
  if (!nvidiaRun || nvidiaPending) return;
  const run = nvidiaRun; nvidiaPending = true;
  try { const data = await api(`/nvidia/${encodeURIComponent(run)}`); if (run === nvidiaRun) nvidiaRender(data); }
  catch (error) { if (run === nvidiaRun) $('nvidia-status').textContent = error.message; }
  finally { nvidiaPending = false; }
}
async function startNvidia(kind) {
  const run = nvidiaRun;
  if (!run) return;
  $(kind === 'synth' ? 'nvidia-synth' : 'nvidia-export').disabled = true;
  try { const data = await api(`/nvidia/${encodeURIComponent(run)}/${kind}`, {method:'POST'}); if (run === nvidiaRun) nvidiaRender(data); }
  catch (error) { if (run === nvidiaRun) $('nvidia-' + (kind === 'synth' ? 'synth-status' : 'status')).textContent = error.message; }
  finally { if (kind === 'export') $('nvidia-export').disabled = false; nvidiaKey = ''; pollNvidia(); }
}
$('nvidia-export').onclick = () => startNvidia('export');
$('nvidia-synth').onclick = () => startNvidia('synth');
setInterval(pollNvidia, 1500);

if (benchPrefix) {
  document.title = `Bench ${state?.bench || benchPrefix.split('/').pop()} · Bench Coach`;
  document.querySelector('.brand').href = '/class';
  document.querySelector('.brand small').textContent = `Bench ${benchPrefix.split('/').pop()}`;
  document.querySelector('a[href="/calibrate"]').href = benchPrefix + '/calibrate';
}
