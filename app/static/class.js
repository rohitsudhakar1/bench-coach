'use strict';
const grid = document.getElementById('benches');
const cards = new Map();
function line(parent, tag, text, cls = '') {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = cls;
  parent.append(element);
  return element;
}
async function refreshClass() {
  try {
    const response = await fetch('/class/state', {cache:'no-store', signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error('offline');
    const rows = await response.json();
    for (const row of rows) {
      let card = cards.get(row.bench);
      if (!card) { card = document.createElement('a'); cards.set(row.bench, card); }
      card.className = 'bench' + (row.attention ? ' attention' : '');
      card.href = row.url;
      card.replaceChildren();
      line(card, 'h2', `Bench ${row.bench}`);
      line(card, 'p', row.student || 'No student name', 'student');
      if (row.attention) {
        const label = line(card, 'p', 'Trainer needed', 'attention-label');
        const dot = document.createElement('span'); dot.className = 'dot'; dot.setAttribute('aria-hidden', 'true'); label.prepend(dot);
      }
      line(card, 'p', row.recording ? (row.instruction || 'Waiting for the coach') : (row.run ? 'Run stopped' : 'Ready to start'));
      if (row.recording && row.current_step !== null) line(card, 'p', `Step ${row.current_step + 1}`, 'muted');
      if (row.question) line(card, 'p', typeof row.question === 'string' ? row.question : row.question.text || 'Coach needs a reply');
      if (row.warning) line(card, 'p', row.warning, 'warning');
      grid.append(card);
    }
    document.getElementById('status').textContent = `${rows.length} benches · Live · Attention first`;
  } catch (_) {
    document.getElementById('status').textContent = 'Connection lost. Showing last update; retrying…';
  } finally { setTimeout(refreshClass, 1000); }
}
refreshClass();
