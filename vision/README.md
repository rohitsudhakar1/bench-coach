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

`VISION_DEVICE` picks where the detector runs (`cuda`, `mps` or `cpu`); without it the best one
present is used.

Tests: `$BENCH_PY -m pytest vision/tests` (about 2 s). The one test that runs the command on a
real video is skipped when `BENCH_SAMPLES` is not set.

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
- `tip_on`: the tip end stays in the tip box 0.4 s and then leaves. `tip_off`: it stays over
  the waste 0.3 s.
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
- Claude check of hints (`verify.py`), only with `--new-draw` and `ANTHROPIC_API_KEY` set: for
  each camera that saw a hint (at most 2), three crops around where the tip went (before, during,
  after) go to `claude-haiku-5-5`, which answers in a fixed JSON form. The event gets
  `detail.verified` (true, false or null) and `detail.vlm`. At most 20 checks a minute
  (`--verify-per-minute`), 2 s timeout, no retries. No key, a failure or the cap gives null and
  the event is sent at once. `--no-verify` turns it off. Kind and object never change.

All the numbers are in `Rules` (`events.py`), `SceneRules` (`scene.py`) and `DrawRules`
(`draw.py`).

## Table mode

One fixed camera looking down on the bench, tubes with red (A), blue (B) and yellow (C) caps, a
tip box, a waste cup, and a small magenta band on the pipette near its tip.

```
$BENCH_PY -m vision.table setup --source 0
$BENCH_PY -m vision.table setup --source table.mp4 --headless --tip-box 60 280 25 --waste 400 280 25
$BENCH_PY -m vision.table run --source http://localhost:8701/cam/0/frame.jpg
```

`--source` is a camera index, a video file, or a capture frame URL (its `X-Frame-Time` is kept,
repeated frames are skipped). Setup finds the three caps by colour in one still (OpenCV hue: red
0-10 or 170-179, blue 100-130, yellow 20-35, all with saturation at least 110 and value at least
80), asks for clicks for anything it could not find plus the tip box and waste, and saves
`vision/table.json` (not committed: it belongs to one camera position). Run follows the magenta
band (hue 140-165) and names visits by their order: tip box with no tip on is `tip_on`, waste
with a tip on is `tip_off`, first tube after a fresh tip or a dispense is `draw`, the next
different tube `dispense`, back into the same tube within 4 s is mixing. Mixing goes out as
`hand_touch` with `detail.action = "mix"`, since the contract has no mix kind. Events have
`detail.mode = "table"`, `object` like `"tube A"`, confidence 0.75. Losing the band for more than
0.5 s drops the visit. It only knows the band visited a place, not that liquid moved.

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
```

Both scorers replay through the same `Vision` class (`pipeline.py`) the live part runs, and both
print chance numbers next to precision and recall. Results are in `REPORT.md`.

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

## Sources and licences

- MediaPipe Hand Landmarker, model `hand_landmarker.task` float16, Apache-2.0.
  https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker. Hand points per frame.
  Downloaded on first run to `vision/models/` (not committed).
- Grounding DINO tiny, `IDEA-Research/grounding-dino-tiny`, Apache-2.0.
  https://huggingface.co/IDEA-Research/grounding-dino-tiny. Object boxes from text prompts.
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
