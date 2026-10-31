# Tripod filter, round 2

Contract proposal (docs/EVENTS.md unchanged): each pipette and pipette-tip object in
`detail.boxes` carries boolean `held`. Angle requires explicit true. Draw ignores it.
Work is uncommitted, confined to vision/ plus angle/feed.py and its tests.

## Behavior

Restored the original draw selection, tip fallback, carry arithmetic, cached detections,
object publication and overlay boxes. The round 1 gate no longer removes or replaces boxes.
The additional two-second stand rule now annotates only; the earlier three-second static,
area and waste filters still run exactly as before.

A separate per-camera held check requires adjacency now and more than 0.5 s of hand evidence
in the last second. Adjacency padding is 40% of the hand's longest box dimension. Each frame
interval contributes at most 0.25 s; stale detector hands expire after one second for this
check only. Continuous moving candidates may use their hand-associated trajectory.
Stationary candidates touching a frame edge or inferred bench line are marked false after
2 s and three observations. Bench lines are wide rack/plate edges, not measured geometry.
Angle rejects false, missing, numeric and string held values. A new unheld objects event
clears the previously accepted crop. Explicit manual angle ROIs retain their existing behavior.

## Frame checks

Same labels, cached model results, scorer and splits throughout. Original baseline is
revision `10f83e2`; round 1 results remain in tripod-after.json. New results are in
tripod-round2-frames.json. Neither labels nor scoring rules changed.

| Metric | Original | Round 1 | Round 2 |
| --- | --- | --- | --- |
| Tune pipette found | 33/43 | 30/43 | 33/43 |
| Hold pipette found | 38/58 | 38/58 | 38/58 |
| Tune wrong boxes | 106/182 (58.24%) | 77/145 (53.10%) | 106/182 (58.24%) |
| Hold wrong boxes | 130/199 (65.33%) | 109/170 (64.12%) | 130/199 (65.33%) |

The original frame results are restored exactly. Round 1's smaller wrong-box denominator
came from withholding proposals; round 2 intentionally restores those boxes for draw/display.
These reused, coarse labels are regression checks, not an independent benchmark.

## Action scoring

tripod-round2-actions.json contains both splits and both action modes. All counts match the
original score_actions.json. Round 1 is preserved in tripod-actions-after.json.

| Held-out hint metric | Original | Round 1 | Round 2 |
| --- | --- | --- | --- |
| Draw precision | 15/29 (0.52) | 8/21 (0.38) | 15/29 (0.52) |
| Draw recall | 15/18 (0.83) | 8/18 (0.44) | 15/18 (0.83) |
| Dispense precision | 10/24 (0.42) | 7/19 (0.37) | 10/24 (0.42) |
| Dispense recall | 10/22 (0.45) | 7/22 (0.32) | 10/22 (0.45) |

Tune hints also return to draw 14/29 precision, 14/20 recall and dispense 11/25 precision,
11/20 recall. Default conservative action counts also match the original baseline.

## Both 12-27 clips, angle path

Cached replay covers all 150 frames per view. The audit feeds actual objects events through
angle.feed.FeedState. Before counts apply the previous no-held-check acceptance to the same
restored objects events, including score ordering and crop clipping. Counts are crops at
objects-event times, not shaft detections or physical angle readings.

| Audit | Left original | Left round 2 | Right original | Right round 2 |
| --- | --- | --- | --- | --- |
| Angle crops | 7 | 0 | 5 | 0 |
| Angle crops on known distractors | 6 | 0 | 4 | 0 |
| Draw-selected frames on distractors | 71 | 71 | 57 | 57 |
| Emitted pipette/tip boxes on distractors | 15 | 15 | 5 | 5 |

Round 1 emitted zero distractor boxes in either view. Round 2 retains those proposals for
draw but prevents angle from using them. Zero accepted angle boxes also means zero useful
pipette measurements on these clips; this is suppression, not improved angle recall.
The original left clip's one draw and one dispense hint return; right has neither.

Three paired angle-path stills are saved at 2.1, 7.3 and 12.5 s under vision/eval/out/.
They render the actual current FeedState crop, not the general draw overlay. No angle crop
sits on a tripod. The stills and local logs are ignored and must remain uncommitted.

A fresh model run of both full clips finished in 47.2 s: 150 frames and 38 detector runs
per camera, 41 total events. Each camera emitted 19 objects events. Left emitted 17 pipette/tip
boxes, right 5; all had held false. Passing those events through the real angle feed yielded
zero angle crops and zero distractor crops in both views. This checks actual model output,
not only the cache; it does not establish useful angle recall. No angle or coach service ran.

```sh
$BENCH_PY -m vision --source "$BENCH_SAMPLES/lsv_mv/CP-Left-Front-12-27.mp4" \
  "$BENCH_SAMPLES/lsv_mv/CP-Right-Front-12-27.mp4" --hub none --port 0 \
  --new-draw --no-verify --seconds 15 --log vision/eval/out/tripod-round2-fresh.jsonl
```

## Verification

94 tests passed with BENCH_SAMPLES set, covering vision and angle. Regression tests enforce
the exact held-out action counts, original frame acceptance, per-box held flags, both raw
pipette labels, camera isolation, stale hands, brief passes, clock reset, two-second stands,
and the actual angle feed on both cached views. Angle tests verify missing/false/nonboolean
metadata rejection and replacement of a previously held crop. Existing event tests were
restored unchanged; short draw visits no longer need hand qualification.
All three paired stills were visually inspected. The model run began at 60% free memory,
ran under three minutes and exited. No servers remain running. git diff --check passed.
The required git grep public-path audit returned no matches; new text files were checked too.

## Reproduce

```sh
$BENCH_PY -m vision.eval.score --json vision/eval/tripod-round2-frames.json
$BENCH_PY -m vision.eval.score_actions --json vision/eval/tripod-round2-actions.json
$BENCH_PY -m vision.eval.tripod
$BENCH_PY -m pytest vision/tests angle/tests -q -p no:cacheprovider
```

Set BENCH_SAMPLES for source-video checks and still generation. No packages were added.
No audio was played. No changes were made outside the assigned worktree or allowed parts.

## Git log

`git log --format='%h %an %aI %cI' main..HEAD` output: empty.
No commits, identity changes or history rewrites.
