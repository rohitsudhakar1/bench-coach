# ELN export report

Contract proposal: document three additional note downloads, `/note/{run}.benchling`,
`/note/{run}.eln`, and `/note/{run}.csv`. No event, clock or port changes.
`docs/EVENTS.md` is unchanged.

## Delivered

Benchling-ready HTML, an eLabFTW-targeted .eln archive, and plain step CSV are
available beside Download PDF in the Lab note tab. The brief explicitly requires
note-service and app integration, so changes are confined to `note/` and `app/`.
All files remain uncommitted.

The exports reuse saved note data. A missing note is built with rules, without a
model call or an event post. Export builders make no network requests. The UI
uses the existing local hub proxy; no ELN account or API is contacted.

HTML has a results table with steps, outcomes, evidence and times, plus technique,
speech, problems and an attachment inventory. CSV has seven columns and one row
per step, preserving unknown times as blank and camera offsets as JSON. Times
are evidence/status timestamps, not invented durations. Formula-like CSV text
gets an apostrophe prefix; other formats retain the original text.

The .eln ZIP contains one root folder, RO-Crate metadata and one experiment
Dataset, identified by `genre: experiment`. It includes HTML, CSV, note JSON,
available event/timing logs and safe referenced key frames. Recordings are listed
but excluded. File entities record MIME types, byte counts and SHA-256 hashes.
Root and experiment references describe the import contents. External paths and
symlinks escaping the run directory are excluded from attachment collection.
No packages were added; sources and licences are in `note/README.md`.

The implementation follows the [ELN specification 1.2+20260923](https://github.com/TheELNConsortium/TheELNFileFormat/blob/master/SPECIFICATION.md),
[RO-Crate 1.2](https://www.researchobject.org/ro-crate/specification/1.2/) and
[eLabFTW import documentation](https://doc.elabftw.net/import-export.html).
The earlier note reports identified playback, timing and evidence limitations;
these exports reuse the rebuilt note's recorded values without claiming better
recognition or reintroducing the earlier timing format.

## Checks

- `$BENCH_PY -m pytest note/tests app/tests -q`: 66 passed, 2 skipped in 21.03 s.
  This run included the first five export tests. Sample-dependent tests skipped.
- Final `$BENCH_PY -m pytest note/tests/test_elnexport.py -q`: 6 passed in 5.52 s,
  including the later empty-note case.
- Updated proxy tests: `$BENCH_PY -m pytest app/tests/test_hub.py -q`:
  11 passed in 0.99 s.
- `node --test app/tests/eln-downloads.test.cjs app/tests/note-player.test.cjs`:
  3 passed. Checks all three links, changing runs, empty/inline notes, and existing
  muted video behavior.
- `node --check app/static/app.js` and `git diff --check`: passed.
- Fake-run checks cover all 8 step rows and 6 key frames, exact event bytes,
  ZIP CRC integrity, one root, unique graph IDs, root/experiment references,
  descriptor context and conformance, author reference, file sizes and hashes.
  Other cases cover HTML escaping, CSV quoting/Unicode/formulas, unknown times,
  empty steps, excluded recordings, escaping symlinks, missing runs, and downloads
  without PDF/model/event-post dependencies.
- The existing server-free browser check now checks the new buttons at three
  viewport sizes. Chromium launch failed with a sandbox Mach-port permission
  denial before page load. No visual-layout result is claimed.
- The required public-repository absolute-home-path scan returned no matches.
  New source and report files were also checked.

No server was started; no camera, microphone, sound playback or hosted model was
used. Documentation fetching was only for format research, outside export code.

## Limits

Live Benchling paste and eLabFTW import were not tested. The HTML is intended for
copying its rendered content; attachments must be uploaded separately when
pasting. Offline structural checks are not a full JSON-LD/schema validator or a
vendor import acceptance test. Archives are held in memory and exclude video and
audio. Missing optional attachments are omitted. No licence is assigned to the
user's experimental data. Existing note evidence limitations remain unchanged.

## Git log

Ran `git log --format='%h %an %aI %cI' main..HEAD` before finishing.
Output was empty: no branch commits beyond main. No commits, pushes, date
variables, author changes or history rewrites were made.
