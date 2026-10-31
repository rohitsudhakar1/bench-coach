# Presenter hotkeys

No event contract changes proposed or made.

Added a bench-only keyboard handler, a compact help dialog and a footer hint.
Space clicks the existing Start/Stop button, so it shares the app's request,
error and busy behavior. A monotonic clock rejects another Space for 1,000 ms.
Repeated keydown events are ignored. Disabled controls cannot start requests.
N and P select the existing tabs, F toggles full screen, T opens `/trainer`
with no opener, and ? toggles help. Escape and the close button dismiss help.

All shortcuts pause while editing or while the protocol dialog is open.
Help also blocks run and navigation shortcuts until dismissed. Modified browser
shortcuts and IME composition are left alone. Full-screen failures show a notice.
No sound, service changes, downloads, new dependencies or API keys were used.
All deliverable edits are under `app/` and remain uncommitted.

## Checks

- `node --test app/tests/*.test.cjs`: 14 passed, including 7 hotkey tests.
  Covers the 999/1,000 ms boundary, held keys, disabled controls, editing and
  dialog protection, modifier keys, tab selection, trainer isolation, full-screen
  entry/exit/failure and help behavior.
- `node --check app/static/hotkeys.js`: passed.
- `BENCH_PY` environment's Python, `-m pytest app/tests -q -p no:cacheprovider`:
  29 passed, 1 skipped in 3.51 seconds. Sample-dependent coverage was skipped.
- `app/tests/browser_check.py`: attempted, but Chromium exited during launch
  with a sandbox permission denial. No browser assertions or visual checks ran.
  The check now includes shortcut integration and a help screenshot when a browser
  can launch. No server was started; the attempted browser process exited.
- `git diff --check`: passed.
- Required public-path audit: no matches in tracked files. New deliverables were
  also checked for machine paths and credentials; none were found.

## Limits

Actual browser layout, native Escape/focus restoration, full-screen permission
and popup behavior still need the browser check or a manual rehearsal. Shortcuts
only receive events in the bench document, not in the embedded note document.
The one-second guard applies to Space presses; pointer clicks retain existing behavior.

## Git log

Ran `git log --format='%h %an %aI %cI' main..HEAD` before finishing.
Output was empty: no branch commits ahead of main. No commits, history changes,
identity changes or date overrides were made.
