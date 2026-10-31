# Plunger stop estimates

Contract proposal: optional `detail.stop` on vision `dispense` events, with values
`"first"`, `"second"`, or `"unknown"`. Enable with `--stops`; the field is absent by
default. `docs/EVENTS.md` is unchanged. No coach behavior or new event kind is added.
The label is an uncalibrated visual estimate, not proof of a mechanical stop or liquid transfer.

## What works

`vision/stops.py` finds thumb presses, records their depth in palm-length units, and
classifies complete traces. It uses the existing MediaPipe thumb/index distance and
held-pipette boxes. A shallow single plateau estimates first stop; a pause followed
by a deeper plateau estimates second stop. The README gives every main threshold.
Missing landmarks, stale/unheld boxes, discontinuities, incomplete presses, multiple
presses per event and conflicting camera observations produce unknown. Per-press
records include start/end, hand, maximum depth and the reason for the label.

`Vision(stops=True)` and `python -m vision --stops` annotate both the original press
rule and `--new-draw`. Dispenses wait until two media seconds after their event/end
time for release evidence, or flush at EOF. Event timestamps, kinds, objects,
confidence and hint metadata stay unchanged. Other events pass through immediately;
arrival order can therefore differ from timestamp order. A stalled source delays
flushing until another frame or shutdown. Asynchronous detector source timestamps
are used to reject boxes older than 0.6 seconds. Nothing is inferred from vessel dwell.

## Measured on the CP clips

Replayed the existing 10 Hz MediaPipe/Grounding DINO caches for all six requested
clips: 1,320 camera frames, 132 camera-seconds (66 seconds of paired scenes).
No fresh model inference is included. All six source videos were present and the
review pictures were decoded from them. Results are in `eval/stops-results.json`.

| Paired segment | Frames | Usable thumb frames | Dispense hints | First | Second | Unknown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 12-27 | 300 | 5 | 1 | 0 | 0 | 1 |
| 249-269 | 400 | 152 | 2 | 0 | 0 | 2 |
| 414-445 | 620 | 173 | 5 | 0 | 0 | 5 |
| Total | 1,320 | 330 (25.0%) | 8 | 0 | 0 | 8 |

All 8 emitted hints received the field; **0 of 8 received a first/second estimate**.
The original conservative mode emitted 0 dispenses on these clips, with or without
stops. Running each view independently gave 7 new-draw dispenses, also all unknown;
the paired sequencer names events differently, so these are separate measurements,
not extra observations to pool. Replay checked all 18 combinations (three segments,
left/right/paired, original/new-draw): every old event field and count was preserved.

The thumb tracker found 3 candidate presses across the paired clips. Their estimated
depths were 0.262, 0.449 and 0.648 hand lengths; all were rejected as incomplete or
occluded. These are landmark movements, not measured physical plunger distances.

## What could be checked by eye

Inspected all 8 paired-replay hint windows, using 12 full-frame samples per window
(96 views). Also inspected 18 native-source hand crops in each of the five
micropipette windows (90 crops). Notes, exact times and crop bounds are in
`eval/stops-review.json`. Review sheets are local ignored files in `eval/out/stops/`.

**0 of 8 windows allowed a reliable first-versus-second-stop count.** The 12-27
window shows glove handling. Both 249-269 windows show a serological pipette
controller, not a two-stop micropipette. The five 414-445 windows show a micropipette,
but gloves, the view angle, frame boundaries or incomplete motion hide the necessary
plunger travel. Even where the thumb and cap are partly visible, the pictures do not
establish two distinct mechanical stops. No accuracy, precision or recall is claimed.
Existing action labels do not contain stop-count ground truth and were not treated
as such. The 8 hints are model events, not 8 confirmed real dispenses.

## Validation and limits

- Synthetic tests cover one-stop and two-stage presses, measured depth, noise/spikes,
  missing/invalid observations, frame gaps, release, EOF, timeout, hand changes,
  timestamp reset, scale/translation, stale asynchronous detections, conflicting
  cameras, multiple presses, non-reuse, and default compatibility.
- The CLI test decodes a generated 65-frame video, injects synthetic hand/box outputs,
  and checks the real pipeline and stdout JSON emit one second-stop dispense with
  an epoch timestamp. It opens no server or speaker. This tests wiring, not models.
- Full vision suite: 153 passed. Stops tests alone: 39 passed with samples;
  36 passed and 3 sample checks skipped with `BENCH_SAMPLES` unset.
- Fresh model runs were deferred because free memory checks were below the required
  40% floor. No models, packages or APIs were downloaded or called for this work.
- Thresholds were chosen for synthetic traces, not calibrated on a pipette. A direct
  deep press without a visible first pause is unknown. A slow grip change or a wrong
  held box can still fool the heuristic. Frame gaps and short presses can prevent any
  estimate. A stable starting baseline is assumed to be the resting thumb position.
- Real first/second-stop recognition, a close-up labelled dataset, live-camera behavior
  and downstream UI rendering of this proposed field remain unvalidated. Table mode
  has no thumb observations and is not extended. No coaching is added.

Reproduce from the repository root with `BENCH_PY` set to the shared environment and
`BENCH_SAMPLES` set to the sample root:

```sh
VISION_SKIP_MODELS=1 "$BENCH_PY" -m pytest vision/tests -q
"$BENCH_PY" -m vision.eval.stops --output vision/eval/stops-results.json \
  --sheets vision/eval/out/stops
```

The new logic uses the Python standard library and the project's existing models,
OpenCV and pytest; sources and licences are listed in `vision/README.md`.

## Delivery audit

All edits are under `vision/`, on branch `stops`, and are left uncommitted for the
lead. No push, identity change, date override, history rewrite, audio playback or
server start was performed. The tracked-file absolute-path check and a separate
scan of the new files had no forbidden path hits. `git diff --check` passed.

Required command, run before delivery:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty. There are no branch-only commits.
