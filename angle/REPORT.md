# Angle rebuild report

Report written 2026-10-30T20:20:14-04:00.

Contract changes proposed: none. `docs/EVENTS.md` was not changed.

Built in `angle`, branch `angle`. All project changes are under
`angle/` and remain uncommitted for the lead. The shared rules, assigned brief, main README
and event contract, earlier README, scorecard, rehearsal, angle README and both angle
reports were read. Earlier code was inspected for behavior and lessons; the implementation
was written here with separate geometry, detection, sources, feed, events, runner and
calibration modules. Only the twelve labelled stills and their labels were copied as test
data from the earlier draft. No other worktree was modified.

## What works

- `python -m angle` and `python -m angle.run`: one/two video files, capture JPEGs, or direct
  camera/stream inputs; no keys needed. Default health service is port 8703. Isolated test
  servers used only ports within 8800–8899 and were shut down by their owners.
- Per-camera latest vision boxes over the hub websocket, with clipping, score selection,
  expiry, empty updates, malformed/future/out-of-order rejection, and reconnects.
- LSD with centre preference; manual CSRT fallback; loss returns to the chosen box.
  Full-frame local fallback remains explicitly rough and is not object recognition.
- Draw/dispense windows, `--always` override, hint provenance, current coach step tags, and
  run-state reset. Unlike the earlier draft, completed/skipped steps cannot replace an
  unrelated current step. There is no change to the shared event shape.
- Calibrated shaft-plane intersection and level-board gravity; conditioning rejection;
  rough single-view fallback rather than averaged projections. Rough caveats appear in
  events, warning text and overlays. Threshold is strictly greater than 20 degrees.
- Two-Hz events, five-second warning cooldown, stdout on hub failure, bounded replay,
  optional preview/overlay video, and resource shutdown on EOF or normal interruption.
- Concurrent capture reads and stale/future/repeated timestamp suppression. Freshness is
  checked again at measurement time after any manual selection delay. Direct webcams
  cannot claim synchronized stereo. Capture skew above the limit gives rough fallback.
- Measurement replay reads capture timing ledgers, uses encoded-frame indices, skips gaps,
  checks recorded pair skew, and keeps source epochs separate from emitted event time.
- `python -m angle.calibrate` accepts capture, two videos or matched folders; protects the
  first level reference; requires 12 distinct poses and fitted tilt spread; checks all RMS
  errors and baseline; atomically saves a successful fit and preserves old files on REDO.
- Printable 25 mm board with PNG physical scale; six-step table checklist and the complete
  physical pencil procedure in `CALIBRATE.md`.

## Measured results

Freshly rerun, using the shared Python environment:

| Check | Result | Scope |
| --- | --- | --- |
| Hand-labelled development stills | 9/12 detected; 3 missing; projected-angle MAE 1.3154°, maximum 4.9135° | Operator crops from inherited endpoints; prior detector tuning used this same set |
| Synthetic stereo, exact pixels | 794/1000 accepted; 206 rejected; max error 1.7233e-12° | Known cameras and correct shaft lines |
| Synthetic stereo, 0.5 px endpoint noise | 791/1000 accepted; 209 rejected; MAE 0.8710°, p95 2.6776°, max 5.0359° | Known cameras, Gaussian endpoint noise, poor geometry refused |
| Calibration from 16 rendered board pairs | Rotation error 0.308347°; translation error 1.229474 mm; gravity error 0.230952° | Actual corner finder and CLI; known synthetic camera poses |
| Calibration fit RMS | Left 0.148 px; right 0.154 px; stereo 0.156 px | Rendered pairs only |
| Four-second sample replay | 7 angle events, 0 warnings, 40 overlay frames; reported angles 3.88–7.83° | Left CP-414-445 from 2 s, manual crop, CSRT, rough estimates |

Calibration recovery assertions also require focal error below 2%, rotation below 0.5°,
translation below 3 mm and gravity error below 0.5°. The rendered-shaft test runs LSD and
stereo together for a known 30° shaft, then checks rough fallback for skew and abstention
for stale frames. These are code checks, not physical camera measurements.

`samples/results.json` contains the fresh per-frame errors and synthetic results.
`samples/evaluation.jpg` was visually checked: missing detections remain marked, reference
lines are magenta, detected lines orange, crops cyan. The printable board and
`samples/overlay-preview.jpg` were also visually checked. Local replay artifacts are
`angle/runs/left-smoke.jsonl` and `angle/runs/left-smoke.mp4` (ignored by Git).

Final automated check:

```sh
mkdir -p angle/runs
$BENCH_PY -m pytest angle/tests -q -p no:cacheprovider --basetemp angle/runs/pytest
```

```text
33 passed in 3.81s
```

Tests cover geometry, endpoint reversal, bad calibration matrices, noisy/degenerate stereo,
rendered shaft detection, tracker recovery, crop expiry/priority, idle suppression, hints,
current-step and run resets, threshold/rate/cooldown, stdout delivery fallback, real websocket
reconnection and cleanup, capture timestamps, recording gaps, offsets, file overlay output,
invalid CLI inputs, calibration recovery/REDO, print scale, and process-owned health-server
shutdown at EOF. `git diff --check` passed; a separate whitespace scan checked the new,
untracked Python/Markdown/requirements files because plain Git diff omits untracked files.

A calibration-test failure exposed OpenCV's differing corner array shapes: projection and
reference arrays now explicitly reshape to Nx2 before per-image RMS. No tolerance was
relaxed to make recovery pass. An initial custom pytest temporary-directory invocation
failed because its parent did not exist; the documented command creates it first.

## Limits and table follow-up

Physical calibration and the pencil check are PENDING. No real-camera calibration is
bundled, no physical accuracy is claimed, and no webcam or microphone was opened. The
supplied sample clips lack calibration/known physical tilt, so only projected errors can
be scored. The still set is inherited and small, not independently labelled or held out.

A vision box may contain the handle or background; LSD can follow the wrong edge and CSRT
can drift without reporting loss. Two cameras choosing different pipettes are not detected
by an identity matcher. The old right-view rehearsal crop was unreliable; the README does
not advertise it as a safe preset. A GOOD calibration fit cannot detect an unlevel reference
board, bad real-world coverage, or later camera movement. Delivery timestamps are not
hardware exposure synchronization.

Calibration video mode assumes aligned constant-rate files; use timestamp-paired folders
for dropped-frame calibration footage. The measurement runner supports the timing ledger,
but assumes `frame` indexes encoded frames and both views share a recording origin. Without
a ledger it assumes synchronized constant-rate files. Overlays skip missing pairs and
are not authoritative timing records. No fisheye/ChArUco mode is implemented.

Hint activity is labelled as a hint, not transfer proof. Hub reconnection waits for new
observations, including a current-step event; it does not fetch history. With hub down,
normal mode eventually waits for activity; use `--always` for standalone measurement.
Downstream coach speech must preserve the rough caveat; that consumer is outside this part.

No package or model downloads, sound playback, commits, pushes, date overrides, author
changes, history rewrites, or handoff clones were made. Dependencies and licences are listed
in README.md and requirements.txt. Every server started by tests was stopped; the runtime
subprocess test explicitly verifies its health port is closed after EOF.

## Required Git log

Run in this worktree before handoff:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty (no branch-only commits). Files are intentionally uncommitted under the
Codex builder rule. No Git author or committer dates were set.
