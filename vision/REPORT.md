# vision report

## Proposed changes to docs/EVENTS.md (not made)

- Round 2: document `held: true/false` on each pipette/pipette-tip entry in
  `objects.detail.boxes`. Angle requires explicit true; draw ignores this metadata.

- A `mix` event kind. Table mode sees mixing (the tip going back into the same tube within 4 s)
  and today sends it as `hand_touch` with `detail.action = "mix"`, because the contract has no
  mix kind.
- Say that `draw` and `dispense` may have `object: null` (a `--new-draw` hint where no container
  box was under the tip), and that their `detail` carries `cams`, `start`, `end`, `hint`, and
  with the Claude check `verified` and `vlm`.
- Say that `objects` events carry `detail.width` and `detail.height` (the camera's own frame
  size the boxes are in) and `detail.detector_frame_t`.

## What works

- `python -m vision --source a.mp4 b.mp4` and `--capture http://localhost:8701 --cams 2`. Events
  go to the hub, or are printed as JSON lines while it is down. `/health` answers on the port
  (checked on 8811 during a run). Ctrl-C and SIGTERM stop it cleanly.
- End to end on the two-camera trypsin clip (31 s, both views, detector on, `--new-draw`, debug
  video): 75 s on this Mac, 88 events, the tip pick-up found, hints carry `hint: true` and
  `verified: null` (no key here). The debug video shows boxes, hand points, the tip point and
  the latest events.
- Hands with glove recolouring, Grounding DINO tiny boxes, the box clean-up, touches, tip on
  and off, the conservative draw/dispense rule, the `--new-draw` tracker, the Claude check, boxes
  scaled to each camera's frame size, `VISION_DEVICE`, `--debug-video`.
- Table mode `setup` and `run`, on a camera, a file, or a capture frame URL.
- 40 tests pass with `BENCH_SAMPLES` set (39 and 1 skipped without it), in under 2 s.
- The live part and both scorers run the same `Vision` class, so the numbers below are what the
  live part does on those files.

## Measured

Labels are the earlier draft's, copied into `vision/eval/`. The models were run again with this
code over every labelled video (`vision/eval/cache/`, 1.1 MB). "Chance" is the same events placed
at random times in the clip, 200 tries. "hold" clips never picked a setting in the earlier draft;
"tune" ones did.

Draw and dispense against 85 labelled actions (`score_actions`, 1.5 s tolerance):

| rule | split | draw precision | draw recall | dispense precision | dispense recall |
| --- | --- | --- | --- | --- | --- |
| default (conservative) | hold | 0.50 (2 of 4), chance 0.30 | 0.11 (2 of 18) | 0.67 (2 of 3), chance 0.42 | 0.09 (2 of 22) |
| `--new-draw` | hold | 0.52 (15 of 29), chance 0.26 | 0.83 (15 of 18), chance 0.43 | 0.42 (10 of 24), chance 0.30 | 0.45 (10 of 22), chance 0.32 |
| default | tune | 0.50 (2 of 4), chance 0.35 | 0.10 (2 of 20) | 1.00 (4 of 4), chance 0.51 | 0.20 (4 of 20) |
| `--new-draw` | tune | 0.48 (14 of 29), chance 0.25 | 0.70 (14 of 20), chance 0.36 | 0.44 (11 of 25), chance 0.30 | 0.55 (11 of 20), chance 0.37 |

The earlier draft reported 0.52 / 0.83 draw and 0.43 / 0.45 dispense held out; this rebuild
lands within one event of that. Draw hints are right about twice as often as chance. Dispense
hints are only about 12 points over chance. The default rule rarely fires, and on so few events
its precision means little.

Frames and other events against 128 labelled frames (`score`), held-out half:

- Boxes on the wrong thing: 0.65 (pipette 5 of 12, pipette tip 25 of 64, tip box 66 of 89,
  mostly a chest-mounted phone, and waste 34 of 34; these clips have no waste bin at all).
- Pipette found on 38 of 58 frames where one is labelled.
- hand_touch fired 3, none right, 0 of 4 real touches found. tip_on missed the one real tip
  pick-up. One tip_off fired, and it is wrong (no waste in these clips).
- Tuning half: wrong boxes 0.58, pipette 33 of 43, hand_touch 1 of 3 right (chance 0.07).

One choice differs from the brief's wording. The brief says to drop rack and plate boxes over
half the bench. With half the frame as the limit, held-out touches went from 3 to 7 fired, all
wrong, and the default rule fired one more wrong press. With a tenth of the frame (the earlier
draft's measured setting) they are 3. So the default is a tenth; `SceneRules.max_frac` changes it.

## What does not work well

- Draw and dispense are hints. Half the draw hints and more than half the dispense hints are
  false. Naming alternates draw, dispense, so one extra or missed entry flips the names after it
  until the next fresh tip.
- `object` is a kind of thing ("tube", "bottle") or null, never "tube A". Telling tubes apart
  needs table mode.
- Touches and tip on/off are weak on these clips (numbers above). The detector keeps boxing a
  phone as a tip box; waste boxes are all false here. A real bin on the demo bench needs a check.
- The Claude check has not run with a real key on this machine (no `ANTHROPIC_API_KEY`), so its
  accuracy is not measured. With no key every hint goes out at once with `verified: null`. Tests
  cover the rule, the cap, failures and the pictures with a fake client.
- Table mode is only checked on made-up video in its tests, not a real bench, real light or a
  real camera. It knows the band visited a place, not that liquid moved.
- Speed: files run at about 4 frames a second per camera with two cameras and both prompts
  (`--containers-every 3` cuts the container cost). Live mode was not timed against the real
  capture part.
- `scripts/addpkg.sh` (outside vision, from main) still names a local home folder; it is the lead's file.

## Date check

`git log --format='%h %an %aI %cI' main..HEAD` before this report was committed:

```
2784f10 Andy Bei 2026-10-30T20:27:08-04:00 2026-10-30T20:27:08-04:00
832bfc4 Andy Bei 2026-10-30T20:22:24-04:00 2026-10-30T20:22:24-04:00
0622562 Andy Bei 2026-10-30T20:20:15-04:00 2026-10-30T20:20:15-04:00
3ae94a8 Andy Bei 2026-10-30T20:18:01-04:00 2026-10-30T20:18:01-04:00
```

Real times, no rewritten history, author from the repo config. No sound played, no package
added, only ports 8810 and 8811 used for checks, nothing left running.

## Tripod follow-up, round 2

See [TRIPOD-REPORT.md](TRIPOD-REPORT.md). Draw selection and carry are restored to the original
baseline; only angle uses the held-hand check. Held-out frames: 38/58 pipettes, 130/199 wrong
boxes. Held-out draw: 15/29 precision, 15/18 recall; dispense: 10/24 precision, 10/22 recall.
Both cached 12-27 views provide no angle crops, including no tripod crops. Three angle-path
stills are ignored under eval/out/. Missing useful angles remains a limitation.
The follow-up is uncommitted. Latest `git log --format='%h %an %aI %cI' main..HEAD` output: empty.

## Tip and touch follow-up

See [TIP-REPORT.md](TIP-REPORT.md). New labels (30 tip pick-ups, 22 ejects, 51 touches) and a
steady-tip-box rule for tip_on: held-out 5 of 16 pick-ups found and 5 of 17 events right
(chance 0.09 and 0.08), was 0 of 16. Touches and tip_off are unchanged; nothing tried beat them
on held-out. Draw and dispense scores are unchanged.

## Table robustness follow-up

See [TABLEROBUST-REPORT.md](TABLEROBUST-REPORT.md) for calibration and tracking changes,
per-condition before/after counts, tests, limits and the current empty branch commit audit.
No additional event contract change is proposed by this follow-up.
