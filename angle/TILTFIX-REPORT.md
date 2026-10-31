# Tilt fix

No event-contract changes proposed. Changes are confined to angle/ and left uncommitted.

## Cause

The saved hint sweep contains fresh pipette-tip boxes incorrectly marked `held:true`.
The held flag was neither missing nor bypassed. The launcher passes `--need-box`, does
not pass `--always`, and vision draw/dispense hints intentionally open the same two-second
technique-check window as ordinary events. In the two reported cases:

| Estimate | Box age | Activity age | Box label | Held |
| --- | --- | --- | --- | --- |
| 55.49 degrees | 0.301 s | 1.202 s | pipette tip | true |
| 88.75 degrees | 0.414 s | 1.304 s | pipette tip | true |

Both measured views used vision crops. Neither was a stale crop or full-frame fallback.
The earlier tripod checks did not reproduce these false held classifications in the
looped capture-feed sweep. Hand adjacency on a wrong object is still not pipette identity.

The saved stream also contains five angle readings after Stop, including 68.79 degrees.
Stop cleared the cache, but continuing vision events immediately populated it again.

## Changes

Normal measurement now requires a fresh, explicitly held vision crop in every measured
view, plus a draw/dispense event or hint within two seconds. Missing, false, malformed or
expired held evidence cannot fall back to a manual tracker or full-frame edge. Existing
per-camera cache rejection and frame freshness checks remain in place.

Every result above 45 degrees is logged to stderr as a probable mis-detection and dropped
before display or publication. EventGate independently enforces the same ceiling, so it
cannot forward a bad angle to the coach, which can produce its own warning from an angle
event. Rejection does not consume either event or warning cooldown. Exactly 45 degrees
remains eligible; the normal warning threshold remains strictly above 20 degrees.

After Stop, new vision observations are ignored until Start. Before the first run event,
standalone hub-fed operation remains available. The explicit `--always` diagnostic still
supports file/manual-ROI checks without vision or activity, but the 45-degree ceiling also
applies there. The demo launcher does not enable that diagnostic.

## Checks

- Angle suite: 57 passed in 3.60 s, with BENCH_SAMPLES set. Covers hint and ordinary draw/
  dispense activity, manual fallback suppression, held-box loss and expiry, activity expiry,
  stop/restart, display suppression, rough and stereo publication, and the 45-degree boundary.
- Saved sweep replay: all nine original angle candidates were checked against their original
  ordered vision/run events. Two high readings during the run were logged and rejected;
  five post-stop readings were blocked; the two remaining readings were 3.34 and 11.92 degrees.
  No warning survived. This reuses recorded measurement values, not a new pixel-level replay.
- `tests/test_modes_smoke.py -k hints`: 1 passed, 5 deselected in 26.23 s. Actual capture,
  vision, angle, coach, hub and note ran on check ports. The saved smoke stream contained
  16 objects events, one draw hint and one dispense hint, zero angle events and zero warnings.
  The test checked pages, downloads, notes, no tracebacks, shutdown and port release.
- The first smoke attempt skipped because this new worktree lacked the hand-model asset.
  For the passing run, the existing cached asset was copied under ignored angle/runs/ and an
  ignored sitecustomize hook pointed vision.hands.MODEL there in the test and its children.
  No vision source was changed and no model was downloaded. The initial unit invocation
  also needed its missing basetemp parent created; the full rerun above passed.
- `git diff --check` passed. The required tracked-file public-path audit returned no matches;
  new report, fixture and test files were checked separately. No packages were added, sound
  was played, or servers left running.

The fixture `tests/sweep_hints.jsonl` preserves event order and relative times from the saved
sweep. It retains only objects, activity, run status and measured-angle fields. Run names,
absolute timestamps, paths, speech and unrelated events are omitted.

## Reproduce

With BENCH_PY and BENCH_SAMPLES set and the vision models already cached:

```sh
mkdir -p angle/runs
"$BENCH_PY" -m pytest angle/tests -q -p no:cacheprovider --basetemp angle/runs/pytest-tiltfix
"$BENCH_PY" -m pytest tests/test_modes_smoke.py -k hints -q -rs -p no:cacheprovider \
  --basetemp angle/runs/smoke-tiltfix
```

## Limits

This is suppression, not improved recognition. A wrong object marked held may still yield
an angle at or below 45 degrees during a false hint. The brief's ceiling also suppresses
any real extreme tilt. No physical angle accuracy or better held-pipette recall is claimed.
The fresh smoke run covers the existing eight-second hint-mode test, not the full original
30-second sweep. No vision thresholds or shared event contract were changed.

## Git log

`git log --format='%h %an %aI %cI' main..HEAD` returned empty output.
No commits, identity changes or history rewrites were made.
