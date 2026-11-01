# Masks report: real outlines instead of boxes

Proposals for docs/EVENTS.md: none. Outlines stay inside the renderer and the live view; no
event changes.

## What was built

- `vision/masks.py`: `load_segmenter()` and `outlines(seg, frame, boxes)`. The detector's boxes
  are the prompts, all boxes of a frame go through the model in one call, and each mask becomes
  a polygon. A mask that fills under 12% of its box or spills more than a quarter of the box
  outside it is dropped and the box is drawn. No weights, no torch, or a model error on one
  frame all give boxes, with one line saying why.
- `scripts/render_demo.py --masks` (on by default, `--no-masks` for boxes). Outlines are worked
  out in the vision pass on each detection frame, saved in `analysis.json`, drawn with a light
  fill, and carried along with their box between detections.
- `app/live.py --masks` (on by default, `--no-masks` keeps the old GrabCut cutout).
- `scripts/fetch_models.py`: EdgeTAM is a default model (56 MB), SAM 2.1 tiny is optional;
  `--check` loads EdgeTAM with the network blocked.
- `timm` added (EdgeTAM needs it), listed in requirements.txt and vision/requirements.txt.
- Tests in `vision/tests/test_masks.py`: polygon from a mask, rejecting spilled and sliver masks,
  the fall back to boxes (no model, model error, missing weights), both drawers draw the outline
  and not the box, the outline follows its box between detections, and one real EdgeTAM frame
  (marked `models`, skips without samples or weights).

## Model choice, measured on this Mac (MPS)

Six frames from the CP Left/Right Front clips at 640x360, 4 to 8 Grounding DINO boxes each:

| Model | Size | Median per frame (all boxes) | Max | Masks kept |
| --- | --- | --- | --- | --- |
| EdgeTAM | 56 MB | 62 ms | 101 ms | 40 of 40 |
| SAM 2.1 tiny | 156 MB | 138 ms | 143 ms | 40 of 40 |

The outlines look the same to the eye (stills compared side by side), so EdgeTAM is the default.

## Speed in the real tools

- Render, CP-414-445, two cameras, 31 s of footage: 41.8 s total with masks, 35.5 s without
  (vision pass 37.3 s vs 30.8 s; EdgeTAM 47 ms median per call). A 2-minute clip scales to about
  2.7 minutes, well under the 10-minute limit, so masks are on by default.
- Live view, CP-Left-Front-414-445 looped, 24 s: picture 30 fps either way; detections 5.0 per
  second with masks, 5.6 without; outline step 46 to 59 ms with EdgeTAM, 21 to 37 ms with GrabCut.

## What it looks like

Outlines follow the tip box, tube rack, pipette, waste bin and the absorbent pad closely on all
three CP clips, in both the render and the live view. Stills were saved outside the repo.

## What does not work or is left

- The outline is only as good as the box and its name. Grounding DINO still calls hair and the
  pipette "pipette tip" and misses the tip boxes on the right of the 249-269 clip; masks do not
  fix that.
- No mask tracking between detections. EdgeTAM and SAM 2.1 have video trackers in transformers,
  but they need a session per object and would rerun on every frame; the outline is moved with
  its box instead, so a pipette that rotates between detections keeps its old shape for up to
  0.35 s.
- transformers prints a one-line note when the image model loads from the video checkpoint; it
  is harmless (the outlines are right) but noisy.
- A full-suite run once timed out in scripts/tests/test_launcher.py while other jobs were
  running; it passes alone (23 of 23) with and without these changes.

## Commits

```
6256dd3 Rohit Sudhakar 2026-10-31T20:04:36-04:00 2026-10-31T20:04:36-04:00
c8ad2ff Rohit Sudhakar 2026-10-31T20:02:30-04:00 2026-10-31T20:02:30-04:00
0f323e3 Rohit Sudhakar 2026-10-31T19:58:17-04:00 2026-10-31T19:58:17-04:00
```

This report is committed after the log above was taken.
