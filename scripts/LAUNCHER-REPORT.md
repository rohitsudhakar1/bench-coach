# Launcher report

Contract proposals for the lead: document the existing capture/note `GET /health`
endpoints and hub `GET /parts` response (`checked_at`, per-part `alive` and
`status: ready|sending|quiet|offline`). Launcher readiness uses app `/state` and the
other parts' `/health`; preflight needs `/parts`. No event contract was changed.

## Delivered

All new files are under `scripts/` and remain uncommitted. `demo.sh` uses `BENCH_PY`
or `python3`; Python code uses the selected environment without local machine paths.
The README has Run the demo and Table day instructions and a dependency/licence list.
No package installation, model download, speaker playback, or external API call was
needed for this work.

The launcher starts available capture, app, vision, angle, coach and note in order,
waits for JSON health, appends stdout/stderr to per-part logs, rejects occupied ports,
and owns each child's process group. Ctrl-C, SIGTERM, startup failure and fatal child
exits all clean up those groups. Coach and note each restart once; replacement health
has its own deadline. A second crash stops the demo. Missing entry points print skips.
Package mains are preferred over service modules; inert service files are ignored.
Note is launched with `serve`. Angle starts on capture feeds and uses vision boxes
unless manual shaft ROIs are supplied.

Both grouped and repeated file-source arguments work. Files loop with a silent fake
mic by default. A WAV mic file overrides silence and leaves coach listening. Camera
indices, exclusions, custom port base/run folder, hints, table mode, table source,
manual angle ROIs and optional angle calibration are wired through. Table camera
indices already owned by capture map to capture JPEG URLs. Table mode requires its
saved layout and app for run lifecycle; missing table code is explicitly skipped.
Replay posts ordered events directly with original spacing and current epoch times,
then keeps the services running. Replay preserves all events, including run events;
it does not invoke capture recording endpoints.

Preflight checks advancing, fresh, distinct camera feeds; table caps at saved regions;
magenta band visibility; calibration geometry, image sizes and error metrics; five
seconds of microphone PCM followed by cached local transcription of “testing”; every
required hub part; more than 2 GB of free disk; and protocol loading. Independent
checks continue after failures. Explicit skips print no PASS. The async overall limit
is 105 seconds, HTTP requests are bounded, and speech runs in a child with a 45-second
inference deadline and kill/reap cleanup. No sound is played.

## Validation

Final command, with `BENCH_PY` set to the prepared environment:

```sh
mkdir -p runs
"$BENCH_PY" -m pytest scripts/tests -q -p no:cacheprovider --basetemp=runs/launcher-tests
```

Result: **40 passed in 34.89 seconds** (19 launcher, 21 preflight cases).
An earlier invocation of this command failed during temporary-directory setup because
`runs/` did not yet exist; creating that parent fixed the invocation. No service was
started by that failed setup.

Fake-service integration tests bind only available ports within 8800–8899. They cover
startup order, URL/port propagation, replay clock and delivery, coach restart limit,
coach/note replacement-health timeouts, occupied ports, startup timeout, log files,
Ctrl-C, and termination of a descendant that ignores SIGINT. All test-owned children
were stopped and service listeners checked for release.

The actual capture, angle and note entry points also passed a smoke test using two
generated 160×120, 10 fps, 12-frame videos and a fake mic. The test waits past video
EOF and requires the capture JPEG timestamp to remain less than one second old,
then shuts down all three and checks port release. This validates CLI compatibility
and looping, not recognition or note accuracy.

Preflight tests use fake HTTP responses and generated cap images, stub the pending
table detector API, and fake microphone/transcription processes. They cover stale,
frozen and duplicate cameras; missing/displaced caps and wrong size; invalid RMS;
quiet/stale hub health; silence/wrong words; malformed mic PCM; inference timeout
cleanup; missing table code; invalid protocols; disk at the 2 GB boundary; continued
reporting; and explicit skips. A successful fake-service preflight takes about three
seconds for its camera sampling; the complete test suite's timing above includes all
repeated samples. No physical readiness or real speech recognition was measured.

`sh -n scripts/demo.sh`, both CLIs' `--help`, and `git diff --check` passed.
The required tracked-file public-path scan returned no matches. An additional scan
covered the new untracked scripts, tests and documents and returned no matches.

## Remaining integration and physical checks

App, vision and coach are absent in this worktree. Their command shapes and table
helper interfaces follow the earlier draft and are exercised with fakes; rerun a
complete demo after those parts merge. The table helper import is lazy so its absence
produces FIX lines without preventing other preflight checks. No real table detector,
Whisper inference, USB camera, physical microphone, calibration pencil check, or
end-to-end lab-note generation was tested here.

Health proves service availability, not working inference. Camera source names and
different images cannot prove two physical lenses; cover each lens before presenting.
Calibration error bounds cannot detect a moved camera or incorrect gravity reference.
Table detections do not establish liquid transfer. The on-disk protocol check does not
prove the dashboard has that same protocol selected. A cached speech model is required;
missing weights fail without attempting a download. Replay keeps original payload
references, so old recording paths and note URLs may no longer resolve.

No sample footage is bundled or required for these tests. README examples use
`BENCH_SAMPLES`. No files outside the launcher worktree were edited; the other checkouts
were read-only references. No commits, pushes, history rewrites, identity changes or
date overrides were made.

## Git log audit

Ran in this worktree before finishing:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output was empty: there are no branch-only commits. All delivered files are uncommitted
for the lead.
