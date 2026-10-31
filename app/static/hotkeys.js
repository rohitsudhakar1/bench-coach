'use strict';
(() => {
  const get = id => document.getElementById(id);
  const help = get('hotkeys-dialog');
  let lastRunPress = -Infinity;
  const editing = element => element && (element.isContentEditable ||
    element.closest?.('input, textarea, select, [role="textbox"], [role="combobox"]'));
  const toggleHelp = () => help.open ? help.close() : help.showModal();
  get('hotkeys-hint').addEventListener('click', toggleHelp);
  get('hotkeys-close').addEventListener('click', () => help.close());

  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey ||
        editing(event.target) || editing(document.activeElement) || get('import-dialog').open) return;
    const key = event.key.toLowerCase();
    if (![' ', 'n', 'p', 'f', 't', '?'].includes(key)) return;
    // Prevent native Space button activation and scrolling even for ignored repeats.
    event.preventDefault();
    if (event.repeat) return;
    if (key === '?') { toggleHelp(); return; }
    if (help.open) return;
    if (key === ' ') {
      const now = performance.now();
      if (get('run').disabled || now - lastRunPress < 1000) return;
      lastRunPress = now;
      get('run').click();
    } else if (key === 'n' || key === 'p') {
      get(key === 'n' ? 'note-tab' : 'protocol-tab').click();
    } else if (key === 't') {
      window.open('/trainer', '_blank', 'noopener');
    } else if (key === 'f') {
      try {
        const operation = document.fullscreenElement ? document.exitFullscreen() :
          document.documentElement.requestFullscreen();
        Promise.resolve(operation).catch(() => showNotice('Full screen is unavailable in this browser.'));
      } catch {
        showNotice('Full screen is unavailable in this browser.');
      }
    }
  });
})();
