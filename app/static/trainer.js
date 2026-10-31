'use strict';
const $ = id => document.getElementById(id);
let requestNumber = 0;
function cell(row, text) { const td = document.createElement('td'); td.textContent = text; row.append(td); return td; }
function small(parent, text) { const el = document.createElement('small'); el.textContent = text; parent.append(el); }
async function refresh() {
  const current = ++requestNumber;
  $('status').textContent = 'Loading saved runs…';
  try {
    const response = await fetch('/trainer/runs?' + new URLSearchParams({protocol:$('protocol').value, student:$('student').value.trim()}), {cache:'no-store'});
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (current !== requestNumber) return;
    const selected = $('protocol').value;
    $('protocol').replaceChildren(new Option('All protocols', ''));
    for (const protocol of data.protocols) $('protocol').add(new Option(protocol, protocol));
    $('protocol').value = selected;
    $('status').textContent = `${data.runs.length} of ${data.total} runs${data.runs.length ? '' : ' · No runs match these filters'}.`;
    $('runs').replaceChildren();
    for (const run of data.runs) {
      const row = document.createElement('tr');
      const name = cell(row, run.student || 'Not recorded'); small(name, run.run); small(name, `${run.status} · ${run.source}`);
      for (const issue of run.issues) small(name, issue);
      cell(row, run.protocol_name);
      cell(row, run.start === null ? '—' : new Date(run.start * 1000).toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}));
      cell(row, run.duration === null ? '—' : `${Math.floor(run.duration / 60)}m ${Math.floor(run.duration % 60)}s`);
      for (const status of ['done','skipped','unsure']) cell(row, run.counts[status]);
      cell(row, run.counts['not finished'] + run.counts['not reached']);
      cell(row, run.reused_tips); cell(row, run.tilts_over);
      const note = cell(row, run.note_url ? '' : 'Not ready');
      if (run.note_url) { const link = document.createElement('a'); link.href = run.note_url; link.textContent = 'Open note'; note.append(link); }
      $('runs').append(row);
    }
    $('missed').replaceChildren();
    $('summary-help').textContent = selected ? 'Within the current student filter. Missed = skipped, unsure, or unfinished. Rates use runs containing that exact step; edited instructions are listed separately.' : 'Select a protocol to compare its steps.';
    for (const step of data.most_missed) {
      const li = document.createElement('li'); li.value = step.index + 1; li.append(document.createTextNode(step.text));
      const rate = document.createElement('strong'); rate.textContent = `${step.missed}/${step.runs} missed · ${Math.round(step.rate * 100)}%`; li.append(rate);
      small(li, `${step.skipped} skipped · ${step.unsure} unsure · ${step.unfinished} unfinished`); $('missed').append(li);
    }
  } catch { if (current === requestNumber) $('status').textContent = 'Could not load runs. Press Refresh runs to retry.'; }
}
$('filters').onsubmit = event => { event.preventDefault(); refresh(); };
$('protocol').onchange = refresh;
let timer;
$('student').oninput = () => { clearTimeout(timer); timer = setTimeout(refresh, 200); };
refresh();
