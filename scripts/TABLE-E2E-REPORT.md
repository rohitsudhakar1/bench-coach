# Table end-to-end report

No event-contract changes proposed. Files are uncommitted.

## Result

`tests/test_table_e2e.py` generates a 480 × 360, 10 fps overhead video with red,
blue and yellow tube caps, a tip box, waste and a moving magenta pipette band.
It calibrates the video's first frame through the real headless setup command.
Both capture inputs use that video, so no external sample files are required.
The launcher runs capture, hub, table vision, coach and note with `--table on
--base-port 8860`. Vision reads capture's camera 0 JPEG URL. Angle is excluded.
The test starts and stops a run through the hub's real controls; it injects no
vision, coach or note events.

Observed sequence: tip on, draw tube A, dispense tube B, draw tube C with the
same tip, eject without dispensing into B. All five observations have table
provenance, confidence 0.75 and timestamps present in capture's camera 0 timing
rows. Coach marks steps 1, 2, 3, 6 and 8 done from vision; steps 4, 5 and 7 are
skipped. It warns about reuse before C and skipped step 7. The automatically
built note lists both problems and the same eight step statuses. Both videos,
silent microphone recording, event log, timing log and HTML note exist.
The test checks no vision events before Start, inactive vision after Stop,
launcher exit and release of ports 8860–8865.

## Integration fixes

1. The launcher already passed `--port` and `--follow-runs`, but table vision
   rejected them. Table vision now serves health using the existing helper,
   polls hub run state, opens/resets on Start and releases its source on Stop.
   File EOF waits for another run. Standalone runs retain their existing behavior.
2. The brief's `--table on` invocation was rejected by the boolean-only parser.
   The launcher now accepts `on` and `off`, retaining bare `--table` as enabled.
3. An immediate rerun failed because the launcher's port probe treated TCP
   TIME_WAIT connections as occupied ports after every service had exited.
   Its probe now uses SO_REUSEADDR, like the servers. Regression tests check
   recently closed connections and rejection of a genuine listening socket.

## Verification

With `BENCH_PY` pointing to the shared environment:

```sh
"$BENCH_PY" -m pytest tests/test_table_e2e.py vision/tests/test_table.py scripts/tests/test_launcher.py -q -p no:cacheprovider --durations=3
```

26 passed in 36.53 seconds. The end-to-end case took 25.42 seconds, below two
minutes. An earlier successful run of the integration and table unit tests
reported 6 passed in 25.46 seconds. The intervening rerun exposed fix 3 above.

After adding the explicit TIME_WAIT regression:

```sh
"$BENCH_PY" -m pytest scripts/tests/test_launcher.py -k 'port_probe or occupied_port or explicit_table' -q -p no:cacheprovider
```

3 passed, 18 deselected in 0.12 seconds. No new packages, model downloads,
hosted API calls, physical cameras or speaker playback. Test children have
hosted API keys removed from their environment. Dependencies and licences are
listed in `scripts/README.md` and `vision/README.md`.

## Limits

This proves synthetic pixel-to-note integration, not real-bench recognition or
liquid movement. The two feeds intentionally use the same video; no stereo or
camera-identity claim is made. Capture playback is independent of Start, so the
video has a 15-second idle lead-in and loops. The work deadline is 90 seconds,
with bounded child cleanup and a total-time assertion below 120 seconds.
Occupied ports fail before launch rather than selecting a different port block.

Run state is polled every 0.1 seconds. A stalled HTTP source can delay Stop by
the existing reader's ten-second timeout. Health reports loaded configuration,
run identity and frame count, not camera freshness. The integration exercises
one run; repeated-run reset and real camera setup still warrant rehearsal.
No changes were made to the event contract or protocol.

## Repository checks

`git diff --check` passed. The required tracked-file path scan returned no hits;
new test and report files were also scanned. No generated media or calibration
is included in the changes. All owned services stopped.

Required date audit:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty. No branch-only commits, history rewrites or date overrides.
