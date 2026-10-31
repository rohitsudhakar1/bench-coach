# scorecard report

No change to docs/EVENTS.md proposed.

## What works

- `scripts/scorecard.py` writes `SCORECARD.md`: 28 rows covering capture, vision (default rule,
  `--new-draw`, frame events, table mode), angle (stills, synthetic stereo), coach, note, app (hub
  and protocol converter) and the nvidia task export. Each row has the data kind, how much data,
  the number, a chance number where there is a precision, a one-line meaning and what was not
  measured. Held-out and tuning rows are separate rows.
- It runs in about 6 s with `BENCH_SAMPLES` set (3 s without; capture and converter rows then
  say skipped). Limit was 2 minutes.
- Coach rows: "waiting for coach/" until `coach/` is in the repo, then they fill in. Checked
  against the unmerged coach checkout with `--coach`: scripted mistakes 8 of 8 steps right and
  all three warnings; clean run 8 of 8, no warnings; real vision events on the 414-445 pair 1 of
  4 steps; hint mode held-out 17 questions, 8 about a real action (47%, chance 0.31), 8 of 40
  steps ticked by a yes; tuning 16 questions, 9 real (56%, chance 0.32). These numbers can change
  as the coach part changes.
- 7 tests in `scripts/tests/test_scorecard.py`, under 1 s.

## Measured (from the committed SCORECARD.md)

- Vision `--new-draw` draw, held-out: precision 0.52 (chance 0.26), recall 0.83. Dispense 0.42
  (chance 0.30), recall 0.45. The earlier draft looked at the held-out scores when keeping filters,
  so these are optimistic. Default rule recall about 0.1.
- Vision frames, held-out: pipette found on 38 of 58; 65% of boxes on the wrong thing; touches 0 of
  3 right.
- Table mode, synthetic: 27 of 40 made-up runs exactly right, precision 0.95, recall 0.98, after
  making the band pass over other tubes and step just outside each place. With a cleaner path
  it was 40 of 40, which only showed the test was too easy.
- Angle stills (tuning): 9 of 12, mean error 1.3 deg against 21.5 deg for guessing the average.
  Synthetic stereo: 0.87 deg mean with 0.5 px noise.
- Capture on the two 414-445 files: timing rows match the saved videos, the two views stay within
  one frame (worst 33 ms) of the same clip moment.
- Converter: 23 of 23 steps right on whether a camera can check them, 16 of 16 on the action, but
  the key is mine and covers only six short texts.
- NVIDIA tasks: reference 1.00, blank 0.00, rules solver 0.89 on 9 tasks.

## Not done

- No speech (Whisper) row and no whole-demo rehearsal row; listed under "Not in the table".
- Hint mode simulates only a person who answers questions; the "also says done" case is not scored.
- The protocol-converter key and the coach scripted sessions were written by me, not an outside reader.

## Git log

`git log --format='%h %an %aI %cI' main..HEAD` before this report's commit:

```
65e3bf3 Andy Bei 2026-10-30T20:37:09-04:00 2026-10-30T20:37:09-04:00
```
