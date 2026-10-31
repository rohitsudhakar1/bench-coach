# Contract proposals for the lead

No changes were made to docs/EVENTS.md. The app uses these earlier-draft extensions; the other rebuilt parts should support them:

- `run` start detail includes `protocol_data`, the complete selected document. The coach can load the right protocol even if it missed selection, and the hub can recover without relying on the imported file.
- Loading a protocol emits app-owned `run-config` with `{protocol, protocol_data, conversion}`. The coach should replace its selection on this event. Producers cannot submit this event through `/events`.
- Capture recording controls receive `{run, protocol}`. Step indices are zero-based. Object boxes use source-image pixels; optional detail `width` and `height` override the image dimensions.
- The embedded note adapter uses the earlier renderer's `#vids` JSON filename list, `#player` video and timestamp `data-off` map of recording filename to seconds. The note producer must retain these hooks or implement dual-player seeking itself. PDF and run export proxy `/note/<run>.pdf` and `/note/<run>.zip`.

The existing accepted hint fields are consumed without changing their meaning. The note service alone builds the note on run stop; this app never calls POST /note.

# Delivered

Built cleanly in `app`, with source under `app/` and the explicitly assigned `protocols/convert.py`. The existing demo YAML and shared contract are unchanged. No main-checkout or earlier-draft files were modified. Files are uncommitted.

Implemented the hub routes, bounded WebSocket fan-out, durable run logs, active-run recovery, shared run controls, health strip, protocol conversion, note proxy/exports, responsive dashboard and JSONL replay. Recovery chooses the latest run start clock rather than file modification time, so late notes cannot select an older run. Stop clears warning/question/angle state. Late note events stay in the right log and cannot replace a newer run's note.

The page includes two camera feeds and expiring boxes, the current-step heading, protocol evidence labels, large warnings, angle gauge, conversation, open question, protocol paste dialog, Start/Stop, automatically selected Lab note and download links. Note timestamps seek two muted players to distinct offsets and stay paused. Note HTML is sandboxed; text is inserted as text. The note proxy uses the configured service origin and never reads event-supplied file paths.

# Checks and measured results

Shared Python command:

```text
$BENCH_PY -m pytest app/tests -q -p no:cacheprovider
24 passed in 0.46s
```

Coverage includes HTTP routes and the capture-status proxy, WebSocket delivery/disconnect, concurrent starts from two callers, capture failure, logging, active and completed recovery, torn final log row, protocol snapshots, late-note routing, warning clearing, validation, all note formats, import persistence, health caching/parallel probes, slow-subscriber handling and replay input validation. No live models or devices are involved.

JavaScript checks:

```text
node --check app/static/app.js
node --check app/static/note-player.js
node --test app/tests/note-player.test.cjs
2 passed (37.1 ms total runner duration)
```

The playback tests cover separate camera offsets, muted paused seeking, delayed metadata, latest-click selection, duration clamping and missing recordings. Python compile checks and git diff --check also passed.

Rule conversion ran against all 11 available requested protocol text files, read in place. It produced 72 steps, 34 with evidence and 32 with angle checks:

| Text | Steps | With evidence | With angle checks |
| --- | ---: | ---: | ---: |
| CP-12-27 | 1 | 0 | 0 |
| CP-249-269 | 2 | 1 | 0 |
| CP-414-445 | 4 | 4 | 3 |
| Add_cytokine_into_cells | 7 | 3 | 3 |
| Lentivirus_infection_XM | 4 | 2 | 2 |
| Mock_Cas9_Delivery | 7 | 4 | 4 |
| Mock_Transformation | 7 | 1 | 1 |
| PCR_Reaction_Setup_DJI_coarse | 5 | 1 | 1 |
| PCR_Reaction_Setup_XM | 6 | 4 | 4 |
| Serial_Dilution_XM | 6 | 5 | 5 |
| Splitting_cells_DJI-025 | 23 | 9 | 9 |

These counts describe converter output, not semantic correctness. The CP-414-445 test also checks the expected tip/draw/dispense/draw sequence and trypsin source. Claude success and failure were tested with mocked replies; no live key or service was used.

# What remains unverified

Chromium launch was attempted through the shared Playwright installation with the cached headless executable. It exited before any page opened with `bootstrap_check_in ... MachPortRendezvousServer ... Permission denied (1100)` and SIGTRAP. No screenshot, actual desktop overflow result or real media playback is claimed. The repeatable server-free check is `app/tests/browser_check.py`; it targets 1920×1080, 1440×900 and 390×844, and saves screenshots when run where Chromium is permitted.

No real capture, coach, note service, camera, microphone or recognition integration was run. The note markup hooks and capture request bodies need cross-part verification. No audio was played. No HTTP demo server was started or left running; the failed browser process exited. No package or model downloads occurred.

The hub runs as one worker, not multiple coordinated processes. It restores the latest run, not an imported selection that never started. Recovered capture recording status is unknown; event logging resumes without restarting cameras. Non-note late events cannot be assigned to an earlier run because their contract has no run identifier. In-memory event history is capped at 10,000. Protocol rules are heuristic, and compound evidence remains alternatives. These limitations are documented in README.md.

# Git date and name check

Command run in this worktree:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output:

```text
(no output)
```

No commits exist ahead of main. No commit, staging, push, date override, identity change or history rewrite was attempted. The lead can review and commit the files.

## Trainer addition

See [TRAINER-REPORT.md](TRAINER-REPORT.md) for the trainer implementation, contract
proposal, three-run checks and browser limitation. Changes are left uncommitted.

## Calibration page follow-up

The calibui additions, synthetic fit measurements, verification and remaining angle
restart requirement are recorded in [CALIBUI-REPORT.md](CALIBUI-REPORT.md).
