# Table robustness report

No event contract changes are proposed. Work is uncommitted. Scope is the table implementation,
its tests and documentation under vision/, plus scripts/preflight.py as required by the brief.

## Result

On 60 rendered runs (15 conditions, seeds 0–3), the original matched 142 of 360 expected events
and emitted 150 total: precision 94.7%, recall 39.4%, 20/60 exact runs. The revised tracker
matched 360/360, emitted 360 total and got 60/60 runs exactly right. These are development
fixtures used while making the changes, not held-out real-table evidence. They do not establish
real-table reliability. A separate noise seed (7) is exercised by the regression tests.

## Before and after per condition

Each row contains four runs and 24 expected events. “Right/emitted” counts one-to-one matches
of kind, tube name and the labelled visit time window; extra events reduce precision. Setup
failure contributes six misses and no emitted events. Neither version gets manual cap corrections.

| Condition | Before right/emitted | After right/emitted | Before exact runs | After exact runs | Before setup successes |
| --- | ---: | ---: | ---: | ---: | ---: |
| clean | 24/24 | 24/24 | 4/4 | 4/4 | 4/4 |
| warm | 0/0 | 24/24 | 0/4 | 4/4 | 0/4 |
| cool | 0/0 | 24/24 | 0/4 | 4/4 | 0/4 |
| dim | 0/0 | 24/24 | 0/4 | 4/4 | 0/4 |
| glare | 24/24 | 24/24 | 4/4 | 4/4 | 4/4 |
| hand_shadow | 0/0 | 24/24 | 0/4 | 4/4 | 4/4 |
| hue_plus15 | 0/0 | 24/24 | 0/4 | 4/4 | 0/4 |
| hue_minus15 | 0/0 | 24/24 | 0/4 | 4/4 | 0/4 |
| low_saturation | 0/0 | 24/24 | 0/4 | 4/4 | 0/4 |
| glove | 24/24 | 24/24 | 4/4 | 4/4 | 4/4 |
| tilt10 | 24/24 | 24/24 | 4/4 | 4/4 | 4/4 |
| tilt20 | 24/24 | 24/24 | 4/4 | 4/4 | 4/4 |
| shake | 22/30 | 24/24 | 0/4 | 4/4 | 4/4 |
| clutter | 0/0 | 24/24 | 0/4 | 4/4 | 4/4 |
| combined | 0/0 | 24/24 | 0/4 | 4/4 | 0/4 |

All revised setups succeeded. Setup failures explain much of the recall improvement. The
baseline also lost the shadowed band and followed the larger static magenta object in clutter.
Shake near the cap edge caused baseline misses and extra visits; larger regions removed them.
Glare, partial glove occlusion and the two tilted views already passed the baseline; these
fixtures show preserved performance, not a measured improvement on those conditions.

## What changed

- Setup estimates per-channel gains from the median bench colour and holds them fixed during
  the run. Gains target neutral intensity 100 and are limited to 0.5–6. A neutral bench is assumed.
- Automatic setup assigns the three cap colours jointly, with circular hue distance and a
  near-tie rejection. Each reviewed region saves its measured hue, saturation and value.
  Preflight tests those saved colours inside their own regions instead of finding a global
  largest blob. Same-colour duplicates require manual selection; --manual replaces all centres.
- Automatic regions grow from 1.4 to 1.65 detected cap radii. Overlap remains an error.
- The band uses corrected HSV, static distractor exclusions from setup, and a small bounded
  velocity prior for choosing visible candidates. Initial ambiguity returns no observation.
  Lost frames never receive predicted coordinates. Existing dwell, loss and event rules stay intact.
- Setup prints and saves a quality score; preflight recomputes it from cap visibility, median
  exposure and learned hue separation. Below 70 or with a quality issue it prints FIX. Dim
  synthetic footage can still track successfully while preflight asks for better exposure.
  The score is a readiness heuristic, not a confidence estimate. Old configs still run and
  preflight clearly says their score is unavailable. Rerun setup to get calibration.
- Fixed preflight’s missing marker_tip import and made its band check use saved correction
  and distractor masks. A new run resets the band state as well as the event tracker.

## Fixture and scoring details

The generator draws a 320 × 240 neutral bench, three caps, tip box, waste and moving band.
Six visits cover tip-on, draw A, dispense B, mix B, draw C with the same tip, then tip-off.
Each has eight travel-in frames, six dwell frames and eight exit frames at 10 fps, followed
by four trailing frames (13.6 seconds per run). All measured pixels, including setup, receive
seeded Gaussian sensor noise (standard deviation 1.2) and JPEG quality 88 compression.

Warm BGR gains are 0.65/0.85/1.25; cool 1.3/0.9/0.65. Dim intensity is 28%. A moving
shadow reduces the band patch to 27%. Glare adds white spots to caps and band. Hue shifts
are ±15 OpenCV hue units (±30 physical degrees); low saturation is 85/255. A blue glove
rectangle hides roughly half the band. Tilt uses a planar pinhole projection at 10° or 20°
about the horizontal axis, focal distance 400 pixels, with setup in the same tilted view.
Shake translates frames independently by up to three pixels in each axis and puts tube
visits 14 pixels off-centre. Clutter adds green/cyan objects and a larger static magenta disc.
Combined applies warm/dimmer light, −15 hue, saturation 170, glove, 20° tilt, shake and clutter.

Expected time windows derive from each scripted visit: three frames before the end of the
travel-in through five frames after dwell. A matching event can consume each expected item
only once. Full per-condition counts and baseline commit are in
[eval/table_stress_results.json](eval/table_stress_results.json).

## Repeat and verification

Use BENCH_PY for the prepared Python environment. No packages, models, audio or sample
recordings are needed. Baseline source is loaded from the pinned commit in local git history:
`98840791ea5e6150eacd3a39e7bdd3fdd6674062`. Keep that commit available when rerunning.

```sh
$BENCH_PY -m vision.eval.table_stress --output vision/eval/table_stress_results.json
# Optional viewable fixtures (one clip per condition):
$BENCH_PY -m vision.eval.table_stress --videos vision/eval/out/table-stress
VISION_SKIP_MODELS=1 $BENCH_PY -m pytest vision/tests scripts/tests/test_preflight.py tests/test_table_e2e.py -q
```

The combined test command passed 104 tests, with one sample-dependent skip, in 56.78 seconds.
It includes setup/run on MJPEG files, standalone stdout events, real preflight cap scoring,
loss and ambiguity checks, and the capture → vision → hub → coach → saved-note integration.
That integration used only ports in 8800–8899 and verified release of its server ports.
Two additional tests check duplicate-cap rejection and swapped-cap preflight rejection;
the final table robustness suite passed 21 tests (including those two). No sound was played.

## Limits and next bench check

The old and new detectors see identical synthetic inputs. The cases are small, deliberately
simple and reused during development. Tilt is a flat-plane model: raised cap openings, band
height and hand parallax are absent. No real camera, changing auto exposure, reflective curved
tube, moving clutter or full hand occlusion was validated. No new model or dependency was used.
OpenCV (Apache-2.0, https://opencv.org) supplies masks, blobs and video/JPEG operations;
NumPy (BSD-3-Clause, https://numpy.org) supplies calibration arithmetic and seeded fixtures.

A coloured bench can invalidate the white-balance assumption. Static exclusion areas can hide
a real band that crosses them; moving excluded objects requires new setup. Full occlusion
still drops visits after 0.5 seconds. Similar cap and band hues, ambiguous cap objects, severe
glare and large camera movement still require repositioning and review. Manual regions retain
the requested radius. Preflight requires closed caps and does not prove action recognition.
The tracker only detects visits and alternates draw/dispense; it cannot verify liquid transfer,
plunger action, volume, tip attachment or physical tilt. A physical bench rehearsal remains
required before claiming performance at a real table.

## Date and public-repository audit

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty. No branch-only commits; files remain uncommitted. No history or identity was
changed. The required tracked-file absolute-home-path scan returned no matches; the new
files were checked as well. No event contract file was changed.
