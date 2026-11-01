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

## Models and Wi-Fi off

`fetch_models.py` lists every model the default demo loads, with its size and licence:
Grounding DINO tiny (690 MB, Apache-2.0) and the MediaPipe hand landmarker (8 MB,
Apache-2.0) for vision, faster-whisper small.en (486 MB, MIT) for the coach and the
preflight microphone, and the Silero VAD file (2 MB, MIT) that ships inside
faster-whisper. `--with florence2 gdino-base owlv2` adds the optional detectors.

```sh
"$BENCH_PY" scripts/fetch_models.py               # what is here and what is missing
"$BENCH_PY" scripts/fetch_models.py --download    # fetch the missing ones, then check
"$BENCH_PY" scripts/fetch_models.py --check       # load each with the network blocked
```

Hugging Face models go to the normal Hugging Face cache (`HF_HOME` or `HF_HUB_CACHE`
move it); the hand model goes to `vision/models/`. `--check` sets `HF_HUB_OFFLINE=1`
and `TRANSFORMERS_OFFLINE=1`, refuses every DNS lookup and connection that is not to
this machine, loads each model in its own process and runs it once on a blank input.
It fails if a file is missing, a load fails, or anything tried the network.

When every model the started parts need is on disk, the launcher sets those two flags
for the parts and prints `models: all on disk, parts start offline`. Without them a
cached Grounding DINO still loads with Wi-Fi off, but only after about 70 seconds of
retries, past the startup deadline. A flag you set yourself is kept. If a model is
missing the launcher says so and the part downloads it on first start. With the flags
set, vision's hand loader refuses to download and names the fix. Preflight runs the
same check as its `offline ready` line (`--no-offline` skips it). YOLOE and the
unadopted tip model are not covered. Hosted wording and answers need a key and the
network; without either they fall back to the rule-based text.

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

One package was added for the R2 download (`scripts/requirements.txt`): boto3
(https://github.com/boto/boto3, Apache-2.0), used only for `list_objects_v2`, `get_object` and
`download_file` against R2's S3-compatible endpoint. Its tests use a fake client and need no
network. Existing dependencies used here:

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
| huggingface_hub | https://github.com/huggingface/huggingface_hub | Apache-2.0 | fetch_models cache lookups and downloads |
| Transformers | https://github.com/huggingface/transformers | Apache-2.0 | fetch_models offline load of the detectors |
| MediaPipe | https://github.com/google-ai-edge/mediapipe | Apache-2.0 | fetch_models offline load of the hand model |

Services retain their own dependency and model licence lists. The launcher uses the
standard library, plus fetch_models' file lookups when vision or coach start; preflight uses the libraries above and the parts' table and
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

## Ten-minute soak

```sh
"${BENCH_PY:-python3}" scripts/soak.py --base-port 8840
```

Set `BENCH_SAMPLES` to the sample folder first. The default pair is
`lsv_mv/CP-{Left,Right}-Front-414-445.mp4`; `--source LEFT RIGHT` selects another pair.
`--seconds` defaults to 600, `--run-seconds` to 120, and `--sample-seconds` to 5.
All six ports must be within 8800–8899. The output directory must be new and under
this worktree's `runs/`; `--output runs/my-soak` chooses its name.

The script launches `scripts/demo.sh` with real vision, hints, angle and note,
looped video and silent microphone. It removes hosted-service keys from the child
environment and uses cached model weights only. Prepare the models beforehand.
The measured interval begins after all services and the hub websocket are ready.
It stops and starts recording every two minutes, stops the final recording, gives
the final note up to 30 seconds to finish, and shuts down the launcher.

Outputs are `samples.csv`, `summary.json`, `summary.txt`, `launcher.log`,
`processes.jsonl`, and the recordings/logs beneath `recordings/`. These are ignored
runtime artifacts. The launcher's optional `--process-log FILE` appends only part,
PID and start timestamp for every start, including replacements; it needs an existing
parent directory. It avoids global process enumeration in restricted environments.

CPU is the change in each service process's user+system CPU time divided by elapsed
wall time; 100% means one core. RSS is resident process memory in MiB, not total GPU
allocation, and excludes subprocesses. Missing measurements stay blank, including
the first CPU/rate sample. Camera rates are differences in capture sequence counters,
not the camera's advertised FPS. Detector rates are differences in vision's consumed
result counters per camera: these count completed detection results seen by the
pipeline, not every internal model forward pass or superseded asynchronous result.
Hub event rate counts messages received on `/ws`; reconnects are reported and can
lose events. Disk size is logical file bytes under the entire worktree's `runs/`.

Progress counters are probed roughly every half second to record gaps over three
seconds, including gaps still open at shutdown. HTTP calls have two-second timeouts;
gap timing has polling/network uncertainty and is not an exact latency trace. Quiet
hub or detector intervals are reported as gaps, not automatically diagnosed as hangs.
Angle and coach can legitimately be quiet, and note runs only on stop. This checks
stream progress, not every thread's liveness or recognition accuracy.

Memory summaries give start/peak/end RSS and a least-squares slope over the second
half. “Steady growth” means three increasing median blocks in that half and a slope
over 1 MiB/min. This heuristic does not prove a leak; allocator warmup, caches and
bounded history can grow too. On macOS, memory pressure is checked every sample;
under 40% free the script halts the soak and cleans up. No speaker is opened.

The soak adds [psutil](https://github.com/giampaolo/psutil) (BSD-3-Clause) for direct
PID CPU/RSS reads, listed in `scripts/requirements.txt`. HTTPX, websockets and pytest
are already listed above. No package was installed for the measured run.

## Demo video from a recorded run (9am checklist)

`scripts/render_demo.py` turns one or two camera files and a protocol into a finished 1080p
video: every object outlined and named, both hands drawn, the protocol steps ticking off on the
right with their times, warnings as they happen, and the lab note at the end. It runs the same
vision, coach and note code as the live demo, offline, so nothing needs to be started first.

### Recording

1. Camera angle: side-on or 45 degrees from the front, about 60-80 cm from the work, at bench
   height or a little above. The pipette, the tip box and every tube must be in frame the whole
   time, and the hands must not leave it. A second camera from the other side helps when one
   hand hides the tubes.
2. Light: bright and even. Overhead lights on, no window behind the bench, no strong shadows on
   the tubes. Blue or purple gloves are fine.
3. Hold the phone or Pi camera still on a stand. Landscape, 1280x720 or 1920x1080, 30 fps.
4. Before pressing record, write the protocol as numbered lines in a text file, one action
   per line, naming the tubes the way they are labelled (for example `2. Draw 100 uL from tube A`).
   Lines that say attach/eject a tip, draw/collect from and dispense/add into are the ones the
   cameras can tick off.
5. Start both cameras, wait two seconds, then do the steps at a normal pace. Change tips where
   the protocol says so. Stop recording two seconds after the last step.
6. Copy the files to the Mac (AirDrop or `scp` from the Pi), or let the Pi upload them to R2
   and pull them as below.

### From the Raspberry Pi through R2

The Pi uploads each recording as one session: a USB camera video, a ribbon camera video, the
microphone's sound and a `sync.json` that says when all three started. `scripts/r2_pull.py`
lists and downloads them. It only reads the bucket; it never uploads or deletes.

It needs `boto3` (`BENCH_PY="$BENCH_PY" scripts/addpkg.sh boto3`) and four environment variables:
`R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Keep them in a private
file outside the repo and load it with `set -a; . path/to/r2.env; set +a`. Without them the
tools stop with a one-line message saying what is missing.

1. After Rohit stops recording, wait until the Pi says the upload is done, then check it is there:

   ```sh
   "$BENCH_PY" -m scripts.r2_pull --list
   ```

   Newest first: device, session id, start time in local time, length, and the size of each
   file. A USB file of about 1 MB or less for 30 seconds still has every frame, but the picture
   is dark or the lens is covered (two of the Oct 31 test sessions look like this); turn the
   lights on and record again.
2. One command fetches the newest session and renders the finished video, with the USB camera
   and the ribbon camera as the two views and the recorded sound under the footage:

   ```sh
   "$BENCH_PY" -m scripts.render_demo --r2-latest --protocol protocol.txt --out ~/Desktop/bench-coach-demo.mp4
   ```

   `--r2-session e259bf` picks a session from the list instead of the newest; `--r2-device`
   limits it to one Pi. Downloads go to `$BENCH_R2_CACHE` (default `~/.cache/bench-coach/r2`)
   in a folder per session, and are not downloaded again on a re-run. `--audio file.m4a` adds
   sound to any render. The sound starts with the first frame of footage, after the title card.
3. To run the live demo on the same recording instead:

   ```sh
   "$BENCH_PY" -m scripts.r2_pull --latest --out ~/bench-r2
   ./scripts/demo.sh --source ~/bench-r2/current/usb.mp4 ~/bench-r2/current/ribbon.mp4
   ```

   `current` always points at the last session pulled.

### The command

```sh
"$BENCH_PY" -m scripts.render_demo --video cam1.mp4 --video2 cam2.mp4 \
  --protocol protocol.txt --out ~/Desktop/bench-coach-demo.mp4
```

`--video2` is optional. `--title "PCR master mix"` sets the name on screen. `--seconds 60` renders
only the start (for a quick look). Re-running with `--reuse` skips the vision pass and only redraws
(seconds). A protocol `.yaml` from `protocols/` works too. Everything else lands next to the
output in `<name>-work/` (events, the lab note as HTML and Markdown, the vision pass).

If a step the cameras missed has to be in the video, write it in a labels file, one JSON object
per line, and add `--labels labels.jsonl`:

```
{"s": 12.5, "kind": "tip_on", "object": "pipette"}
{"s": 20.0, "kind": "draw", "object": "tube A"}
```

`s` is seconds into the video. Steps decided by these lines say "hand-labelled" in amber on
screen, and the credits line says hand-labelled events were added. Never present them as detected.

On this Mac a 2-minute clip takes about the time measured in `scripts/RENDER-REPORT.md`.
Nothing plays sound and no server is started.

### Filter labels in a render

`python -m scripts.render_demo ... --filter-labels` applies the protocol, hand-size
and temporal rules in `vision/labelfilter.py` before drawing and gates detected
`tip_on` events before the coach pass. It works with `--reuse`; the saved analysis
remains unfiltered. Manually supplied `--labels` events keep their attribution
and bypass this gate. The option is off by default. It removes many real labels
as well as false ones; see [the measurements](../vision/LABELFILTER-REPORT.md).
