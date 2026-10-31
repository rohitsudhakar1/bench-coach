# Florence-2: finishing the evaluation

No change to docs/EVENTS.md. The default detector stays Grounding DINO tiny. Florence-2 is still
one flag away (`--detector florence2`) and now drops the boxes it is unsure of.

## Recommendation for the demo

Use Grounding DINO tiny on both cameras with `--new-draw`, as now. Florence-2 draws better boxes on
the labelled frames, but on the held-out clips it makes draw and dispense detection worse, down to
what random timing would give, and it does not reach 3 runs a second per camera with two cameras.
Putting Florence-2 on one camera and tiny on the other only helps or hurts in the same way, at
2.6 runs a second per camera.

## 1. Telling Florence-2 when a thing is not there

Florence-2's phrase grounding writes out a phrase and then its box. It gives no score per box,
but it does pick each word with some probability. `vision/zoo.py` now keeps those probabilities
while it generates (a logits processor, so no extra model call) and gives each box the
probability of the first word of its phrase: how sure the model was that it should name that
thing next, as against moving on or stopping. A box that copies an earlier box of another name
gets a tenth of that, because Florence-2 often draws "waste bin" on the same spot as the tip box.

The boxes are the same as before on every frame checked (38 of 38). Scores spread out as hoped:
hands sit near 0.99, while the median waste box scores 0.24 and the median plate 0.23.

A cut per name was picked on the tuning frames only (`vision/eval/florence_cut.py`, coordinate
search, the same merit as `compare.py`): tip box 0.3, waste 0.9, nothing for pipette or pipette
tip (a cut there only lost pipettes). These are `zoo.FLORENCE_CUTS`, used by the live part.

Held-out labelled frames (64 frames, 4 clips):

| | Pipette found | Wrong boxes | Waste boxes wrong | Tip box right |
| --- | --- | --- | --- | --- |
| Grounding DINO tiny (default) | 38 of 58 | 0.65 (130 of 199) | 34 of 34 | 0.26 |
| Florence-2, every box | 42 of 58 | 0.45 | 36 of 36 | 0.67 |
| Florence-2 with the cuts | 42 of 58 | **0.28** | 0 kept | 0.67 |

On the held-out tip clips the cuts also stop 14 wrong tip_off events (all 14 Florence-2 fired
were wrong; they came from the waste boxes).

Caution: none of the labelled clips has a waste bin, so the waste cut was picked on clips where
every waste box is wrong. It has never been checked against a real bin. The pipette score did not
separate right from wrong pipettes: Florence-2 boxes a pipette on 92 to 100% of frames on the bench
clips, where tiny boxes one on 37 to 83%.

A second short prompt, or Florence-2's region-to-category task, was not tried: either costs at
least one more model call per frame (region-to-category one per box), and Florence-2 is already
the slow one. The word probabilities cost nothing extra.

## 2. Draw, dispense and tips on the held-out clips

Florence-2 was run over every held-out file (1,367 detector frames, main and container prompt,
written to `vision/eval/cache-florence2s/` with each box's numbers). The tuning clips were not
run, so nothing for Florence-2 was tuned on them; the draw and tip rules are the ones tuned with
tiny. Results are in `vision/eval/florence-results/`. Chance is the same events at random times.

Draw and dispense with `--new-draw` (held-out, 7 streams, 18 draws and 22 dispenses labelled):

| | Draw right | Draw found | Dispense right | Dispense found |
| --- | --- | --- | --- | --- |
| tiny | 15 of 29 (0.52, chance 0.26) | 15 of 18 (0.83, chance 0.43) | 10 of 24 (0.42, chance 0.30) | 10 of 22 (0.45, chance 0.32) |
| Florence-2 with cuts | 7 of 24 (0.29, chance 0.26) | 7 of 18 (0.39, chance 0.36) | 4 of 19 (0.21, chance 0.29) | 4 of 22 (0.18, chance 0.26) |
| Florence-2, every box | 7 of 26 (0.27, chance 0.26) | 7 of 18 (0.39, chance 0.38) | 5 of 17 (0.29, chance 0.28) | 5 of 22 (0.23, chance 0.23) |
| Florence-2 main + tiny containers | 7 of 21 (0.33, chance 0.27) | 7 of 18 (0.39, chance 0.33) | 6 of 20 (0.30, chance 0.30) | 6 of 22 (0.27, chance 0.27) |

Without `--new-draw` both detectors find almost nothing (tiny 2 of 18 draws, Florence-2 4 of 18).

With Florence-2 the draw and dispense numbers sit at chance. The loss is on the bench clips (pcr49
0 of 4 draws found against 4 of 4, hood033 0 of 3 against 1 of 3). Swapping in tiny's container
boxes did not bring it back, so the cause is Florence-2's pipette boxes, not its container boxes:
it puts a pipette box on nearly every frame, and the tip end the draw tracker follows comes from
that box. Florence-2 also gives one box per container phrase, so it misses all but one tube.

Tips (held-out, 9 clips, 16 tip_on and 13 tip_off labelled; hand_touch where labelled):

| | tip_on right | tip_on found | tip_off | hand_touch right | hand_touch found |
| --- | --- | --- | --- | --- | --- |
| tiny | 5 of 17 (0.29, chance 0.08) | 5 of 16 (chance 0.09) | 0 of 1, found 0 of 13 | 5 of 11 (0.45, chance 0.24) | 5 of 33 |
| Florence-2 with cuts | 4 of 19 (0.21, chance 0.08) | 4 of 16 | none fired, found 0 of 13 | 5 of 13 (0.38, chance 0.21) | 5 of 33 |
| Florence-2, every box | 4 of 19 | 4 of 16 | 0 of 14 | 5 of 14 (0.36) | 5 of 33 |

Tip pick-up is a little worse with Florence-2 and tip_off is not found by either.

## 3. Florence-2 on one camera, tiny on the other

Built from the two caches with no model run (`florence_cut mix Left|Right`): on the two-view clips
one view's boxes come from Florence-2 (with cuts) and the other's from tiny; one-camera clips take
the detector of that camera (left: Florence-2, right: tiny).

| | Draw right / found | Dispense right / found | tip_on right / found | hand_touch right / found |
| --- | --- | --- | --- | --- |
| Florence-2 left, tiny right | 7 of 24 / 7 of 18 | 4 of 19 / 4 of 22 | 4 of 18 / 4 of 16 | 4 of 12 / 4 of 33 |
| tiny left, Florence-2 right | 15 of 29 / 15 of 18 | 10 of 25 / 10 of 22 | 5 of 18 / 5 of 16 | 6 of 12 / 6 of 33 |

Each split follows whichever detector sees the bench clips: no gain from mixing. On the one
true two-camera stream (mv414) the counts are the same either way (2 of 2 draws found).

## Speed

`vision.eval.speed` on 20 frames, one after the other, load average about 7: tiny 145 ms a call,
Florence-2 238 ms (215 ms in the earlier report, under different load; keeping the word
probabilities did not show a cost I could separate from the load). With two cameras sharing one
detector: tiny 3.5 runs a second per camera (1.7 with the container prompt), Florence-2 2.1 (1.0),
Florence-2 plus tiny 2.6. Only tiny passes 3 a second. I did not touch `vision/detector.py`'s
batching; another builder is on speed.

## How to rerun

```
$BENCH_PY -m vision.eval.recache --detector florence2 --out cache-florence2s --budget 95 <stems>  # repeat
$BENCH_PY -m vision.eval.florence_cut tune                      # picks cuts on tune, writes cache-florence2-cut
$BENCH_PY -m vision.eval.florence_cut mix Left --out cache-mix-left
VISION_EVAL_CACHE=cache-florence2-cut VISION_EVAL_MIN_SCORE=0 $BENCH_PY -m vision.eval.score_actions --per-clip
VISION_EVAL_CACHE=cache-florence2-cut VISION_EVAL_MIN_SCORE=0 $BENCH_PY -m vision.eval.score_tips --per-clip
```

The cut and mix caches are made from the committed ones in seconds, so they are not committed. In
the result files, the "tune" numbers for the Florence-2 setups are empty or partial because the
tuning clips were not run with Florence-2; only "hold" counts.

## Limits

- One person labelled the frames and actions. 18 draws and 22 dispenses held out; the draw gap
  (15 against 7 found) is the clearest number here, the tip numbers are a few events each.
- The draw tracker was tuned with tiny. A tracker tuned on Florence-2 boxes might do better; that
  needs Florence-2 over the tuning clips too (about 1,100 more frames, roughly 9 minutes).
- Nothing was downloaded; Florence-2 base was already on disk. Nothing added to the environment.
- Tests: `vision/tests/test_zoo.py` covers the phrase scores and the cuts. 100 pass, 1 skipped.

## Date check

The commit list that was here was removed when the repo moved to Rohit's account; see the README's history note.
