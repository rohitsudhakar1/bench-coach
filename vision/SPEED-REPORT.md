# Detector speed

No change to `docs/EVENTS.md` is proposed. Changes stay under `vision/`, as the speed brief
requires. Files are uncommitted.

The three-runs-per-second-per-camera target is **not achieved or verified**. The execution
session exposes CPU only: PyTorch has MPS support built in, but reports MPS unavailable and
rejects an MPS tensor allocation. This prevents the requested accelerator timing and
float16 acceptance check. The earlier detector report's MPS numbers are not new measurements.

## Changes

Both cameras can share one Grounding DINO or Florence generation. File mode collects each
camera round before inference; live mode submits a round atomically to one worker. Only the
newest waiting frame of each camera is retained. Partial batches work when a camera finishes.
Results keep their own camera ID and source timestamp. Closing the worker waits for its current
model call to finish. Container prompts batch separately and retain per-camera call counts.

Fixed prompt tensors are reused by batch size, with separate caches for other prompts. New
image pixels are always processed. Florence inputs preserve integer token IDs when floating
pixels and weights are converted for a half-precision experiment. Float32 remains the default;
no half-precision setting is accepted without an MPS accuracy check.

The motion gate compares every pixel against the last successful inference, skipping exact
repeats for less than one second. Failed calls do not advance that reference, and skipped
frames do not become fresh detector evidence. A one-pixel colour change triggers inference.
It skipped 0 of 332 eligible detector frames across the six two-view sample clips, so there
is no motion-related speed gain in those clips. The held-out hood window also skipped 0 of
60 eligible frames. This deliberately does not treat small motion or sensor noise as a
still image.

The existing source maximum side of 640, model processor sizes, prompts, box thresholds and
postprocessing remain unchanged. Smaller model inputs were not accepted without accuracy
proof. The default detector is still tiny. Forced Florence batching was slower on CPU, so
runtime uses serial Florence calls there; accelerators use its batch method. Tiny uses batches
on CPU as well.

## Measurement method

`vision.eval.speed_pairs` retains the original single-frame processor and model calls as
`--mode legacy`. `auto` measures the selected runtime policy, `serial` measures the updated
single-frame path, and `batch` forces one two-image model call. The samples use both actual
camera views. Times exclude decoding, loading and one warm-up pair, include processing and
postprocessing, and synchronize accelerator work. Rates are pair count divided by elapsed
inference time: actual detector runs per second per camera, with no credit for skipped frames.
They do not include hands, event processing, hub delivery or the extra container prompt.

CPU comparisons use one PyTorch thread for both versions. That is a benchmark setting, not a
change to the service's global thread setting. The first default-thread Florence attempt was
interrupted before three minutes and produced no completed timing; it is not used as a
baseline. Other process inspection was unavailable, so an otherwise idle machine could not
be established. Load averages are retained with each timing result.

All model commands used cached weights with offline loading. Each measured process has a
165-second budget, and available memory is checked before loading. No package or weight was
downloaded. The hand model for the later CLI check was copied from its existing cache into
the ignored model folder. No audio or server is started by the benchmark tools.

## CPU timing results

Twelve synchronized pairs: four each from 12–27, 249–269 and 414–445.

| Detector | Before runs/s/camera | After runs/s/camera | Before / after median pair ms | 1-minute load before / after |
| --- | ---: | ---: | ---: | ---: |
| gdino-tiny | 0.749 | 0.811 | 1323 / 1232 | 7.11 / 7.62 |
| florence2 | 0.411 | 0.416 | 2442 / 2394 | 7.01 / 6.30 |

Tiny improved about 8%. Florence changed about 1%, which is too small to claim a useful
speedup on this short shared-machine run. Neither meets three per camera on CPU.
Florence retained its 768 × 768 processor size; tiny retained shortest edge 480, longest 640.
The per-pair records are `eval/speed-{before,after}-all-<detector>.json`.

On the initial four-pair 414–445 check, forced Florence CPU batching fell from 0.374 to
0.145 runs/s per camera (median pair time 2638 to 6903 ms). That is why its CPU runtime policy
stays serial. Tiny's same check rose from 0.686 to 0.772 runs/s per camera. These small checks
were used to choose the CPU policy, not to claim MPS performance.

## Accuracy scope

`vision.eval.speed_accuracy` compares the original float32 call to the new batch call on the
128 labelled frames, in short separate processes. Acceptance requires equal box counts and
labels, one-to-one IoU at least 0.99, and score differences at most 0.001. This is a regression
check on reused labels, not an independent accuracy estimate. Tiny kept all box counts and
labels, with minimum IoU 0.997905 across 128 frames. Six frames
exceeded the additional score-difference guard: largest difference 0.001441 rather than the
0.001 cutoff, so its strict combined pass count is 122/128. No tolerance was relaxed.
Florence passed all 128 frames with identical boxes (minimum IoU 1.0). MPS float16 remains
untested. Supplying `--dtype float16` here exits before loading a model with the explanation
that MPS is required.

The unchanged cache replay still gives tiny 38/58 held-out pipettes with 130/199 wrong boxes
(0.65), and Florence 42/58 with 67/149 wrong boxes (0.45). See
`eval/speed-cached-scores.json`. Those are the historical cache baselines.

The fresh held-out check replaces all 226 main detector calls used by the four held-out label
windows, keeps their original hands, and replays through the normal scene and pipette rules.
Container boxes are copied and are not used for these default-mode box scores. Caches explicitly
record this limited scope; they must not be presented as new draw/dispense or hint measurements.
Tiny's fresh original-call CPU replay and its batched CPU replay are identical: both find
38/58 pipettes and have 132/200 wrong boxes (0.660). The saved older cache has 130/199 wrong
boxes (0.653), but the fresh baseline shows that discrepancy already exists without batching.
It must not be attributed to a batching regression or hidden by rounding. The baseline and
updated outputs are `eval/speed-heldout-legacy-gdino-tiny.json` and
`eval/speed-heldout-gdino-tiny.json`; all reported box counts and event scores agree. The
batch path is retained on that full replay result, not on a claim of identical confidence scores.

Florence's CPU runtime replay finds 42/58 pipettes and has 65/148 wrong boxes (0.439), so neither
required metric worsens against its earlier cache. Differences from the earlier MPS cache are
not claimed as accuracy gains caused by the optimization. Full live timing and full-stream
held-out scoring on MPS remain outstanding.

## Tests and checks

- All 111 vision tests pass with sample videos enabled. Tests cover batch routing, a camera
  ending early, latest-frame replacement, prompt cache isolation, new pixels after a cached
  prompt, integer token IDs in half-precision experiments, motion and failed-call recovery.
- A two-second two-camera tiny file run completed with five detector calls per camera and
  six objects events. A 1.2-second Florence run with container prompts completed with three
  calls per camera and four objects events. Both used stdout and no health server; source
  dimensions, camera IDs and detector timestamps were checked. Details: `eval/speed-smoke.json`.
  These are short functional checks, not live-throughput measurements.
- The held-out scorer refuses incomplete model caches. Sample tests skip without both clips.
- No sound was played, no server was left running, and no dependency was added. Memory checks
  stayed above the required floor; disk stayed above 5 GB. The tracked and new-file public-path
  scans found no home-directory paths. `git diff --check` passed.

## Reproduce

See the commands in `README.md`. Run each detector and each accuracy chunk separately, with
`BENCH_PY` and `BENCH_SAMPLES` configured. The scripts enforce offline loading and a short
budget. Use `--dtype float16` only for Florence on MPS; the accuracy tool refuses it otherwise.
The record files include before/after boxes, IoU checks and per-pair durations.

## Date audit

`git log --format='%h %an %aI %cI' main..HEAD`

Output: empty. No branch-only commits were made, and no history or dates were changed.

## Measured on the Mac's GPU (lead, Oct 31 03:15)

The sandbox above could only use the CPU. Rerun outside it with `vision.eval.speed_pairs`, 8 pairs per segment, all three segments, device mps, load average about 7 (another builder was running):

| Detector | Before (legacy) | After (batch, float32) | After (batch, float16) |
| --- | --- | --- | --- |
| Grounding DINO tiny | 3.69 runs/s per camera | 4.04 | not measured |
| Florence-2 base | 1.85 | 2.15 | 2.76 |

Tiny gains about 10% and stays above the 3 runs per second target. Florence-2 stays under it even in float16, and float16 accuracy against float32 was not checked on the GPU, so float16 is not a default.
