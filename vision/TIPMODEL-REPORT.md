# A small pipette detector trained on bench footage

No change to docs/EVENTS.md.

Result: the tip model was not added as `--detector tipmodel`. On held-out clips it is ten times
faster and draws far fewer wrong boxes, but the live code finds the held pipette less often with
it (30 of 58 labelled frames against 38 of 58), and pipette found is what draw, dispense and
tip_on depend on. The code, the training set builder and the rebuild script are here so the
next round can start from them.

## What was built

- `vision/tipmodel/dataset.py` builds the training set. Grounding DINO and MediaPipe hands were
  run every 2 s on 21 extra clips (lsv_dji, lsv_hood, multicam camA), and the saved model output
  of the six cached tuning clips was used every 0.7 s. A box became a label only when it was a
  "pipette" box (or a "pipette tip" box, which the detector often puts on the whole pipette)
  with score 0.25 or more, at least 1.5 times as long as wide, with a long side of at least 12%
  of the frame, and with a hand point on it. 238 frames from 26 videos were kept.
- Checked by eye: a random sheet of 40 kept frames before the size rule had about 12 wrong boxes
  (thermocycler lid, centrifuge, small blobs, a pipette lying on the bench under a hand). Most of
  those were small, which is why the size rule was added. Expect roughly one label in five to
  still be wrong. Small "pipette tip" boxes were tried as a second class, but on the sheet they
  sat on the pipette body as often as on the tip, so the model learns pipettes only.
- No held-out clip goes in. Every clip held out in `labels.json`, `actions.json` or `tips.json`
  is excluded, and so is the other camera of the same moment. That leaves no micropipette
  footage from the lsv_mv bench (only CP-Left 12-27, which shows a pipette aid), so the lsv_mv
  held-out frames are a bench the model never saw. A test checks the exclusion.
- `vision/tipmodel/train.py` fine-tunes torchvision's Faster R-CNN MobileNetV3-Large FPN (COCO
  weights) at 480 x 640 on MPS: 24 epochs, batch 4, SGD with a one-cycle schedule, random crop,
  flip, brightness, contrast and hue changes. About 8 minutes in two pieces.
- `vision/eval/tipends.json`: the tip end marked by eye on 27 held-out frames (19 in mv414L, 8 in
  hood033), from 3x gridded crops, then checked once more with the point drawn at 4x. Reading
  error is about 5 px. mv249L (serological pipettes running out of the crop), mv12R (pipette aid)
  and one hidden frame were left out.
- `vision/tipmodel/compare.py` runs the comparison below. `vision/tipmodel/model.py` holds
  `TipModel` and `WithTipModel` (Grounding DINO for everything else, the tip model for pipettes),
  ready to be wired in if a later model wins.

## Measured, held-out only

`$BENCH_PY -m vision.tipmodel.compare --json vision/eval/tipmodel-compare.json`, tip model score
threshold 0.5. Frames: both detectors on the same 128-frame label set's held-out frames (mv12R,
mv249L, mv414L, hood033). Live code: the cached held-out clips replayed through `Vision` with the
pipette boxes swapped, scored by `vision.eval.score`.

| Held-out | Grounding DINO tiny (now) | Tip model |
| --- | --- | --- |
| Frames: pipette box on the labelled pipette | 28 of 58 | 31 of 58 |
| Frames: wrong pipette and pipette tip boxes | 95 of 144 (66%) | 3 of 35 (9%) |
| Frames: tip end error, median | 187 px | 44 px |
| Frames: tip end within 20 px | 2 of 27 | 2 of 27 |
| Live code: pipette found | **38 of 58** | 30 of 58 |
| Live code: wrong pipette boxes | 5 of 12 | 2 of 21 |
| Live code: wrong pipette tip boxes | 25 of 64 | none drawn |
| Time a frame (MPS, 640 px) | 178 ms (all eight prompts) | 17 ms (pipettes only) |

The tip end error for Grounding DINO is large because when no box is on the pipette, the
comparison takes its best scoring box, which is often a pipette standing in a rack. Both are
poor at the exact tip end (2 of 27 within 20 px): the live rule takes the end of the box, and
neither model's box ends where the tip ends.

Run on the same held-out clips at thresholds 0.3 and 0.15 (a second and third look at held-out,
so treat these as optimistic): live pipette found 31 and 32 of 58, wrong pipette boxes 5 of 24
and 6 of 25. Still short of 38.

With `--detector tipmodel` the full step would still need Grounding DINO for hands, tip box,
tubes, racks, plates and waste, so the speed gain only comes if those move to a trained model too.

## Why it lost, and what to try next

- The lsv_mv bench has no training footage at all once both cameras of every held-out moment
  are excluded, and that is 50 of the 58 labelled pipette frames. Labelling a few hundred frames
  from other lsv_mv recordings (there are none in the sample folder) is the obvious next step.
- About one auto-label in five is wrong, and auto-labels only exist where Grounding DINO already
  found the pipette, so the model can not learn the frames DINO misses.
- A tip end point (a keypoint head, or a small box on the last centimetre of the tip) trained on
  hand-marked points would address the tip end error directly; the box end rule caps both models.

## Rebuild the weights

Weights are never committed. `models/` is ignored by git.

```
BENCH_SAMPLES=... $BENCH_PY -m vision.tipmodel.rebuild
```

That auto-labels in 150 s pieces, builds `vision/models/tipmodel/dataset.json` and its images,
and trains in 8 minute pieces, waiting whenever `memory_pressure` shows under 40% free. About 25
minutes in all here. Disk: 160 MB for images, checkpoint and weights.

## Sources and licences

- torchvision Faster R-CNN MobileNetV3-Large FPN and its COCO weights, BSD-3-Clause,
  https://github.com/pytorch/vision. The model fine-tuned here.
- Grounding DINO tiny, Apache-2.0, and MediaPipe Hand Landmarker, Apache-2.0, for auto-labels
  (already used by vision).
- No package was added.

## Notes

- Disk was at 4 GB free during training (under the 5 GB floor), from other work on the machine.
  Nothing large was downloaded; the COCO weights (about 75 MB) were the only download.
- `vision/tests/test_tipmodel.py`: the auto-label rule, the held-out exclusion, the augmentation
  and the box swap. Runs without weights or videos.

## Git log

```
GITLOG
```
