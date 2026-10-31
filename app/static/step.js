(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let state = null, socket = null, connected = false, synced = false;
  let refreshing = null, dirty = false, sending = false, pending = null;
  let postError = null;
  const key = s => s?.recording && Number.isInteger(s.current_step)
    ? `${s.run}:${s.current_step}` : null;

  function render() {
    const steps = state?.protocol?.steps || [];
    const index = state?.current_step;
    const active = Boolean(state?.recording && Number.isInteger(index) && steps[index]);
    el('protocol').textContent = state?.protocol?.name || '';
    el('position').textContent = active ? `Step ${index + 1} of ${steps.length}` : 'Current step';
    const finished = steps.length > 0 && steps.every((_, i) =>
      ['done', 'skipped', 'unsure'].includes(state?.steps?.[i]?.status));
    el('current').textContent = active ? steps[index].text
      : !state ? 'Waiting for the hub…'
      : !state.recording ? (state.run ? 'Run stopped.' : 'Ready when you are.')
      : finished ? 'Steps finished. Review the run on the bench page.' : 'Waiting for the coach…';
    el('next').textContent = active ? (steps[index + 1]?.text || 'This is the last step.') : '—';
    const warning = state?.recording ? state.warnings?.at(-1)?.detail?.text : '';
    el('warning-box').hidden = !warning;
    el('warning').textContent = warning || '';
    const question = state?.recording ? state.question : null;
    el('question').textContent = typeof question === 'string' ? question : question?.text || '';
    el('question').hidden = !el('question').textContent;
    el('done').disabled = !active || !connected || !synced || sending || pending === key(state);
  }

  function refresh() {
    dirty = true;
    if (refreshing) return refreshing;
    refreshing = (async () => {
      while (dirty) {
        dirty = false;
        const currentSocket = socket;
        try {
          const response = await fetch('/state', {cache: 'no-store', signal: AbortSignal.timeout(8000)});
          if (!response.ok) throw new Error('state');
          const next = await response.json();
          if (socket !== currentSocket || !connected) continue;
          const oldKey = key(state);
          state = next;
          synced = true;
          if (oldKey !== key(state)) postError = null;
          if (pending && pending !== key(state)) pending = null;
          if (!sending && !pending) {
            el('feedback').textContent = postError || (!state.recording ? 'Start a run from the bench page.'
              : key(state) ? 'Confirm when you finish this step.' : 'Waiting for the coach.');
          }
          el('connection').textContent = 'Connected';
          render();
        } catch {
          synced = false;
          el('connection').textContent = 'Unable to refresh';
          el('feedback').textContent = 'Reconnecting to the hub…';
          render();
          socket?.close();
        }
      }
    })().finally(() => { refreshing = null; });
    return refreshing;
  }

  function connect() {
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`);
    socket.onopen = () => { connected = true; synced = false; refresh(); };
    socket.onmessage = () => refresh();
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      connected = synced = false;
      el('connection').textContent = 'Disconnected · retrying';
      render();
      setTimeout(connect, 1500);
    };
  }

  el('done').addEventListener('click', async () => {
    if (el('done').disabled) return;
    const pressed = key(state);
    postError = null;
    sending = true;
    render();
    // Recheck after a phone wakes or another student advances the shared run.
    await refresh();
    if (!connected || !synced || pressed !== key(state)) {
      sending = false;
      render();
      return;
    }
    pending = pressed;
    el('feedback').textContent = 'Sending confirmation…';
    try {
      const response = await fetch('/events', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        signal: AbortSignal.timeout(8000),
        body: JSON.stringify({t: Date.now() / 1000, source: 'app', kind: 'speech',
          step: state.current_step, detail: {text: 'done'}})
      });
      if (!response.ok) {
        pending = null;
        postError = 'Confirmation rejected. Try again.';
        el('feedback').textContent = postError;
      } else if (pending === pressed) {
        el('feedback').textContent = 'Confirmation sent. Waiting for the coach…';
      }
    } catch {
      // A lost response may still mean accepted. Never resend automatically.
      el('feedback').textContent = 'Delivery uncertain. Check the bench page before confirming again.';
    } finally {
      sending = false;
      render();
      refresh();
    }
  });
  connect();
})();
