# Step label editor

Proposed changes to docs/EVENTS.md: none. The JSONL extension is local to the offline
renderer; no live event contract or service port changed.

## What works

`python -m scripts.label_steps --video run.mov --protocol protocol.txt --out labels.jsonl`
starts a loopback-only page on 8810. It loads numbered text or YAML through the same
protocol loader as the renderer. The page has silent video playback, a scrubber, step
buttons, number keys 1–9, Space, frame arrows, undo, reused-tip/skipped-step warnings,
atomic Save and reopen. Later steps are clickable. Repeating a step replaces its time;
skipping a step and its warning undo together. Leaving with unsaved edits raises the
browser's normal unsaved-changes prompt. Save errors keep the edits in the page.

The backend rejects invalid times, duplicate/out-of-range step indices, unknown warning
kinds, mismatched saved protocol text and attempts to overwrite the source inputs.
Video responses support byte ranges. The editor needs no other Bench Coach service,
model, API key or network. No dependencies were installed.

MOV/HEVC and other non-H.264 formats get a temporary silent H.264 MP4 before serving.
H.264 MP4 uses the original unless `--transcode` is supplied. The source is never changed.
ffprobe supplies presentation timestamps for frame stepping, including variable frame
rate. The preview is removed on shutdown, including interruption during conversion.
Ports are restricted to 8800–8899. The README has the record → label → render checklist.

The renderer now accepts explicit `step` and `warning` rows as well as its original
`tip_on`/`draw`/`dispense` action rows. Direct step annotations are necessary for things
like opening a bottle, which the action-hint schema cannot express. Explicit step labels
replace rule-based protocol progression for that render: unlabelled steps stay unfinished,
while detected boxes and hands remain visible. This avoids a false visual hint overwriting
an annotated outcome or completing an unreviewed step. Warning-only files retain normal
rule-based progression. Existing sample action files still render unchanged, but this
editor refuses to overwrite them; choose a new output file.

Completed/skipped steps and warnings say hand-labelled. The renderer also corrects the
saved JSON/Markdown/HTML note attribution and restores the explicit timestamps after
note inference. Previously only the video card received that correction. A shortened
render does not include future manual outcomes in its note.

## Checks and measured results

- Focused suite: 65 passed in 0.90 s across `test_label_steps.py`, `test_render_demo.py`
  and `test_render_layout.py`. It includes JSONL → renderer loader → coach → timeline,
  warnings, old action files, reopen, validation, byte ranges, failed atomic replacement,
  temporary-preview cleanup, note attribution and timestamps. A real generated H.264
  render exercised loading, coaching, note writing, drawing and encoding without models.
- The supplied 414–445 protocol round-tripped four manual outcomes at 6.5, 15.9, 23.1 and
  25.2 seconds. Sample tests skip without `BENCH_SAMPLES` or their input files.
- The 164.17 s `device-1628.mov` phone recording is 10-bit HEVC. Conversion and server
  startup completed in 20.99 s. Its temporary preview was removed when the server stopped.
  This checks decoding/transcoding and serving, not playback in a browser.
- The existing 31 s H.264 sample needed no conversion; server startup took 1.49 s.
- SIGTERM during HEVC conversion exited cleanly and removed the temporary preview folder.
- The page JavaScript passed `node --check`.
- Broader `scripts/tests`: 173 passed, 3 skipped, 2 failed in 75.31 s. The failures were
  the classroom shutdown-port assertion and the launcher's replay/restart wait. Both passed
  on an isolated rerun in a separate temporary directory (2 passed in 10.46 s), with no
  launcher changes. The initial failures are not claimed fixed.
- All owned label servers stopped and their ports were checked closed. After the broader
  tests and the isolated retry, no listeners remained on test ports 8800–8899.
- `git diff --check` passed. The required tracked-file path scan and a separate scan of
  newly added files found no machine-specific home-directory paths.

Reproduce the focused checks:

```sh
"$BENCH_PY" -m pytest scripts/tests/test_label_steps.py scripts/tests/test_render_demo.py \
  scripts/tests/test_render_layout.py -q -p no:cacheprovider
"$BENCH_PY" -m scripts.tests.check_label_steps_browser --video run.mov --protocol protocol.txt \
  --screenshots shots-steplabel --port 8810
```

## What remains unverified or limited

Browser verification and the requested screenshot could not be completed. Headless
Chromium failed before opening the page with macOS `bootstrap_check_in` permission denied
(1100). The current Playwright WebKit binary was absent; an older installed WebKit aborted
at launch. Computer-use had no available browser and could not open an in-app tab. No
security settings were changed and no browser was downloaded. Each attempted check stopped
its owned server and verified port release. `BROWSER-CHECK.txt` records the blocker in the
requested shots-steplabel destination; no screenshot was fabricated.

The reusable Playwright check covers playback, frame keys, number keys, retiming, undo,
warnings, Save/reload and narrow layout when a browser can launch. Those UI assertions have
not run successfully here. Chrome/Safari playback and the two-minute human workflow still
need a real browser check. Frame stepping uses decoded presentation timestamps, but its
visual accuracy is unverified here. HDR colour tone mapping is not implemented. Converted
previews are not intended for colour judging. Long videos need time and temporary disk space.

Use the original video and protocol when reopening labels. Saved step text catches a
protocol mismatch but the simple JSONL format does not identify the source video. Keep one
editor tab open; separate tabs use last-save-wins. Unsaved edits and undo history are not
persisted. Numeric shortcuts cover only the first nine steps. This is manual annotation,
not improved recognition or proof of liquid volume, reagent identity or sterility.

## Files and dependencies

New: `scripts/label_steps.py`, `scripts/label_steps.html`,
`scripts/tests/test_label_steps.py`, `scripts/tests/check_label_steps_browser.py`.
Updated: `scripts/render_demo.py` and `scripts/README.md`.

Existing Python standard library, FastAPI, Starlette, Uvicorn, ffmpeg/ffprobe and the
renderer/protocol dependencies are used. Tests use pytest, HTTPX and Playwright.
Sources, licences and commands are listed in `scripts/README.md`. No third-party code
was copied, packages added, models downloaded or audio played.

## Git log

Command: `git log --format='%h %an %aI %cI' main..HEAD`

Output: empty (no commits on this branch beyond main).

All changes are uncommitted for the lead. No push, identity change or date override.
