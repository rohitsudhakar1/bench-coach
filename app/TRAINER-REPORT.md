# Trainer report

Contract proposal: document optional `detail.student` (trimmed string, at most 120
characters) on the existing app `run` start event. Implemented for the requested typed
name; no new event kind. `docs/EVENTS.md` is unchanged.

## Delivered

`/trainer` lists saved runs with student, protocol, date, length, done/skipped/unsure
and unfinished steps, reused-tip problems, over-limit angle readings and note links.
Protocol and student filters also narrow the missed-step summary. Different instruction
versions are kept separate. Typed names are persisted on Start and take precedence in
the note header. Spoken names retain the existing note parser's behavior.

The view reads only run-folder events and saved note JSON. It reuses the note rules for
unsaved notes without opening video or external protocol paths. It writes nothing and
adds no database or packages. Corrupt rows and notes have visible notices; symlinked
inputs are ignored. Laptop layout uses a compact table; phone layout stacks filters
and scrolls the table within its panel.

## Checks

- `python -m pytest app/tests note/tests -q -p no:cacheprovider`: 54 passed,
  2 skipped in 9.45 seconds. Skips require optional sample footage.
- Four trainer tests use three fake run folders: saved-note and event fallback,
  typed and spoken names, filter intersection, missed-step denominators, edited
  protocols, unfinished steps, post-stop exclusion, malformed rows/notes,
  symlinks, unchanged input files, routes, name validation and rendered headers.
- `node --check app/static/trainer.js` and `node --check app/static/app.js`: passed.
- `python -m app.tests.trainer_browser_check`: Chromium could not launch because
  the sandbox denied its process rendezvous. No browser assertions ran. The check
  is included for 1440×900, 1280×800 and 390×844, filters, summary and note links.
  Visual fit remains unverified here.
- Public-path tracked-file scan: no matches. No sound, downloads or servers started.

## Limits and choices

Counts inherit the note's evidence rules; they are not independent technique measurements.
Tilts count readings, not distinct incidents. Reuse counts cross-tube draws, not repeated
coach warnings. Missed includes unsure and unfinished, with separate counts visible.
Active runs participate; refresh to update results. Saved notes are authoritative for
step and technique counts and may be stale until rebuilt. Missing protocol snapshots
cannot supply step counts. Old note headers require rebuilding to show a later typed
name. Note links use the existing proxy and require the note service. Large archives
are scanned per request; no archive-scale performance measurement was made.

## Git history

`git log --format='%h %an %aI %cI' main..HEAD` produced no output.
All changes are uncommitted. No history or Git identity was changed.
