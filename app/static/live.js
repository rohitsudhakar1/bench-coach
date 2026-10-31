'use strict';
// The live view page: the drawn picture comes as MJPEG from the live process; this file only
// fills the rates, the legend, the steps and the coach lines from /data twice a second.
const $ = (id) => document.getElementById(id);
const COLORS = {pipette: '#ffc800', 'pipette tip': '#ff7800', 'tip box': '#008cff', tube: '#3cdc3c',
  'tube rack': '#00ffa0', plate: '#c800ff', waste: '#ff5050'};

function text(el, value) { if (el.textContent !== value) el.textContent = value; }

function chip(el, label, value, goodAt) {
  text(el, label);
  el.className = 'chip ' + (value >= goodAt ? 'good' : value > 0 ? 'warn' : '');
}

function renderSteps(hub) {
  const ol = $('steps');
  if (!hub.ok) { ol.innerHTML = '<li class="muted">Hub not reachable. The picture still works.</li>'; return; }
  ol.replaceChildren(...hub.steps.map((s) => {
    const li = document.createElement('li');
    li.className = s.index === hub.current ? 'current' : (s.status || '');
    li.textContent = s.text;
    if (s.evidence) { const ev = document.createElement('span'); ev.className = 'ev'; ev.textContent = s.evidence; li.append(ev); }
    return li;
  }));
}

function renderCoach(hub) {
  const box = $('coach');
  if (!hub.coach.length) { box.innerHTML = '<p class="muted">Coach lines appear here.</p>'; return; }
  box.replaceChildren(...hub.coach.slice().reverse().map((c) => {
    const p = document.createElement('p'); p.className = c.who;
    const who = document.createElement('span'); who.className = 'who'; who.textContent = c.who;
    p.append(who, document.createTextNode(c.text)); return p;
  }));
}

function render(d) {
  chip($('fps-picture'), `picture ${d.fps.picture.toFixed(1)} fps`, d.fps.picture, 20);
  chip($('fps-detect'), `detections ${d.fps.detections.toFixed(1)}/s`, d.fps.detections, 3);
  text($('source'), d.source.fallback ? `fallback video ${d.source.name}` : `source ${d.source.name}`);
  $('source').className = 'chip ' + (d.source.fallback ? 'warn' : '');
  const names = [...new Set(d.objects.filter((o) => o.label !== 'hand').map((o) => o.label))];
  const hands = d.hands.map((h) => `${h.side} hand`);
  $('legend').replaceChildren(...names.map((n) => {
    const s = document.createElement('span'); s.style.setProperty('--c', COLORS[n] || '#ccc'); s.textContent = n; return s;
  }), ...hands.map((n) => { const s = document.createElement('span'); s.style.setProperty('--c', '#fff'); s.textContent = n; return s; }));
  const hub = d.hub;
  const cur = hub.steps.find((s) => s.index === hub.current);
  text($('current'), !hub.ok ? 'Hub not connected' : cur ? cur.text : hub.run ? 'Run in progress' : 'Ready at the bench');
  $('question').hidden = !hub.question; text($('question'), hub.question || '');
  $('warning').hidden = !hub.warnings.length; text($('warning'), hub.warnings[hub.warnings.length - 1] || '');
  text($('protocol'), hub.protocol || '');
  renderSteps(hub); renderCoach(hub);
}

async function poll() {
  try {
    const r = await fetch('/data', {cache: 'no-store'});
    if (r.ok) { const d = await r.json(); render(d); $('nopicture').hidden = d.frame.width > 0; }
  } catch (e) { text($('fps-picture'), 'live view not reachable'); }
  setTimeout(poll, 500);
}

const img = $('picture');
function connect() { img.src = '/stream.mjpeg?' + Date.now(); }
img.addEventListener('error', () => setTimeout(connect, 1000));
connect();
poll();
