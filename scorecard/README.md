# scorecard

`scripts/scorecard.py` measures every part on the data in this repo and writes `SCORECARD.md` at
the repo root. It runs the parts' own code; it does not change how any part behaves.

```sh
BENCH_SAMPLES=<sample video folder> $BENCH_PY scripts/scorecard.py      # about 6 s
$BENCH_PY scripts/scorecard.py --json numbers.json                      # raw numbers too
$BENCH_PY scripts/scorecard.py --coach <a coach checkout>               # coach not merged yet
$BENCH_PY -m pytest scripts/tests/test_scorecard.py -q
```

Without `BENCH_SAMPLES` the capture and protocol-converter rows say skipped; everything else
comes from files in the repo (vision's cached model output and labels, angle's stills, the note's
scripted run, nvidia's sample runs). The coach rows say "waiting" until `coach/` is in the repo,
then fill in on their own. No key is used, nothing plays sound, no port is opened.

What each row rests on is in its Data column: held-out, tuning, real (no split), scripted or
synthetic. Precision rows carry a chance number: the same events at random times, or the simple
guess named in the row.

Libraries: none beyond what the parts already use (numpy, OpenCV, FastAPI's test client, PyYAML),
plus Python's own `difflib` for matching table-mode event sequences.
