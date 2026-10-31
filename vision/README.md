# vision

Finds hands and the things on a pipetting bench in each camera and turns them into events for
the hub (`docs/EVENTS.md`): `objects`, `hand_touch`, `tip_on`, `tip_off`, `draw`, `dispense`.
Port 8702 serves `GET /health`.

## Run

From the repo root. `BENCH_PY` is the shared Python, `BENCH_SAMPLES` the sample video folder.

```
# two video files, as fast as the machine goes, with an annotated video to look at
$BENCH_PY -m vision --source $BENCH_SAMPLES/lsv_mv/CP-Left-Front-414-445.mp4 \
    $BENCH_SAMPLES/lsv_mv/CP-Right-Front-414-445.mp4 --debug-video vision-debug.mp4
# the same at real speed (the detector then runs in its own thread, as with live cameras)
$BENCH_PY -m vision --source a.mp4 b.mp4 --realtime
# live, from the capture part
$BENCH_PY -m vision --capture http://localhost:8701 --cams 2
```

Events are posted to `http://localhost:8700/events`. While the hub is down they are printed as
JSON lines and the hub is tried again every 5 s. `--hub none` only prints, `--log file.jsonl`
keeps a copy, `--quiet-objects` keeps `objects` events off the printed log. Other flags: `--fps`
(per camera, default 10), `--start` and `--seconds` to cut a file, `--no-detector` for hands only,
`--port 0` for no health server.

`--detector` picks the box model: `gdino-tiny` (default), `gdino-base`, `owlv2`, `florence2`,
`yoloe`, or `ensemble:<a>+<b>` (both, overlapping boxes merged). `yoloe` needs
`VISION_YOLO_DIR`, a folder for its weights. How they compare is in
[DETECTOR2-REPORT.md](DETECTOR2-REPORT.md); Florence-2 boxes the pipette and tip box best but is
too slow for two cameras, so the default did not change. [FLORENCE-REPORT.md](FLORENCE-REPORT.md)
finishes that check: Florence-2 boxes now carry a score from the model's own word probabilities,
unsure waste and tip box boxes are dropped, and on held-out clips Florence-2 still finds far fewer
draws and dispenses than tiny with `--new-draw`, so tiny stays the demo's detector.

the default remains tiny. The batching work and its limits are in
[SPEED-REPORT.md](SPEED-REPORT.md); the three-runs-per-camera target is not yet verified.

`VISION_DEVICE` picks where the detector runs (`cuda`, `mps` or `cpu`); without it the best one
present is used.

Tests: `$BENCH_PY -m pytest vision/tests`. Sample-dependent tests skip when
`BENCH_SAMPLES` is unset or the required clips are missing.

## Experimental plunger stops (proposal)

Proposed addition to the event contract, not yet adopted in `docs/EVENTS.md`:
with `--stops`, each `dispense` gets `detail.stop`, one of `"first"`, `"second"`,
or `"unknown"`. The field is absent by default. No new event kind, coach hint,
warning or step-completion rule is added. Existing hint flags and confidence stay
unchanged. This is a visual estimate, not evidence that liquid was delivered.

```sh
$BENCH_PY -m vision --source "$BENCH_SAMPLES/lsv_mv/CP-Left-Front-414-445.mp4" \
  "$BENCH_SAMPLES/lsv_mv/CP-Right-Front-414-445.mp4" \
  --new-draw --stops --no-verify --hub none --port 0
$BENCH_PY -m vision.eval.stops --output vision/eval/stops-results.json \
  --sheets vision/eval/out/stops
$BENCH_PY -m pytest vision/tests/test_stops.py -q
```

`--stops` works with the original press rule and `--new-draw`. It buffers dispenses
until two media seconds after their event/end time, or EOF. Other events pass
through, so delivery can be out of timestamp order; event timestamps are retained.
An unfinished press at the deadline or EOF is unknown. A stalled input can postpone
delivery until another frame or shutdown. Table mode does not measure thumb motion
and has no stops flag. There are no new model downloads, packages or API keys.

`stops.py` measures the fall in `Hand.thumb_ratio()` from a stable baseline. Its unit
is wrist-to-middle-knuckle length in the image, not millimetres. A three-frame median
rejects isolated spikes. A press starts at a 0.18-unit drop and ends after returning
within 0.06 units of rest for 0.15 s. Stable levels need at least three samples over
0.15 s, within 0.04 units. A shallow plateau (peak 0.18–0.45) estimates first stop.
A pause followed by a second plateau at least 0.12 units deeper estimates second stop.
A deeper single plateau or a ramp without distinct pauses is unknown. These are
uncalibrated heuristic thresholds, tested on synthetic traces only; direct second-stop
presses, camera foreshortening and different grips can defeat them.

Stop evidence requires landmarks on the held hand, palm length at least 8 pixels,
thumb/index near the pipette, the existing held-pipette gate, and a detector frame no
more than 0.6 s old. Missing frames, gaps over 0.25 s, hand changes or abrupt geometry
changes invalidate a press. Box-only hands and vessel dwell never establish a stop.
Several presses in one event, reused presses or conflicting camera results are unknown.
Internal `Press` records retain start, end, depth, hand, label and rejection reason for
evaluation; only `detail.stop` is added to the event.

On the six CP clips, paired cached replay emitted 8 dispense hints: 0 first, 0 second,
8 unknown. All eight windows were inspected; none allowed a reliable mechanical stop
count. This does not establish accuracy. See [STOPS-REPORT.md](STOPS-REPORT.md) and
the per-window notes in `eval/stops-review.json`.

## How it works

- Hands: MediaPipe Hand Landmarker on every frame. Blue and purple pixels are painted skin
  coloured first, because it misses most blue nitrile gloves otherwise. Where it still finds
  nothing, the detector's "hand" box stands in (no finger points, side guessed from the half of
  the frame it is in).
- Boxes: Grounding DINO tiny with the prompt `hand . pipette . pipette tip . tip box . tube . tube
  rack . well plate . waste bin .` at 480 x 640. Boxes in `objects` events are in each camera's own
  frame size (`detail.width`, `detail.height`), whatever size vision worked at.
- Box clean-up (`scene.py`): pipette and pipette tip boxes that sit still for 3 s are dropped;
  rack and plate boxes over a tenth of the frame and tube boxes over 3% are dropped.
  Waste must be small, in the lower half, and seen in the same place 5 times.
- Draw uses the original best pipette selection, hand-associated tip fallback, and four-second
  carry. Its boxes, timing, overlays and action rules are unchanged by the held-hand check.
- Each pipette or pipette tip in `objects.detail.boxes` now carries `held: true/false`.
  True requires current hand adjacency and more than 0.5 s of evidence in the last second.
  Frame gaps contribute at most 0.25 s; cached detector hands expire after 1 s for this check.
  Stationary boxes touching the frame edge or inferred bench line get false after 2 s.
  This metadata never removes a box from draw. Angle accepts only explicit `held: true`;
  missing metadata is rejected too. Manual angle ROIs remain a separate explicit input.
- `hand_touch`: a fingertip of a hand with finger points stays on a tip box, tube, rack, plate or
  waste for 0.3 s. The same hand on the same thing fires again only after 3 s away.
- `tip_on` (`tips.py`): only a tip box boxed in about the same place on at least 3 detector runs
  counts (a one-off box on a phone does not). Its region reaches half its height further down,
  because the detector often boxes the lid and the tips sit below. The lower end of an upright
  pipette box goes in, and a later run sees it out or higher up; that fires `tip_on`, at most
  once in 5 s. `Vision(tips=None)` brings back the older rule. Draw and dispense naming still
  follows the older rule's tip_on, so those scores did not move.
- `tip_off`: the tip end stays over the waste 0.3 s (older rule; nothing tried did better).
- `objects`: at most 2 a second per camera.
- `draw` / `dispense`, default rule (conservative): the thumb presses the plunger while the tip
  end is in a tube, rack or plate box, or with no finger points the tip end stays 0.8 s in one.
  Presses alternate draw, dispense after a fresh tip. It rarely fires.
- `draw` / `dispense` with `--new-draw` (`draw.py`): an entry is the lower end of the pipette box
  sitting 0.3 s in a bottle, flask, beaker or tube box (a second detector prompt), or, where no box
  is, coming down and holding still. An entry only counts when the pipette box is at least as tall
  as it is wide and the tip was not swinging in fast just before. Entries two cameras see within
  1 s are one event. First entry after a fresh tip or a dispense is a draw, the next a dispense.
  These events carry `detail.hint = true`, plus `cams`, `start` and `end`. No part may treat a
  hint as proof. `--containers-every 3` runs the container prompt on every third detector run only.
- `--dispense-min-dwell 0.4` (off by default): leave out a dispense hint when the tip stayed in
  under 0.4 s; the turn still flips, so draws are unchanged. Held out it raises dispense
  precision from 0.42 to 0.54 but drops recall from 0.45 to 0.32 and costs two right hint-mode
  questions, so it is not the default. The other naming checks tried are in `DrawRules`, all off;
  see `DISPENSE-REPORT.md`.
- Claude check of hints (`verify.py`), only with `--new-draw` and `ANTHROPIC_API_KEY` set: for
  each camera that saw a hint (at most 2), three crops around where the tip went (before, during,
  after) go to `claude-haiku-5-5`, which answers in a fixed JSON form. The event gets
  `detail.verified` (true, false or null) and `detail.vlm`. At most 20 checks a minute
  (`--verify-per-minute`), 2 s timeout, no retries. No key, a failure or the cap gives null and
  the event is sent at once. `--no-verify` turns it off. Kind and object never change.

All the numbers are in `Rules` (`events.py`), `SceneRules` (`scene.py`) and `DrawRules`
(`draw.py`).

## Detector batching

The file loop and live worker collect one newest frame per camera and ask Grounding DINO or
Florence-2 for the batch in one model call. The worker keeps at most one waiting frame per
camera, retains each frame's original timestamp, and can process a single remaining camera.
Container prompts have their own batch and per-camera cadence. Other detector adapters still
run serially. Florence-2 uses serial calls on CPU because forcing batches was slower there;
MPS and CUDA use batches. The default model is still Grounding DINO tiny.

Fixed prompt tensors are cached separately for each prompt and batch size. Image pixels are
processed afresh. Source frames retain the existing maximum side of 640 and the processors keep
their existing model sizes; reducing them has not passed an accuracy check. The motion gate
compares every pixel with the last successful detector input, skips exact duplicates for less
than one second, and then refreshes. It does not manufacture a new timestamp or detector result
for a skipped frame. Sensor noise will usually prevent skipping, which is deliberate: subtle tip
movement must not disappear behind a global motion threshold.

Float32 remains the default. Florence's Python constructor accepts `dtype="float16"` for the
benchmark candidate, but half precision has not been accepted for live use. The evaluation
commands run offline against cached weights and stop after a short budget; no package or weight
download is needed. `BENCH_SAMPLES` must point to the sample folder.

```sh
# Run each command separately; timing excludes model loading and one warm-up pair.
$BENCH_PY -m vision.eval.speed_pairs --detector gdino-tiny --mode legacy --json vision/eval/before.json
$BENCH_PY -m vision.eval.speed_pairs --detector gdino-tiny --mode auto --json vision/eval/after.json
$BENCH_PY -m vision.eval.speed_pairs --detector florence2 --mode auto --json vision/eval/florence.json
# Repeat with offsets 0,16,32,48,64,80,96,112; one short model process at a time.
$BENCH_PY -m vision.eval.speed_accuracy --detector florence2 --offset 0 --json vision/eval/agreement.json
# Requires MPS. Accept only after all labelled chunks pass and held-out scores do not worsen.
VISION_DEVICE=mps $BENCH_PY -m vision.eval.speed_accuracy --detector florence2 --dtype float16 --offset 0 --json vision/eval/half.json
```

Timing uses the synchronized left and right 12–27, 249–269 and 414–445 clips. `--mode legacy` retains the
original processor/model calls, `serial` uses the updated single-frame calls, `batch` forces
batch size two, and `auto` uses the runtime policy. `--threads` defaults to one for reproducible
CPU comparisons; it does not change the live service's thread setting. Rates count genuine
inferences per camera, not skipped or reused results. These timings exclude hands, event rules,
video decoding and hub delivery. Use `--segments 414-445 --pairs 4` for a shorter check. Keep each budget below
three minutes and check available memory between invocations.

## Table mode

One fixed camera looking down on the bench, tubes with red (A), blue (B) and yellow (C) caps, a
tip box, a waste cup, and a small magenta band on the pipette near its tip.

```
$BENCH_PY -m vision.table setup --source 0
$BENCH_PY -m vision.table setup --source table.mp4 --headless --tip-box 60 280 25 --waste 400 280 25
$BENCH_PY -m vision.table run --source http://localhost:8701/cam/0/frame.jpg
```

`--source` is a camera index, a video file, or a capture frame URL (its `X-Frame-Time` is kept,
repeated frames are skipped). Setup estimates white balance and exposure from the median bench
colour, so use a mostly neutral, matte bench. It proposes red A, blue B and yellow C jointly,
then learns each cap's actual hue and saturation from the reviewed regions. Keep caps closed
and the pipette out of view during setup. Ambiguous colours require clicks; use `--manual` to
correct all centres. Automatic tube regions extend to 1.65 times the detected cap radius.
Review them for overlap and for the position of the open tube. Setup saves lighting gains,
cap colours and static magenta distractors in `vision/table.json` (not committed).

Setup prints a quality score out of 100; preflight recomputes it against the current image
using cap visibility, exposure and colour separation. It requests a fix below 70 or when cap
colours are too similar. This is a readiness heuristic, not an accuracy percentage. Old configs
still run, but preflight reports that their quality is unavailable; rerun setup to upgrade.
Lighting correction is held fixed during a run. Rerun setup after moving the camera, rack,
lighting or coloured bench objects. Preflight must see the closed caps before they are opened.

Run follows the magenta
band (hue 134-170 after correction) and names visits by their order: tip box with no tip on is `tip_on`, waste
with a tip on is `tip_off`, first tube after a fresh tip or a dispense is `draw`, the next
different tube `dispense`, back into the same tube within 4 s is mixing. Mixing goes out as
`hand_touch` with `detail.action = "mix"`, since the contract has no mix kind. Events have
`detail.mode = "table"`, `object` like `"tube A"`, confidence 0.75. Losing the band for more than
0.5 s drops the visit. A short motion prior chooses among visible band fragments; no predicted
point is passed to the event rules. Static magenta areas from setup are excluded. Multiple
candidates at acquisition cause a missed observation rather than a largest-blob guess.
It only knows the band visited a place, not that liquid moved.

The launcher supplies `--port` for health and `--follow-runs` to poll the hub's run state
at 0.1-second intervals. Start opens the source and resets tracking; Stop releases it.
File EOF waits for the next run; standalone file runs still exit at EOF and default to
no health port. Capture playback runs independently of Start, so a shared video needs
an idle lead-in or a full loop before the first tip visit. Health reports `mode`, `run`
and processed `frames`; it is not proof that the source is fresh. A stalled HTTP frame
read can delay noticing Stop by the reader's ten-second timeout.

## Measuring it

`vision/eval/` holds the earlier draft's hand labels: 128 labelled frames (`labels.json`) and 85
labelled draw, dispense and mix actions (`actions.json`), with video paths relative to
`BENCH_SAMPLES`. `vision/eval/cache/` is the saved model output for those videos, so scoring
needs no model:

```
$BENCH_PY -m vision.eval.cache --all          # models over every labelled video (needs BENCH_SAMPLES)
$BENCH_PY -m vision.eval.score                # boxes, pipette found, touches, tips, presses
$BENCH_PY -m vision.eval.score_actions --per-clip   # draw and dispense, both rules
$BENCH_PY -m vision.eval.score_tips           # tip on, tip off, touches against tips.json
```

`vision/eval/tips.json` holds 30 tip pick-ups, 22 tip ejects and 51 hand touches marked by eye
from contact sheets (`$BENCH_PY -m vision.eval.sheet video.mp4 out.jpg --start 0 --end 24
--every 0.5`). `score_tips --set '{"tips": null}'` scores the older rules. Results and what was
tried are in [TIP-REPORT.md](TIP-REPORT.md).

Other detectors: `$BENCH_PY -m vision.eval.recache --detector florence2 --labels` reruns one over the
same saved frames (hands kept) into `vision/eval/cache-florence2/`; every scorer reads it with
`VISION_EVAL_CACHE=cache-florence2`. `$BENCH_PY -m vision.eval.compare gdino-tiny florence2`
scores them side by side and `$BENCH_PY -m vision.eval.speed gdino-tiny florence2` times them.

For the speed regression check, `vision.eval.speed_recache --detector gdino-tiny` (or
`florence2`) rebuilds at most 64 held-out detector frames per process. Repeat four times, then
add `--score` to score only the held-out windows through the normal pipeline. `--mode legacy`
builds a separate fresh baseline with the original calls. These caches copy hands and container
boxes and replace **main detections only**; they are not new action or hint benchmarks. The
scorer refuses a cache with pending frames. Results are in [SPEED-REPORT.md](SPEED-REPORT.md).

Both scorers replay through the same `Vision` class (`pipeline.py`) the live part runs, and both
print chance numbers next to precision and recall. Results are in `REPORT.md`.

A small trained pipette detector (`vision/tipmodel/`) was compared with Grounding DINO on
held-out clips in [TIPMODEL-REPORT.md](TIPMODEL-REPORT.md). It draws far fewer wrong boxes and
runs in 17 ms, but the live code finds the pipette less often with it, so it is not an option yet.

Dispense signals and naming checks are measured in `DISPENSE-REPORT.md`
(`vision.eval.dispense_signals`, `vision.eval.sweep_dispense`, `vision.eval.hint_dispense`).

Round 2 is measured in `TRIPOD-REPORT.md`: held-out pipettes remain 38/58, with 130/199 wrong
boxes, matching the original baseline. Draw is restored to 15/29 precision and 15/18 recall;
dispense to 10/24 and 10/22. Angle accepts no boxes on either cached 12-27 clip, so the tripod
is excluded but no useful angle is measured there. These reused labels are regression checks.

```
$BENCH_PY -m vision.eval.tripod  # cached 12-27 audit; BENCH_SAMPLES enables three paired stills
```

The stills and local run logs live in `vision/eval/out/`, which is ignored. The audit checks
labelled distractor regions in both views; no region coordinates enter the live filter.
Bench support lines are horizontal edges of wide rack/plate detections, not calibrated bench
geometry. If no such box exists, only the frame-edge test and older static filter apply.

## Table stress check

```sh
$BENCH_PY -m vision.eval.table_stress --output vision/eval/table_stress_results.json
$BENCH_PY -m pytest vision/tests/test_table.py vision/tests/test_table_robust.py -q
```

Add `--videos vision/eval/out/table-stress` to retain one clip per condition. The benchmark
compares the original tracker at a pinned commit with the current tracker on the same pixels,
including automatic setup failures. See [TABLEROBUST-REPORT.md](TABLEROBUST-REPORT.md) for
per-condition counts and limits. These are rendered stress cases, not real-table accuracy.
Table calibration and tracking use the existing OpenCV and NumPy dependencies below.

## Sources and licences

- Python standard library, PSF-2.0, https://docs.python.org/3/library/.
  Stop traces, median smoothing, event buffering and evaluation JSON. Stop estimation
  reuses the existing MediaPipe hands and Grounding DINO boxes listed below; OpenCV
  decodes review frames and pytest checks synthetic traces. No new dependency.

- MediaPipe Hand Landmarker, model `hand_landmarker.task` float16, Apache-2.0.
  https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker. Hand points per frame.
  Downloaded on first run to `vision/models/` (not committed).
- Grounding DINO tiny, `IDEA-Research/grounding-dino-tiny`, Apache-2.0.
  https://huggingface.co/IDEA-Research/grounding-dino-tiny. Object boxes from text prompts.
- torchvision Faster R-CNN MobileNetV3-Large FPN with COCO weights, BSD-3-Clause,
  https://github.com/pytorch/vision. The tip model in `vision/tipmodel/` (weights not committed).
- Hugging Face transformers, Apache-2.0, https://github.com/huggingface/transformers. Runs the detector.
- PyTorch, BSD-3-Clause, https://pytorch.org. Model runtime (CUDA, MPS or CPU).
- OpenCV, Apache-2.0, https://opencv.org. Video reading, colour masks, drawing, debug video.
- NumPy, BSD-3-Clause, https://numpy.org.
- FastAPI (MIT), https://fastapi.tiangolo.com, and uvicorn (BSD-3-Clause), https://www.uvicorn.org. Health endpoint.
- HTTPX, BSD-3-Clause, https://www.python-httpx.org. Posting events, reading capture frames.
- Anthropic Python SDK, MIT, https://github.com/anthropics/anthropic-sdk-python. Optional
  Claude check of hints, model `claude-haiku-5-5`.
- pytest, MIT, https://pytest.org.

All were already in the shared environment; no package was added.

## Detector models

| Model | Where | Licence | Used for |
| --- | --- | --- | --- |
| Grounding DINO tiny | https://huggingface.co/IDEA-Research/grounding-dino-tiny | Apache 2.0 | default boxes |
| Grounding DINO base | https://huggingface.co/IDEA-Research/grounding-dino-base | Apache 2.0 | `--detector gdino-base` |
| OWLv2 base patch16 ensemble | https://huggingface.co/google/owlv2-base-patch16-ensemble | Apache 2.0 | `--detector owlv2` |
| Florence-2 base | https://huggingface.co/florence-community/Florence-2-base | MIT | `--detector florence2` (phrase grounding) |
| YOLOE-26 large | https://github.com/ultralytics/ultralytics | AGPL-3.0 | `--detector yoloe`, measured only; AGPL means a product using it must publish its source or buy a licence |

All load through transformers or ultralytics, both already in the shared environment.

## Read bottle and tube labels

`python -m vision.labels` runs a separate offline OCR check using macOS Apple Vision
(`VNRecognizeTextRequest`, accurate mode, English, language correction disabled). It does
not start a service, publish hub events, or change protocol steps. OpenCV reads the video;
a small Swift helper reads temporary PNGs. Nothing is downloaded and no API key is needed.
The helper requires macOS and `swiftc` from the Apple command-line tools. Compilation happens
once per command in a temporary directory, which is removed on exit; a cold SDK compile can
add roughly a minute. The video is never copied into the repository.

```sh
# BENCH_SAMPLES points to the directory containing the two Pi camera clips.
"$BENCH_PY" -m vision.labels --source "$BENCH_SAMPLES/pi-usb-cam.mov" --sample-rate 1
"$BENCH_PY" -m vision.labels --source "$BENCH_SAMPLES/pi-usb-cam.mov" \
    --sample-rate 1 --crop 400,150,700,570 --upscale 2
"$BENCH_PY" -m pytest vision/tests/test_labels.py -q
```

`--crop` is `x,y,width,height` in original-frame pixels, top-left origin. It must fit fully
inside the video. `--upscale 2` applies bicubic interpolation to the crop (or full frame).
Output is one JSON line per recognized text region: `{t, text, confidence, box}`. `box` is
`[x,y,width,height]` in the original frame, including the crop offset and undoing enlargement;
these are axis-aligned text-region boxes, not individual letter heights. Confidence is Vision's
score, not a measured probability of correct identification. Empty frames emit the same four
fields with empty text, null confidence/box and `status: "no_text"`.

`t` is media seconds for these standalone analysis rows. `--time-origin` adds an explicit
origin if alignment to capture epoch time is needed. These rows are not the event contract's
event envelope and are never posted to the hub. Timing uses frame index/FPS; this reader is
intended for constant-frame-rate footage, including these 30 fps samples. `--start` and
`--seconds` limit the inspected interval. Sampling picks the first frame at or after each
target time and rejects rates above the source frame rate.

Non-Mac hosts need only the standard library to return a single `status: "unavailable"`
row with all four fields null and a reason (exit 2). Missing Swift, compile failures and
Vision runtime failures also return unavailable, rather than suggesting there was no text.
Bad input returns exit 1 with an error on stderr. There is no non-Mac OCR implementation.

See [LABELS-REPORT.md](LABELS-REPORT.md) for the camera check and its blocked Vision measurements.
`python -m vision.eval.labels_camera --source video.mov` repeats six samples with the native
backend. It can also run the already-installed `tesseract` executable with `--backend tesseract`
as a separate diagnostic. That option is not used by production OCR and is not its fallback.
The evaluator accepts `--times`, `--crop`, `--upscale`; `--deskew` is an optional diagnostic
rotation in degrees (positive counterclockwise). It prints JSONL and saves no sample images.

Additional OCR sources and licences:

- Apple Vision: https://developer.apple.com/documentation/vision/vnrecognizetextrequest.
  Proprietary macOS framework under Apple's SDK/OS terms, not an open-source OCR library.
- Swift compiler: https://www.swift.org. Apache-2.0 with Runtime Library Exception.
  Compiles the helper; Foundation and ImageIO load the image and serialize results.
- OpenCV: https://opencv.org, Apache-2.0 (Python wheel packaging MIT).
  Existing dependency for frame decoding, crops and bicubic resizing.
- Tesseract: https://github.com/tesseract-ocr/tesseract, Apache-2.0.
  Optional, separate measurement diagnostic, using its installed English trained data.
  It is not added to runtime requirements. No packages or trained data were installed.
