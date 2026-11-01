# Label filter report

Proposed changes to `docs/EVENTS.md`: none.

Implemented `vision/labelfilter.py`, a CPU-only post-filter using existing boxes
and hands. Added opt-in `--filter-labels` to the renderer and live view. All work
is uncommitted. No model, dependency, API key, audio playback or server was needed.

## What works

- Instruction lines and expected objects select eligible classes. Long phrases
  are matched first, so “tube rack” does not accidentally enable “tube”. YAML
  titles and descriptions do not enable equipment. Tip attachment permits its
  required pipette, pipette tip and tip-box context. Unknown vocabulary fails
  closed; hands remain visible.
- Each class has size, area and aspect bounds relative to the nearest current
  hand's bounding-box span. Landmark hands are preferred; detector hand boxes
  supply scale when no landmarks exist. No hand means no equipment label.
- One-to-one IoU matching tracks boxes across label changes. Three consecutive
  detector observations and three votes in a five-observation window are needed
  to show a label. Missing detections break the streak. The winning label's
  geometry is checked again. Repainting a frame cannot add votes.
- A `tip_on` needs a stable pipette body near a hand and a stable tip box near the
  same hand. A pipette-tip label alone cannot satisfy the body requirement.
  There must be three adjacent observations for the body to count as held.
- Backward clocks reset state. Results expire after one second. Cameras have
  independent filters. Live protocol/run/size changes and source fallback reset
  state; delayed detector results use hands from their source frame and cannot
  cross a reset. Published held metadata preserves the pipeline's rejection.
- Renderer filtering runs after loading or creating the raw analysis cache and
  before coach/note generation. `--reuse` can switch the filter on/off without
  poisoning that cache. Manual `--labels` events are added afterward and keep
  their explicit attribution. Filtered renders also say the filter is enabled.

The live view uses an explicit `--protocol` file or the hub's current instruction
lines. It filters its own outgoing objects and attachments with `--post-events`.
It cannot intercept events from a separate vision process. The detector, scene
rules and event engine's internal sequencing are unchanged. Only emitted
`tip_on` is gated; other action kinds remain as before.

## Thresholds and evidence

The existing tuning split has 64 labelled frames. I inspected size distributions
from the cached, scene-filtered detections against their nearest hand. For true
tip boxes, the 5th/median/95th percentiles of longest-side/hand-span were
0.74/2.36/4.40; area/span² was 0.40/3.82/13.50. True tip-labelled boxes had
longest-side ratios 0.18/0.67/2.46 and aspect 1.02/1.13/1.74. False tip-labelled
boxes overlapped these ranges heavily: aspect 1.04/1.09/2.00. Only two true
pipette-body observations were available in that geometry inspection. These
are broad heuristic bounds, not a trained classifier or measured physical sizes.
No threshold was adjusted after examining the held-out results below.

| Class | Long side / hand span | Long side / short side | Area / hand span² |
| --- | --- | --- | --- |
| pipette | 0.25–3.5 | 1.2–30 | 0.02–6 |
| pipette tip | 0.08–3 | 1.05–30 | 0.003–5.5 |
| tip box | 0.7–5 | 1–2.2 | 0.4–20 |
| tube | 0.08–2.5 | 1–15 | 0.005–4 |
| tube rack | 0.5–5 | 1–5 | 0.2–20 |
| plate | 0.3–4 | 1–4 | 0.1–12 |
| waste | 0.3–4 | 1–4 | 0.1–12 |

Tube/rack/plate/waste bounds are broad assumptions; the available labels do not
support a trustworthy per-class calibration for them. Matching requires IoU
at least 0.25. Held-body distance is at most 0.15 hand spans from its hand box;
tip-box distance is at most 0.5 hand spans. Neither distance measures depth.
Three observations add about 0.8 seconds of startup delay in the 0.4-second caches.

## Labelled cached replay

`vision/eval/labelfilter.py` replays the eight clips already in `vision/eval/cache`
through the current `Vision` baseline, then filters the scene snapshots. No
models or videos are needed. The benchmark explicitly permits all seven classes,
so it gives no credit for simply excluding a class through the protocol.

The existing `judge` function scores pipette, pipette tip, tip box and waste.
It uses approximate labelled regions and box centres, not segmentation IoU.
Counts below are box observations at labelled times, not distinct objects.
Uncertain boxes are excluded from right/wrong counts (one before in each split,
zero after). Tube, rack and plate accuracy is not scored. The latest past
snapshot is used; there is no lookup into future detections.

| Split (64 frames each) | Wrong before | Wrong after | Right before | Right after | Frames with visible pipette before → after |
| --- | ---: | ---: | ---: | ---: | --- |
| tuning | 110 | 59 | 76 | 29 | 23/43 → 4/43 |
| held out | 129 | 42 | 69 | 27 | 30/58 → 12/58 |

Held-out wrong observations fall 67.4%, but right observations fall 60.9%.
Wrong boxes are still 42/69 (60.9%) of scored filtered boxes, versus 129/198
(65.2%) before. This is chiefly a clutter reduction, not a strong precision gain.
“Visible pipette” above means a current pipette or tip-labelled box on the
labelled pipette. It does not include the event engine's four-second carried
pipette estimate, so it differs from the older reports' 38/58 tracking figure.

Held-out class counts, right/wrong:

| Class | Before | After |
| --- | --- | --- |
| pipette | 7/5 | 1/1 |
| pipette tip | 39/25 | 12/5 |
| tip box | 23/66 | 14/19 |
| waste | 0/33 | 0/17 |

There were two baseline `tip_on` events across these eight cached clip windows;
zero survive. This includes the real attachment in the 414–445 footage. Attachment
recall has not been established on a larger set and clearly suffers here.

452 post-filter observations took 0.017782 seconds total, 0.0393 ms/observation.
This excludes baseline vision replay, decoding and drawing. Detailed per-clip
counts and thresholds are in `eval/labelfilter-results.json`.

## Existing renderer caches

Five readable cached renders were replayed with their actual numbered protocols.
Counts cover each detector snapshot once, across both cameras when present.
These totals measure equipment labels removed, not accuracy: only the existing
labelled-frame benchmark above supplies box ground truth.

| Render cache | Detector observations | Equipment labels before → after | Detected tip_on before → after |
| --- | ---: | --- | --- |
| CP 12–27, two views | 76 | 216 → 0 | 0 → 0 |
| CP 249–269, two views | 100 | 381 → 92 | 0 → 0 |
| CP 414–445, two views | 156 | 585 → 97 | 1 → 0 |
| PCR 105, 122 seconds | 305 | 1417 → 118 | 8 → 0 |
| Pi USB, 30 seconds | 75 | 295 → 19 | 4 → 0 |

The CP 12–27 protocol only says to disinfect hands, so no equipment is eligible;
its zero-label result is expected and is not evidence of better recognition.
The Pi clip has no pipetting, so its four removed attachments are false positives.
Its 19 retained labels are 18 pipette-tip observations and one tip-box observation.
The false `tip_off` at 26.4 seconds is unchanged. The PCR attachment counts have
not been judged against action ground truth. Hand touches also remain unchanged.
Filtering these full cached analyses took 0.0004–0.0052 seconds each.

## Verification

- 257 tests passed, 6 skipped in 12.71 seconds: all vision tests, existing live-view
  tests, new live-filter tests, renderer tests and layout tests. The six skips are
  optional sample-dependent checks with `BENCH_SAMPLES` unset.
- 36 new tests cover protocol vocabulary, each class's geometry, scaling,
  missing/invalid hands and boxes, persistence, duplicate timestamps, gaps,
  missing observations, majority changes, track/camera isolation, event context,
  input immutability, stale results and held metadata. Integration tests cover
  default-off behavior, renderer cache reuse, live overlay and outgoing events,
  protocol/run resets and asynchronous hand alignment.
- Rendered 12 seconds of real Pi footage plus a three-second title from the cache:
  450 H.264 frames at 1920×1080, encoded in 1.153 seconds. Decoded and inspected
  the nine-second footage view. The false equipment boxes shown in the original
  screenshot are absent there, the hand remains drawn, and step 1 stays current
  instead of advancing from the false attachment. This was a visual smoke check,
  not a second accuracy dataset. Output was temporary, outside the repository.
- No live physical-camera inference was run. Live integration uses deterministic
  fake detector results and existing video tests; no server was started.

Reproduce the checks:

```sh
VISION_SKIP_MODELS=1 "$BENCH_PY" -m pytest vision/tests \
  app/tests/test_live_view.py app/tests/test_live_labelfilter.py \
  scripts/tests/test_render_demo.py scripts/tests/test_render_layout.py -q
"$BENCH_PY" -m vision.eval.labelfilter --output vision/eval/labelfilter-results.json
# Optional external cache folder; BENCH_SAMPLES supplies the CP text protocols:
"$BENCH_PY" -m vision.eval.labelfilter --render-dir "$BENCH_RENDER_OUT" \
  --output vision/eval/labelfilter-results.json
```

## What does not work

The filter cannot identify phones, lotion bottles or bags semantically. Persistent
false boxes with plausible dimensions survive. Wrong or missing hand detections
also corrupt scale, and perspective can make a real box look too small. True tips
often lack three consistent boxes; many real labels disappear. Requiring a body
misses attachments when the detector calls the whole pipette “pipette tip”.

Only known class words and a small alias list are recognized. Protocols that omit
equipment omit its label. Bottles/flasks and other unsupported classes are not
mapped to “tube”. There is no cross-camera fusion or calibrated depth. Crossings
and abrupt movement can swap or break IoU tracks. No filter is added to the
standalone vision service or launcher. Draw/dispense, tip-off, hand touches and
internal event sequencing can still be wrong even when their labels disappear.
The existing render snapshots are already scene-filtered and cannot restore
boxes that the baseline removed. This option should remain off by default.

## Git and file audit

Required command, run before finishing:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty. No branch-only commits exist. Files remain uncommitted; no push,
identity change, date override or history rewrite was performed.
The tracked-path audit and scan of newly added source/report files found no
machine-specific home paths. No event-contract edits or packages were added.
