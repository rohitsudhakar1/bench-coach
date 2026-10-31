# Stronger detectors

No change to docs/EVENTS.md. The default detector is still Grounding DINO tiny, so every vision
number in SCORECARD.md is unchanged (the scorecard was rerun; only capture timing wobbled).

## What was built

- `--detector` on `python -m vision`: `gdino-tiny` (default), `gdino-base`, `owlv2`, `florence2`,
  `yoloe`, `ensemble:<a>+<b>`. The adapters are in `vision/zoo.py` and all return the same boxes
  with the same names, so nothing downstream changed.
- `vision/eval/recache.py` reruns any detector on exactly the frames in `vision/eval/cache/`, keeps
  the hands, and writes `vision/eval/cache-<name>/`. Rerunning tiny this way gave the same boxes
  as the saved cache on all 38 frames checked, so the frames line up.
- `VISION_EVAL_CACHE` and `VISION_EVAL_MIN_SCORE` point every scorer at another cache and box cut.
- `vision/eval/compare.py` scores detectors side by side through the normal replay and scorer.
  Each model's box cut is picked on the tuning clips only, then the held-out clips are scored once.
  `--merge a b` builds an ensemble cache from two caches with no model run (not committed; rebuild it with this).
- `vision/eval/speed.py` times detectors one after another on the same 20 frames.
- Tests: `vision/tests/test_zoo.py` (merging, OWLv2 phrase mapping, the option). 68 pass, 1 skipped.

## Measured

Held-out half of the 128 labelled frames (64 frames, 4 clips), through the same scene clean-up and
pipette choice the live part uses. "Wrong boxes" counts pipette, pipette tip, tip box and waste
boxes not on that thing; these clips have no waste bin, so every waste box is wrong.

| Detector | Licence | Pipette found | Wrong boxes | Tip box right | Tuning: pipette, wrong | ms a call |
| --- | --- | --- | --- | --- | --- | --- |
| Grounding DINO tiny (now) | Apache 2.0 | 38 of 58 | 0.65 (130 of 199) | 0.26 (23 of 89) | 33 of 43, 0.58 | 138 |
| Grounding DINO base | Apache 2.0 | 31 of 58 | 0.55 (138 of 250) | 0.22 (28 of 128) | 30 of 43, 0.47 | about 245 |
| OWLv2 base, 2-3 phrasings each | Apache 2.0 | 32 of 58 | 0.64 (48 of 75) | 0.00 (0 of 29) | 32 of 43, 0.41 | about 315 |
| Florence-2 base | MIT | **42 of 58** | **0.45 (67 of 149)** | **0.67 (43 of 64)** | 36 of 43, 0.45 | 215 |
| YOLOE-26 large | AGPL-3.0 | 5 of 58 | 0.67 (16 of 24) | 0.25 (5 of 20) | 7 of 43, 0.14 | about 220 |
| ensemble tiny + Florence-2 | | 45 of 58 | 0.59 | 0.37 | 36 of 43, 0.53 | about 450 |
| ensemble tiny + base | | 37 of 58 | 0.63 | 0.31 | 33 of 43, 0.58 | about 440 |

Speed: the 138 ms (tiny) and 215 ms (Florence-2) figures come from `vision.eval.speed`, run
one after the other on the same frames, at long side 640. The "about" figures are averages taken
during the cache runs while other builders were using the machine, so they come out high. A
second rough comparison: tiny averaged about 200 ms during the same cache runs where Florence-2
averaged 254.

With two cameras sharing one detector, tiny gets 3.6 detector runs a second per camera and
Florence-2 2.3 (1.8 and 1.2 with the `--new-draw` container prompt as well). Florence-2 misses
the 3 per camera mark.

What the numbers mean:

- Florence-2 base is clearly the best box model here. It finds the pipette on 4 more held-out
  frames, puts 31 pipette, tip and tip box boxes on the wrong thing where tiny puts 96, and its tip box boxes are
  right 2 times in 3 against 1 in 4 for tiny (the phone on the chest is no longer boxed as a tip box).
- But Florence-2's phrase grounding draws one box for every phrase on every frame, whether the
  thing is there or not. It has no score to cut on. Every waste box is wrong on these clips
  (36 of 36), and on a bench with no tip box it will still box something. It almost never boxes
  a pipette tip on its own (5 boxes held out).
- Grounding DINO base is less sure of itself on these frames than tiny. At tiny's cut it finds
  almost nothing, and at its best cut (0.1, picked on tuning) it finds fewer pipettes than tiny.
  On a standard test picture it works normally, so this is the footage, not a loading fault.
- OWLv2 boxes more pipettes than tiny on tuning, but every held-out tip box was wrong.
- YOLOE finds almost no pipettes, and it is AGPL. It is in the code only so the result can be checked.
- The tiny + Florence-2 ensemble finds the most pipettes (45 of 58), but tiny's wrong tip boxes
  come back, and it costs both models' time.

## Default: unchanged

The rule was to switch only if held-out results improve and speed holds. Florence-2 improves the
boxes but not the speed with two cameras, so `gdino-tiny` stays the default and Florence-2 is one
flag away (`--detector florence2`). With a single camera Florence-2 gets about 4.6 runs a second, so
it is the better choice for a one-camera setup.

Draw, dispense and tip numbers with Florence-2 were not measured. That needs Florence-2 over all
22 cached files (about 4,300 calls, roughly 18 minutes alone), and the run was stopped half way
when the coordinator asked builders to wrap up: the machine was overloaded and disk was under 5 GB.
Only the labelled-frame caches were kept. Those numbers come from:

```
BENCH_SAMPLES=... $BENCH_PY -m vision.eval.recache --detector florence2 --all --budget 170   # repeat until done
VISION_EVAL_CACHE=cache-florence2 VISION_EVAL_MIN_SCORE=0 $BENCH_PY -m vision.eval.score_actions --per-clip
VISION_EVAL_CACHE=cache-florence2 VISION_EVAL_MIN_SCORE=0 $BENCH_PY -m vision.eval.score_tips
```

## Next things worth trying

- Run Florence-2 on both cameras in one batch. That could bring it back over 3 a second per camera.
  Not tried.
- Ask Florence-2 for the main and container phrases in one caption, which would make the
  `--new-draw` container prompt free. Not tried.
- Use Florence-2 for the pipette and tip box and tiny for the pipette tip and hands, rather than
  merging everything.
- Half precision on MPS made Florence-2 no faster (350 ms both ways under load), so it is not used.

## Limits

- One person labelled the frames, 64 held-out frames from 4 clips. A difference of 4 pipette
  frames is real but small.
- Speed was measured on a shared, busy Mac (load average about 11). The ranking held across both
  ways of timing it.
- Downloaded for this: Grounding DINO base (0.9 GB), Florence-2 base (0.46 GB), YOLOE-26 large with
  its text encoder (0.33 GB, kept outside the repo). OWLv2 was already on disk. Nothing was added
  to the Python environment.

## Date check

`git log --format='%h %an %aI %cI' main..HEAD` before this report was committed:

```
e4cdd4f Andy Bei 2026-10-31T02:34:28-04:00 2026-10-31T02:34:28-04:00
427185c Andy Bei 2026-10-31T02:08:53-04:00 2026-10-31T02:08:53-04:00
13f6097 Andy Bei 2026-10-31T02:06:57-04:00 2026-10-31T02:06:57-04:00
```
