# Dispense hints

## Proposed changes to docs/EVENTS.md (not made)

None. Nothing here changes an event's shape. A dispense hint may now be left out when
`--dispense-min-dwell` is set, which the contract already allows (hints are optional).

## Result in one paragraph

Nothing tried made dispense better on the held-out clips in the way that counts for a person
using the coach, so the default is unchanged and every number in the scorecard stays where it
was. One check, sending a dispense hint only when the tip stayed in for at least 0.4 s, raised
held-out dispense precision from 0.42 to 0.54 (chance 0.30 and 0.33) but dropped recall from 0.45
to 0.32, and in hint mode it cost two right questions (8 of 17 down to 6 of 15). It is there as
`--dispense-min-dwell 0.4` for anyone who prefers fewer, more often right dispense hints. It
is off by default.

## Where the dispense errors come from

`vision.eval.dispense_signals` replays the `--new-draw` tracker and keeps every group of entries
the sequencer names, before it names them, with the labelled action each overlaps.

- The tracker's entries are mostly real: 39 of 54 groups on the tuning clips and 42 of 54 held
  out overlap a labelled draw or dispense. The trouble is the name.
- The name comes from taking turns (draw, dispense, draw...). It is right on 22 of 39 real groups
  on tuning and 24 of 42 held out. Calling every group a draw gets 18 and 25. So the turn order
  is about as good as a fixed guess.
- Why: one dip often shows up as two groups (about a third of the real groups are a second
  piece of the same action), and many real dispenses are never seen. Each extra or missed
  entry flips every later name until the next fresh tip.

## Each signal, checked on the labelled actions first

For each signal, the draw and dispense medians on real groups, and how often a cut halfway
between them puts a group on the right side (0.50 is no help). Tuning, then held out.

| Signal (what the brief suggested) | Tuning | Held out | Verdict |
| --- | --- | --- | --- |
| Target differs from source: distance to the entry before, in container sizes | draw 1.89, dispense 1.32; 0.65 | draw 1.04, dispense 0.65; 0.67 | Weak but steady. Dispenses sit closer to the entry before them, not farther. |
| Dip is shorter: time in the container | draw 0.7 s, dispense 1.1 s; 0.56 | draw 0.8 s, dispense 0.4 s; 0.57 | Flips between the two halves. No use for naming. |
| Thumb presses down: thumb change while in | 5 draws, 6 dispenses with finger points; 0.64 | 4 and 6; 0.70 | Finger points are found on about one group in four, too few to lean on. |
| Pipette rises empty: how far the tip rose after leaving | both 0.0; 0.54 | both about 0; 0.52 | No signal. The pipette box is usually lost as soon as the tip leaves. |
| Pipette tilts toward a wall: box width over height | 0.65 and 0.65; 0.51 | 0.64 and 0.59; 0.55 | No signal from the box shape. |
| Time since the entry before | draw 0.85 s, dispense 1.7 s; 0.56 | 1.85 s and 2.5 s; 0.56 | Weak. |

Rows are in `eval/dispense_signals-tune.json` and `eval/dispense_signals-hold.json`.

## What was tried in the sequencer

All in `DrawRules` (`draw.py`), all off by default. `vision.eval.sweep_dispense` tries each one,
picks on the tuning clips only (best dispense F1 while draw F1 drops no more than 0.02) and prints
the held-out numbers next to it. Full table: `eval/sweep_dispense.json`.

| Setting | What it does | Tuning dispense right / sent (F1) | Held-out dispense right / sent (F1) | Held-out draw |
| --- | --- | --- | --- | --- |
| none (today) | take turns | 11 / 25 (0.49) | 10 / 24 (0.43) | 15 / 29 |
| `merge_gap` 0.2 to 1.0 s | a group right after the last one is the same action, no event, no flip | 10 / 24 down to 5 / 16 (0.46 to 0.28) | 8 / 22 down to 5 / 18 (0.36 to 0.25) | 14 / 26 down to 10 / 21 |
| `same_place` 0.25 to 1.0 | back where the last draw (or dispense) was: no event, no flip | 0.46 to 0.24 | 0.32 to 0.17 | 11 / 24 down to 6 / 15 |
| `by_place` 0.25 to 2.0 | name by place: near the first draw after a tip is a draw, else a dispense | 0.43 to 0.17 | 0.37 to 0.14 | as low as 5 / 12 |
| `near_last` 0.5 to 2.0 | name by distance: near the entry before is a dispense | 0.47 at best | 0.44 at best | precision 0.37 at most |
| `disp_near` 3.0 | send a dispense only near the entry before | 10 / 20 (0.50) | 9 / 22 (0.41) | unchanged |
| `disp_min_gap` 0.6 to 2.0 s | send a dispense only after a pause | 0.35 to 0.40 | 0.42 to 0.45 | unchanged |
| `disp_min_dwell` 0.4 s (picked on tuning) | send a dispense only when the tip stayed in 0.4 s | 10 / 16 (0.55) | 7 / 13 (0.40) | unchanged |

Anything that changes the names (merging, place, distance) hurts draws a lot, because the turn
order is so fragile that fixing it in one spot breaks it in another. The `disp_` checks only leave
a dispense hint out and still flip the turn, so draws do not move at all.

The brief expected dispense dips to be shorter. The data says the opposite for the hints that
matter: the false dispense hints are the short ones (median 0.55 s on tuning and 0.40 s held out,
against 0.70 s and 0.90 s for the right ones). That is why a minimum stay helps precision.

## Before and after on the held-out clips

`vision.eval.score_actions` with 1.5 s tolerance, chance from the same events at random times.

| | dispense precision | dispense recall | draw precision | draw recall |
| --- | --- | --- | --- | --- |
| before (today's default) | 0.42 (10 of 24), chance 0.30 | 0.45 (10 of 22), chance 0.32 | 0.52 (15 of 29), chance 0.26 | 0.83 (15 of 18), chance 0.43 |
| `--dispense-min-dwell 0.4` | 0.54 (7 of 13), chance 0.33 | 0.32 (7 of 22), chance 0.19 | 0.52 (15 of 29), chance 0.27 | 0.83 (15 of 18), chance 0.44 |

## Hint mode, with the coach's evaluation

`vision.eval.hint_dispense` runs the scorecard's hint replay (a simulated person who only answers
yes or no 2 s after each question) and splits the questions by kind.

| | held out: questions about a real action | by kind | steps ticked | tuning |
| --- | --- | --- | --- | --- |
| before | 8 of 17 (0.47), chance 0.31 | draw 5 of 12, dispense 3 of 5 | 8 of 40 | 9 of 16, ticked 9 |
| `--dispense-min-dwell 0.4` | 6 of 15 (0.40), chance 0.32 | draw 4 of 11, dispense 2 of 4 | 6 of 40 | 9 of 15, ticked 9 |
| `disp_near` 3.0 | 8 of 17, same as before | same | 8 of 40 | same |

Two things here are different from what the brief assumed. In hint mode, dispense questions are
not mostly wrong: 3 of 5 held out were about a real dispense. The wrong ones are mostly the
draw questions (7 of 12 wrong). And the coach asks only 5 dispense questions over 5.4 minutes,
so a change of one question moves that share by 20 points. Leaving out short dispense hints
removed one right dispense question and also shifted which draw got asked about, for two fewer
right questions overall. That is why it stays off.

## What would help next

- The tip end and plunger are hidden on most entries in these clips, so box shape and finger
  points cannot see the thumb press or the empty rise. A crop check on each hint (the Claude
  check in `verify.py`, which exists but has never run with a key here) is the one tool that
  looks at the picture itself. It was not measured.
- Fixing the turn order needs fewer split dips at the tracker, not at the sequencer. Merging at
  the sequencer was tried and hurts.
- More labelled clips. Held out there are 22 dispenses and 5 dispense questions. One event moves
  precision by 4 to 8 points.

## How far to trust it

The tuning clips picked the setting; the held-out clips were only read to decide whether to turn
it on, and they said no. The held-out numbers for `--dispense-min-dwell` are therefore a fair
reading, but on 13 events. The labels are the earlier draft's, by one person, and 21 of them are
marked unsure (draw against dispense is often inferred from the protocol order).

## Other notes

- The scorecard was rerun. Every vision and hint row is unchanged. The "Coach: real vision
  events" row now reads 0 of 4 steps, not 1 of 4. That comes from the earlier commit that sent
  tip_on and tip_off at 0.5 confidence, under the coach's proof line; the starting commit of this
  branch already gives 0 of 4. Nothing in this branch touches the default rule.
- The tracker now records on each entry the thumb change, the lowest thumb value, how far the tip
  rose after leaving, and the box lean. No event changes because of this.
- Tests: 5 new in `tests/test_draw.py`. 148 vision and coach tests pass.
- No sound played, no package added, no port used, nothing left running.

## How to rerun

```
$BENCH_PY -m vision.eval.dispense_signals --split hold --rows
$BENCH_PY -m vision.eval.sweep_dispense
$BENCH_PY -m vision.eval.score_actions --set '{"disp_min_dwell": 0.4}'
$BENCH_PY -m vision.eval.hint_dispense --set '{"disp_min_dwell": 0.4}'
BENCH_SAMPLES=<sample video folder> $BENCH_PY scripts/scorecard.py
```

## Date check

The commit list that was here was removed when the repo moved to Rohit's account; see the README's history note.
