# Demo launcher

## Run the demo

Set `BENCH_PY` to a Python environment containing the parts' dependencies, or leave it
unset to use `python3`. Set `BENCH_SAMPLES` to your sample-video folder. From the repo root:

```sh
./scripts/demo.sh --cams 0 1

# Two looped files and a silent fake microphone:
./scripts/demo.sh --source "$BENCH_SAMPLES/multicam_test/camA.mp4" \
  "$BENCH_SAMPLES/multicam_test/camB_sim.mp4"

# Rehearsal ports; optional hints and a recorded microphone:
./scripts/demo.sh --source "$BENCH_SAMPLES/multicam_test/camA.mp4" \
  "$BENCH_SAMPLES/multicam_test/camB_sim.mp4" --base-port 8820 \
  --hints --mic-file tests/voice/testing.wav
```

The last example requires a locally supplied WAV; it is not bundled here. Live cameras
use the real microphone unless `--fake-mic` or `--mic-file` is given. File cameras loop
and default to silence; the coach gets `--mic off` unless a mic file was supplied.
No command enables speaker playback. `--hints` passes `--new-draw` to vision and
`--hints` to coach; the coach asks for confirmation instead of treating a hint as proof.

Wait for **Demo ready**, then open the printed hub URL (normally
http://127.0.0.1:8700). Missing parts print a skip line; if app is missing there is no
web page. Capture, app, vision, angle, coach and note start in that order, each after
its predecessor responds to health. Package `__main__.py` is preferred, then a runnable
`service.py`, with `app.hub` and `angle.run` fallbacks. Note's package gets `serve`.
Angle starts on capture's two feeds and consumes vision boxes by default. Set
`--angle-roi X,Y,W,H` once for camera 0 or twice for both cameras; optionally pass
`--calibration angle/calibration.json`. The ROI must surround the actual shaft.
Uncalibrated estimates are image projections, not physical 3D tilt.

`--fake-vision` starts `scripts/fake_vision.py` in vision's place: a scripted 50-second session with one tilt, one reused tip and one skipped dispense, replayed on every Start; `--fake-speed 3` plays it three times faster. Without `--angle-roi`, angle gets `--need-box` and measures only inside vision's pipette box. See `REHEARSAL.md`.

`--without vision angle` excludes parts; it can be repeated. `--base-port N` moves
all six ports together, keeping the event-contract offsets. All service URLs and the
hub's environment-based probes follow the new ports. Use only 8800–8899 for checks.
`--runs DIR` moves recordings and logs together. `--timeout SECONDS` sets the health
deadline per start, including a restart. HTTP readiness does not prove model accuracy
or camera freshness; use preflight for the latter.

Stdout and stderr append to `runs/logs/<part>.log`. Ctrl-C or SIGTERM stops every
owned process group, including descendants, with INT/TERM/KILL escalation. Existing
listeners are never adopted. Occupied ports, failed startup, or a crashed producer
stop the launcher and its children. Coach and note each get one restart, including
when they fail during startup; the replacement must also become healthy. Run one
launcher per worktree/run directory to avoid mixing recordings and logs.

Replay a saved event file with its relative timing and fresh epoch timestamps:

```sh
./scripts/demo.sh --replay runs/saved/events.jsonl \
  --without capture vision angle coach note --base-port 8820
```

Replay is sent directly to `POST /events`; no `app.replay` module is needed. Events
must be ordered by finite `t` and include `kind` and `source`. Every event, including
run events, is retained; referenced recordings or note links must still exist.
This does not call capture recording endpoints. The hub remains open after playback.
Keep live producers excluded for a replay-only presentation.

## Table day

Use a fixed overhead camera, red/blue/yellow tube caps, and a matte magenta band above
the tip's liquid-contact area. Once vision and app are available, first start capture
so setup can share its feed:

```sh
./scripts/demo.sh --cams 0 1 --without vision angle --base-port 8820
# In another terminal, close the caps and keep the pipette out of view:
"${BENCH_PY:-python3}" -m vision.table setup \
  --source http://127.0.0.1:8821/cam/0/frame.jpg --config vision/table.json
```

Save the layout, stop the launcher with Ctrl-C, then start table tracking:

```sh
./scripts/demo.sh --cams 0 1 --table --table-source capture:0 \
  --without angle --base-port 8820
"${BENCH_PY:-python3}" scripts/preflight.py \
  --capture http://127.0.0.1:8821 --hub http://127.0.0.1:8820 \
  --camera-sources 0 1 --no-angle
```

`capture:1` shares the second camera. A numeric device already owned by capture maps
to that device's capture JPEG URL; a third device is passed to table vision directly.
Preflight needs an existing timestamped HTTP JPEG URL for a separate overhead camera
(`--table-url`). It does not open a second handle to a camera. `--table-config` selects
another layout. Table vision runs with `--follow-runs --realtime` and resets on Start.
Table mode and hints are incompatible. If table code is absent, the launcher skips
vision clearly; preflight reports FIX, never a successful physical setup.

Preflight prints PASS/FIX, returns 0 only when all enabled checks pass, and has a
105-second overall deadline. It starts no server and plays no sound. Close all caps
in the saved regions; at the prompts hold up the band and say “testing” within five
seconds. Apply each FIX and rerun before starting the recording. Open caps without
moving the rack afterward. Confirm the protocol on the dashboard, then Start/Stop a
short run and inspect the note and recordings.

Checks and limits:

- Both JPEG feeds must be at most two seconds old, advance at an observed five fps
  over three seconds, have different source identities, and contain different images.
  `--min-fps` changes the rate floor. Different source names cannot prove separate
  physical cameras: cover each lens to confirm it. `--camera-sources` must match capture.
- Caps use the installed table detector and saved regions/resolution in
  `vision/table.json`. The marker uses that detector's magenta threshold. This is a
  visibility check, not proof of liquid transfer or correct tip attachment.
- Angle calibration must load, match both image sizes, and have finite global and
  per-pair errors no greater than two pixels. Pass `--calibration` for a different file.
  Complete the physical pencil check in `angle/CALIBRATE.md` as well.
- Microphone input comes from capture's websocket, with fresh PCM headers and a
  speech-level signal. Cached local faster-whisper must transcribe “testing”; use
  `--whisper-model` for its local directory. No download is attempted. Inference runs
  in a CPU subprocess, killed and reaped after 45 seconds.
- Hub `/parts` must be fresh and list every required part as alive and ready/sending.
  Quiet producers during a run fail. Disk must have more than two decimal GB free;
  `--disk DIR` selects the recording filesystem. `--protocol FILE` loads the YAML
  and validates nonempty steps; the coach's loader is also used when available.

`--no-table`, `--no-angle`, and `--no-mic` omit only those deliberate checks, with no
false PASS lines. `--no-angle` also removes angle from required hub health. General
vision is still required with `--no-table`. For silent camera demos use launcher
`--fake-mic` and preflight `--no-mic`. The default protocol is
`protocols/pipetting-demo.yaml`. Readiness does not establish detection accuracy.

## Tests and dependencies

```sh
"${BENCH_PY:-python3}" -m pytest scripts/tests -q -p no:cacheprovider
sh -n scripts/demo.sh
```

Tests use generated video, fake HTTP/WebSocket responses, fake speech transcription,
and subprocess fake services on free ports within 8800–8899. They also smoke-test
actual capture, angle and note on generated footage with a fake mic. They stop their
own children and verify listener release. No sample-data download, physical device,
API key or model inference is needed. Table detector calls are faked until vision is
merged; no table recognition accuracy is claimed.

No packages were added. Existing dependencies used here:

| Library | Source | Licence | Use |
| --- | --- | --- | --- |
| Python | https://www.python.org | PSF-2.0 | CLI, supervision, replay, filesystem checks |
| HTTPX | https://www.python-httpx.org | BSD-3-Clause | Preflight HTTP and fake responses |
| OpenCV | https://opencv.org | Apache-2.0 (wheel packaging MIT) | JPEG decoding, generated test videos |
| NumPy | https://numpy.org | BSD-3-Clause | Image comparisons and PCM samples |
| PyYAML | https://pyyaml.org | MIT | Protocol parsing |
| websockets | https://websockets.readthedocs.io | BSD-3-Clause | Capture mic stream |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | MIT | Optional cached local speech check |
| pytest | https://docs.pytest.org | MIT | Tests |

Services retain their own dependency and model licence lists. The launcher uses the
standard library alone; preflight uses the libraries above and the parts' table and
calibration helpers. No third-party source was copied into this directory.

## Table end-to-end regression

```sh
"${BENCH_PY:-python3}" -m pytest tests/test_table_e2e.py -q -p no:cacheprovider
```

This test creates an overhead video, calibrates its first frame with `vision.table setup`,
and runs capture, hub, table vision, coach and note through `run_demo.py --table on`
on ports 8860–8865. Both capture inputs use the generated video. No `BENCH_SAMPLES`,
API key, model, microphone or speaker is needed. Occupied ports fail before launching.
The test checks named tube events from capture pixels, five done and three skipped
steps, tip reuse and skipped dispense warnings, the note, recordings, capture timestamps,
and port release. It has a 90-second work deadline and a two-minute total limit.
See `TABLE-E2E-REPORT.md` for measured results and limits.

`--table on` and bare `--table` enable table mode; `--table off` disables it.

## Several benches

```sh
./scripts/demo.sh --benches 3 --base-port 8820 \
  --source "$BENCH_SAMPLES/multicam_test/camA.mp4" "$BENCH_SAMPLES/multicam_test/camB_sim.mp4" \
  --fake-vision --without angle note
```

Open `http://127.0.0.1:8820/class`. One hub serves all benches. Each has its own capture,
vision, angle, coach and note processes (except excluded parts), with log names such as
`capture-2.log`. Bench B uses base plus `6 * (B - 1)` for its service block; only the first
block starts a hub. Three benches at 8820 therefore use ports through 8837. Startup,
health checks, one coach/note restart, and shutdown apply separately to every process.

`--source LEFT RIGHT` supplies the same looped files to every bench. Repeat
`--bench-source ID LEFT RIGHT` to override individual benches. Without file inputs,
cameras default to pairs 0/1, 2/3, 4/5, etc.; `--bench-cams ID LEFT RIGHT` overrides a
pair. Duplicate physical device numbers are rejected. Give each bench at most one
input override, using IDs 1 through N. `--benches 1` keeps the original behavior.
Classroom mode requires the hub and defaults to silent microphones; physical per-bench
microphone selection is not implemented. `--mic-file` shares one prerecorded input.
Speaker playback remains off.

The launcher sends every producer to `http://HOST:BASE/benches/ID`, with bench-specific
capture and note addresses. The hub adds IDs to events, filters subscriptions, and
stores unique runs in the shared runs directory. Other root APIs remain bench 1.
Per-bench calibrations saved in the classroom UI are loaded from
`runs/calibrations/bench-ID.json` on restart; an explicit `--calibration` overrides all.
Shared table calibration/ROI flags are unchanged; configure appropriate geometry before
using them on physically different benches. This check uses scripted actions, not measured
recognition on the sample videos.
