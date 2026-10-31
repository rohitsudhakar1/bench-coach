# Calibration page report

No event contract changes are proposed. Work is confined to app files, as the
calibui brief requires. Changes are uncommitted. No packages were installed,
no sound played, no hardware camera or microphone opened, and no server was left
running. All HTTP checks used in-process fake capture; the optional browser test
uses intercepted requests on a check-port URL and opens no listener.

## What works

The main top bar links to `/calibrate`. Both sampled feeds show OpenCV checkerboard
corners on the same images that Capture pair saves. The page includes the printable
board, physical print dimensions, first-level-pose confirmation, progress, pose
instructions, background Compute, good/redo with pixel errors, Use this calibration,
and a fresh-set control. Fit failure cannot replace the installed calibration.

Collection shares a lock and a completion-based 0.5-second detection cooldown across
all tabs. Detection runs in a thread, only while requested, with at most two calls
per second per camera. It rejects missing corners, stale/future/nonadvancing or
skewed timestamps, changed preview revisions and mismatched resolutions. Captured
images, corner arrays and frame times persist together in numbered directories.
The first pair stays the gravity reference. Collection is capped at 40 pairs.

Compute uses the existing angle fitter on saved observations in a separate process,
with a 170-second timeout and shutdown cancellation. Existing angle quality checks
remain authoritative: 12 distinct poses, at least 15 degrees of normal spread,
a baseline of at least one square, and every reported RMS within 2 pixels.
Use writes the existing angle JSON schema atomically to `angle/calibration.json`,
or the server-configured `BENCH_CALIBRATION` path. It retains `physical_check: pending`.

## Measured checks

- `python -m pytest app/tests angle/tests/test_calibrate.py -q -p no:cacheprovider`:
  38 passed, 1 skipped in 3.70 seconds. The skip is the existing external sample-text
  parameterization with no sample files configured.
- Six calibration UI tests cover the real HTTP route flow, real corner detection,
  saved frames, background fitting and angle's file reader; early compute/use,
  first-level confirmation, duplicate captures, stale and skewed frames, missing
  boards, revision conflicts, shared rate limiting, unavailable capture, storage
  failure, mismatched resolution, failed installation, duplicate-pose redo,
  preservation of the prior calibration and worker termination on shutdown.
- Sixteen independently rendered board poses served as JPEGs by fake capture yielded
  stereo RMS **0.155786 pixels**. The saved result loads through `Calibration.load`.
  This is a synthetic regression result, not a physical-camera accuracy measurement.
- `node --check app/static/calibrate.js`: passed.
- `node --test app/tests/note-player.test.cjs`: 2 passed.
- `git diff --check`: passed. The required tracked-file public-path audit returned
  no hits; new calibration sources and tests were also scanned with no hits.

`python -m app.tests.calibration_browser_check` was attempted. Chromium exited with
SIGTRAP during launch because the sandbox denied `bootstrap_check_in`. No page
assertions ran. The repeatable script covers three viewport sizes, first-pair
confirmation, capture, compute, use and reset, and checks page overflow and script
errors when Chromium is allowed to launch. Rendering remains unverified.

## Limits and integration notes

Angle reads calibration only at startup. This app saves the file but cannot make an
already-running angle process reload it: there is no reload API in the current angle
part. Restart angle with the same calibration path. The page states this explicitly.
A future angle-owned reload route would remove that remaining setup step; no event
contract or angle source was changed in this worktree.

The board is the bundled 9 × 6 inner-corner, 25 mm pattern. Custom boards have no UI.
The app does not verify a level bench, correct printing, physical camera identity,
exposure synchronization or the physical pencil check. It shows unavailable RMS
when fitting stops before a valid numerical error exists. Keep camera order and
resolution fixed and redo calibration after moving a camera.

All tabs share one session. Browser reloads retain it; hub restarts start a new set.
Saved sets survive on disk but have no resume, per-pair deletion or cleanup UI.
Start a new set retains earlier images and the previously installed calibration.
Preview pauses while the fit runs. The hub retains its existing loopback,
unauthenticated, single-worker service model.

## Git log

Command executed before finishing:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output was empty: no commits ahead of main. Files remain uncommitted for the lead.
No author settings, dates or history were changed.
