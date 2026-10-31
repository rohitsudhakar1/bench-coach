// Installed inside the sandboxed note document; all playback starts muted and paused.
(() => {
  for (const video of document.querySelectorAll('video')) { video.muted = true; video.defaultMuted = true; video.autoplay = false; video.pause(); }
  const data = document.getElementById('vids'), original = document.getElementById('player');
  if (!data || !original) return;
  let names;
  try { names = JSON.parse(data.textContent); } catch { return; }
  if (!Array.isArray(names)) return;
  const source = new URL(original.getAttribute('src') || '', document.baseURI);
  source.pathname = source.pathname.slice(0, source.pathname.lastIndexOf('/') + 1); source.search = ''; source.hash = '';
  const area = document.createElement('div'); area.style.cssText = 'display:grid;gap:10px;min-width:0';
  original.replaceWith(area);
  const players = new Map();
  for (const [index,name] of names.slice(0,2).entries()) {
    if (typeof name !== 'string') continue;
    const video = document.createElement('video'); video.controls = true; video.muted = true; video.defaultMuted = true; video.preload = 'metadata'; video.style.width = '100%'; video.src = new URL(name,source).href;
    const label = document.createElement('p'); label.textContent = `Camera ${index+1}`;
    const status = document.createElement('small'); video.onerror = () => { status.textContent = 'Recording unavailable. You can still read the note.'; };
    area.append(label,video,status); players.set(name,video);
  }
  document.addEventListener('click', event => {
    const stamp = event.target.closest('[data-off]'); if (!stamp) return;
    event.preventDefault(); event.stopImmediatePropagation();
    let offsets; try { offsets = JSON.parse(stamp.dataset.off); } catch { return; }
    for (const [name,video] of players) {
      const offset = offsets[name]; if (!Number.isFinite(offset) || offset < 0 || video.error) continue;
      const seek = () => { video.pause(); video.currentTime = Number.isFinite(video.duration) ? Math.min(offset,video.duration) : offset; };
      video.onloadedmetadata = seek; if (video.readyState >= 1) seek();
    }
  },true);
})();
