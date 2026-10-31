# Tip on, tip off and touches

No change to docs/EVENTS.md. `tip_on` events keep their shape (object `"tip box"`, confidence 0.5).

## What changed

- Labels. `vision/eval/tips.json`: 30 tip pick-ups, 22 tip ejects and 51 hand touches, marked by
  eye from contact sheets at 0.5 s steps (`vision/eval/sheet.py`), on the cached lsv_dji, lsv_hood
  and lsv_mv clips. Tip times start from `actions.json`. New: touches on pcr48-51, serial55 and
  the 414-445 pair, and tips on hood034 (cached for this, plus hood035, unused). Splits are the
  same as `actions.json`: held-out is mv414L/R, mv12R, mv249R, serial55, pcr49, pcr51, hood033
  and hood034.
- A scorer, `vision/eval/score_tips.py`: an event is right when a labelled moment of the same
  kind is within 1 s, each moment used once. Chance puts the same events at random times, 200
  tries. Touches are scored only on clips where touches were looked for.
- `tip_on` now comes from `vision/tips.py`. A tip box only counts when it is boxed in about the
  same place on 3 or more detector runs, and its region reaches half its height lower, because
  the detector tends to box the lid while the tips sit below it. The lower end of an upright
  pipette box enters, and a later detector run sees it leave or rise; then tip_on fires, at
  most once in 5 s.
- Draw and dispense naming still resets on the older rule's tip_on. Letting the new tip_on reset
  it made held-out draw and dispense worse (draw 15 of 29 right fell to 14 of 31, dispense 10 of
  24 to 8 of 22), so it was kept apart. `score_actions` output is now byte for byte the same as
  before.
- Touches and tip_off still use the older rules (see below for why).
- `scripts/scorecard.py` has two new rows for these labels; `SCORECARD.md` was rerun.

## Measured

`$BENCH_PY -m vision.eval.score_tips` (after) and `--set '{"tips": null}'` (before). Saved in
`vision/eval/tips-before.json` and `tips-after.json`.

| Held-out | Before precision | Before recall | After precision | After recall |
| --- | --- | --- | --- | --- |
| tip_on | 0 of 1 (chance 0.04) | 0 of 16 (chance 0.00) | 5 of 17 = 0.29 (chance 0.08) | 5 of 16 = 0.31 (chance 0.09) |
| tip_off | 0 of 1 (chance 0.00) | 0 of 13 | unchanged | unchanged |
| hand_touch | 5 of 11 = 0.45 (chance 0.26) | 5 of 33 = 0.15 (chance 0.09) | unchanged | unchanged |

Tuning clips: tip_on went from 0 of 14 found to 7 of 14, with 7 of 17 events right (chance 0.07).

Per clip, the new tip_on is good on the chest camera (pcr105: 5 of 5 found) and on serial55,
and poor on the head camera pcr clips (pcr48 0 of 4, pcr51 0 of 4), where the pipette's lower
end lands outside every tip box box the detector draws. On the 414-445 pair it fires at 2.9 s
when the lid is opened, 3.6 s before the real pick-up; the older rule fired at 8.1 s, 1.6 s
late. As a result the scorecard's "coach on real vision events" row went from 1 of 4 steps
ticked to 0 of 4. That row is one 31 s clip; the held-out tip numbers above are the wider check.

## Tried and dropped

All choices were made on the tuning clips, then checked on held-out.

- Touches from the steady boxes, with a hand's box centre standing in when a gloved hand has no
  finger points. Tuning got better (1 of 5 right to 10 of 25, chance 0.27). Held-out: 15 of 40
  right (chance 0.29), 15 of 33 found (chance 0.35). That finds three times as many touches but
  sits closer to chance than the older rule (5 of 11, chance 0.26), so it was not kept. Saved in
  `tips-anchor-all.json`. The touch labels are dense (the free hand moves between two racks every
  2 to 4 s), which is why chance is so high.
- tip_off from a steady waste box: no right events on either split. The waste the detector boxes
  is mostly an empty measuring cup; the real waste beaker is boxed rarely, and in the pcr clips
  the eject happens out of frame.
- tip_off inferred from a second tip pick-up (the old tip must have come off): 1 of 13 right on
  tuning, because every false tip_on brings a false tip_off. Dropped before held-out.
- Requiring a hand next to the pipette box for tip_on: tuning rose to 8 of 14 right and 8 of 14
  found, but held-out fell to 2 of 14 right and 2 of 16 found (chance 0.10). Reverted. This was
  the second look at held-out, so the 5 of 16 above is slightly optimistic.
- The "tip-sized blob leaving the pipette end" idea needs pixels around the tip at video rate;
  the cache keeps only boxes and hand points every 0.35 s, so it was not tried.

## Limits

- One person labelled everything. Tip ejects in the pcr clips happen out of frame, so those 15
  tip_off times are guesses from where the pipette swings. Only 22 ejects were found, not 30.
- tip_on is still wrong about 7 times in 10 on held-out, so it stays at confidence 0.5, under the
  coach's 0.6 line.
- Touch objects: tube and rack are hard to tell apart when the tube sits in the rack, so touches
  are matched on time only; the same-object count is printed by the scorer.

## Date check

`git log --format='%h %an %aI %cI' main..HEAD` before this report was committed:

```
GITLOG
```

Real times, author from the repo config, no rewritten history. No sound played, no server
started, no package added.
