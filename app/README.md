# Bench Coach app

The event hub and bench monitor. Run from the repository root with the shared Python environment:

```sh
$BENCH_PY -m app.hub
```

The page is at `http://127.0.0.1:8700`. Use one process and one worker. All browser tabs share that hub's run. Start and Stop control capture as well as the event log. A failed capture request leaves event logging available and puts a notice on the page. A restored active run reports camera recording as unknown; stop it before starting another.

For private checks use ports 8800–8899, for example:

```sh
$BENCH_PY -m app.hub --port 8880 --capture http://127.0.0.1:8881 --note http://127.0.0.1:8885 --runs /tmp/benchcoach-app-runs
```

Stop the process with Ctrl-C. No sound is played by this part. No package installation or API key is needed.

## Routes and storage

| Route | Behavior |
| --- | --- |
| `POST /events` | One event or a batch of at most 1,000. Validates the envelope and finite JSON numbers. Returns `accepted`. Producers cannot post `run` or `run-config`. |
| `GET /events?since=<epoch>` | Events strictly newer than the timestamp, in arrival order. Keeps the latest 10,000 in memory. Read the JSONL file for the archive. |
| `WS /ws` | Each new event, in order. Slow clients close with 1013; reconnect and load `/state`. |
| `GET /state` | Run, recording, capture result, protocol, steps, current step, open question, warnings, angle, boxes, conversation, note and recovery flag. |
| `POST /run/start` | Optional `{protocol: "pipetting-demo.yaml"}`; otherwise the selected protocol. Conflicting starts return 409. |
| `POST /run/stop` | Stops capture before publishing the stop event. No active run returns 409. |
| `POST /protocol` | `{text, name?}` converts, saves and selects a protocol. Loading during a run returns 409. |
| `GET /capture-status` | Proxies capture camera and recording status together; unavailable capture returns 503. |
| `GET /config` | Capture browser URL, selected protocol and filename. |
| `GET /parts` | Parallel, cached probes for capture, vision, angle, coach and note, plus hub health. |
| `GET /health` | Hub readiness and recording flag. |
| `GET /lab-note/{run}` | Read-only note proxy. Defaults to Markdown wrapped as JSON. `?format=html`, `pdf`, or `zip` selects another form. |

Runs default to repo-root `runs/<run>/events.jsonl`; `--runs` or `BENCH_RUNS` overrides the parent directory. Start, stop and subsequent events are flushed and fsynced before broadcast. Late note events append to their own existing run, even after a newer run starts. Other late producer events belong to the active/latest run because the shared event envelope has no run ID.

Startup finds the latest start timestamp across logs, then rebuilds state from that run. It restores the protocol snapshot, progress, conversation and note without duplicating log entries. Invalid rows are skipped; an incomplete final row gets a newline before later appends. A recovered run stays active until Stop. The hub does not issue another capture start on recovery. An unstarted imported selection remains as YAML but is not restored after restart; the latest run or demo is restored instead.

`CAPTURE_URL`, `NOTE_URL`, `VISION_URL`, `ANGLE_URL`, and `COACH_URL` configure upstream addresses. CLI `--capture` and `--note` override their variables. `CAPTURE_PUBLIC_URL` changes the camera address used by browsers. Each health probe has a 0.5-second HTTP timeout and a 0.6-second total deadline; concurrent callers share a two-second cache. Green means reachable and ready/sending. Vision, angle and coach turn amber during a run after ten seconds without an event. Capture frames travel separately and note normally waits for Stop, so those parts can remain ready. Grey means unreachable or explicitly not ready. Tooltips explain the result. Health is not a recognition accuracy check.

The hub binds to loopback by default and has no authentication. The note service owns note creation; the app never calls `POST /note`. The proxy accepts an event URL on the configured note origin and otherwise uses `NOTE_URL/note/<run>`. It never reads an event-supplied local file. HTML is sandboxed. Videos and key frames resolve against the note service, which must also be reachable by the browser. Download run proxies the note service ZIP; its contents are the note service's responsibility.

## Page

Original local HTML, CSS and JavaScript use familiar shadcn/Tailwind-style cards, buttons, badges, tabs and a modal dialog. No CDN, web font or build step. The desktop grid targets 1920×1080 and 1440×900 without document scrolling, with internal scrolling for long steps or notes. Phones stack the panels. Browser rendering could not be verified in this sandbox; see REPORT.md.

Two contained camera images share SVG source-pixel coordinates with their overlays. Boxes expire after three seconds. The current step is the main heading. Steps show seen, said, or seen + said only from coach evidence labels; raw vision hints never tick a step. The open confirmation question has its own panel. Warnings use large text, expire after 15 seconds and clear immediately on stop. Late warnings after stop remain in the log but do not reopen the banner.

Stop selects Lab note automatically, including on reconnect. A note event adds the ready badge and loads readable HTML with PDF and run download links. Inline synthetic note text is also supported. The note adapter understands the earlier renderer's `#vids`, `#player`, and timestamp `data-off` map. It creates two muted players; each timestamp seeks both to their own recorded offsets and leaves them paused. It waits for metadata and clamps to duration. Missing media leaves the note readable. If the rebuilt note renderer changes that markup, its owner must retain the adapter contract or supply its own dual-player behavior.

## Protocol conversion and replay

```sh
$BENCH_PY -m protocols.convert --source protocol.txt --no-llm
$BENCH_PY -m app.replay --source app/samples/demo.jsonl --hub http://127.0.0.1:8880 --start-run --speed 2
```

`protocols/convert.py` uses numbered instructions or separate sentences/lines, preserves the instruction text, and extracts tip, draw, dispense, explicit object, wait and angle rules. Unsupported actions get empty evidence. `ANTHROPIC_API_KEY` enables a schema-guided Claude request; `ANTHROPIC_PROTOCOL_MODEL` overrides the configured model (`claude-haiku-4-5`). This model setting was not tested against a live API. Timeout, HTTP failure or invalid output falls back to rules without printing keys or provider errors. No key is used by the tests.

Imported evidence entries remain alternatives, as in the earlier coach. A compound step can finish after one matching event. Rules cannot establish liquid volume, reagent identity, sterility or completion of every sub-action. Names in the written protocol may differ from camera labels. Review the conversion. All three available MV texts and all eight DJI texts have schema and conversion tests; this is not a semantic accuracy score.

Replay accepts JSONL, shifts timestamps to the current Unix clock and preserves spacing divided by `--speed`. It skips original lifecycle/configuration/note events; `--start-run` makes and stops a new run. Failed event deliveries print JSON to stdout. Without the hub use replay without `--start-run`. Video files belong to capture, not the hub/replay input. The bundled sample contains explicitly synthetic evidence and does not prove recognition accuracy.

## Checks

```sh
$BENCH_PY -m pytest app/tests -q -p no:cacheprovider
node --check app/static/app.js
node --check app/static/note-player.js
node --test app/tests/note-player.test.cjs
# Optional rendering, on a machine where Chromium can launch:
CHROMIUM_PATH=/path/to/chrome-headless-shell $BENCH_PY app/tests/browser_check.py
```

The browser check intercepts requests with synthetic fixtures, starts no server, opens no camera/mic, and writes screenshots to `/tmp/benchcoach-app-browser` (`BENCH_BROWSER_ARTIFACTS` overrides it). It checks the three viewport sizes, desktop overflow, camera alignment, stop/tab transition, downloads and two silent timestamp seeks. It closes its browser in `finally`.

## Libraries

No packages were added. Dependencies already exist in the shared environment. No source was copied from Labprint, and no footage or model weights are included.

| Library | Source | Licence | Used for |
| --- | --- | --- | --- |
| FastAPI | https://github.com/fastapi/fastapi | MIT | HTTP and WebSocket routes |
| Starlette | https://github.com/encode/starlette | BSD-3-Clause | Responses, static files and TestClient |
| Pydantic | https://github.com/pydantic/pydantic | MIT | Event and protocol validation |
| Uvicorn | https://github.com/encode/uvicorn | BSD-3-Clause | ASGI server |
| HTTPX | https://github.com/encode/httpx | BSD-3-Clause | Capture, health, note, Claude and replay requests |
| PyYAML | https://github.com/yaml/pyyaml | MIT | Protocol files |
| pytest | https://github.com/pytest-dev/pytest | MIT | Route and conversion tests |
| Playwright | https://github.com/microsoft/playwright | Apache-2.0 | Optional browser checks only |
| Python standard library | https://www.python.org | PSF-2.0 | Logging, clocks, concurrency and CLI |
| Node.js standard library | https://github.com/nodejs/node | MIT, with bundled notices | JavaScript tests only |

The visual component patterns are implemented locally; this app does not bundle shadcn or Tailwind code. Claude is an optional hosted service, separate from these library licences.

## Training tasks in Lab note

Stopping a run queues `nvidia.export_tasks.export_run` and the offline rules solver in a
separate process. **Training tasks (NVIDIA)** lists each question, its checker, the mean
rules-solver reward, and a ZIP of the complete Harbor task folder. The ZIP includes its
answer key and reference solution, but no recordings or raw speech. The section works
without the note service. Answer keys reflect the event log, not independent bench truth.

**Make synthetic runs** generates 20 runs from the selected stopped run, with seed 0 and
the synth module's default prior of 1. The table compares its smoothed recorded skip rates
with the rates measured in the generated runs. With one recorded run and only 20 generated
runs, differences can be large. The panel names the actual engine and note generator.
`nvidia/.venv/bin/python` is selected for synthesis when present; otherwise the hub's Python
is used. The synth module selects Data Designer when importable and the local NumPy generator
otherwise. No packages are installed by the app. See `nvidia/README.md` for that optional
environment, dependencies, licences, and NVIDIA API key behavior.

| Route | Behavior |
| --- | --- |
| `GET /nvidia/{run}` | Export and synthesis states: idle, queued, running, ready, error. |
| `POST /nvidia/{run}/export` | Queue an export or retry a failed one; returns 202. |
| `POST /nvidia/{run}/synth` | Queue 20 synthetic runs or retry failure; returns 202. |
| `GET /nvidia/{run}/tasks/{task}.zip` | Download one exported task folder. |

Jobs run one at a time with a 170-second limit. Repeated requests share the pending job;
completed results are reused. Closing the hub cancels jobs and kills its worker process.
Successful results persist under `runs/<run>/nvidia-ui/`; task folders remain in
`runs/<run>/tasks/`. Startup resumes an unfinished export for the latest stopped run.
An interrupted synthesis can be retried with the button. Status polling does not use new
event kinds or delay note generation. Run folders containing symbolic links are refused.

Checks use fake run folders and mocked capture/note services:

```sh
$BENCH_PY -m pytest app/tests/test_nvidia_ui.py nvidia/tests -q -p no:cacheprovider
```

No new dependencies were added. This integration uses Python's asyncio subprocess and
zipfile modules (PSF-2.0), the existing FastAPI routes, and the NVIDIA modules documented
in `nvidia/README.md`. The optional browser check also covers task links and the synthesis
table; it requires a working Chromium installation and permission to launch it.

## Trainer view

Open `/trainer` to review saved runs. Before Start, enter an optional student name on
the bench page. It is trimmed, limited to 120 characters, and saved as `detail.student`
on the run start event. The note header uses this name before a spoken name. Older notes
retain their original header until rebuilt with `python -m note build runs/<run>`.

`GET /trainer/runs?protocol=<file>&student=<name>` reads run folders on each request.
Protocol matches exactly; student matches a case-insensitive substring. No database,
recording scan, write, hosted service or new dependency is involved. Saved `note.json`
is preferred; otherwise the existing note rules summarize events and the start event's
protocol snapshot in memory. No protocol path from an event is opened. Missing snapshots
mean step counts are unavailable; malformed data is reported on the row. Symbolic links
for run folders and input files are ignored. Folders without an event log or note are
not considered runs (for example, launcher logs).

The table shows date, duration, final done/skipped/unsure/unfinished counts, reused-tip
problems and angle readings over the limit. Repeated warning messages do not inflate
counts. A draw hint does not prove tip reuse. Active runs have no final duration unless
a saved note supplies one. Refresh after a run or note changes. Note links use the existing
note proxy and need the note service running.

Select one protocol for most missed steps. The student filter also applies to this summary.
Missed means skipped, unsure, not finished or not reached; the breakdown remains visible.
Each denominator counts runs containing that exact step index and text, so edited
instructions are separate. Active runs can contribute unfinished steps. These are recorded
outcomes for review, not a score of real bench accuracy. Phones scroll the results table
horizontally inside its panel.

```sh
$BENCH_PY -m pytest app/tests/test_trainer.py -q -p no:cacheprovider
$BENCH_PY -m app.tests.trainer_browser_check
node --check app/static/trainer.js
```

The browser check uses three temporary fake folders, no server, no camera and no audio.
It needs permission to launch Chromium. See `TRAINER-REPORT.md` for this run's results.

## Camera calibration page

Open **Calibrate** in the bench top bar, or `/calibrate`. Capture must already serve
camera slots 0 and 1; video sources work as well as live cameras. No terminal is
needed to collect pairs, compute a fit, or save the calibration. No keys or sound
are involved. Use the supplied 25 mm board with 9 × 6 inner corners. The page links
the printable board and requires confirmation that the first pose is level, since
that pose defines gravity. The software cannot check whether the surface is level.

Each preview draws detected corners on the exact sampled JPEG. All tabs share one
collection and a lock; detection runs only while the page polls, at most twice a
second per camera. The browser pauses polling in hidden tabs. A capture request
must name the displayed pair, contain two detected boards, and have advancing
capture timestamps no older than one second and within 0.1 seconds of each other.
Both image resolutions must match and stay fixed. Hold the board still; arrival
timestamps cannot establish exposure synchronization.

The page saves lossless copies of those JPEG frames and their detected corners to
`runs/calibration-pairs/<session>/<pair>/` (under the configured runs directory).
The first pair remains the gravity reference. Collect at least 12 distinct poses;
20–25 with varied angle, depth and position is better. Collections are capped at
40 pairs. **Start a new set** keeps previous images on disk and starts a fresh
level reference. A browser reload preserves the hub's collection; a hub restart
starts a new collection. Old sets remain on disk for review, without a resume UI.

**Compute** runs the existing `angle.calibrate.fit` on saved corner observations
in a child process, using the same board, distinct-pose, normal-spread, baseline
and error checks as the CLI. No second round of corner detection is needed.
Preview pauses during fitting. The child has a 170-second limit and is terminated
when the hub closes. The page shows good/redo, camera and stereo RMS in pixels,
and worst-image RMS for good fits. Failed-error fits report their RMS in the reason;
fits that fail before an error can be measured say it is unavailable.

**Use this calibration** atomically writes `angle/calibration.json` only after a
good fit. Set `BENCH_CALIBRATION` on the hub to use a different destination and pass
that same path to angle's `--calibration`. A failed fit or a new collection preserves
the previous installed file. Angle currently reads the file only at startup: restart
angle with `--calibration angle/calibration.json` after saving. This page does not
restart angle or change the event contract. Complete the physical pencil check in
`angle/CALIBRATE.md`; good reprojection error alone does not validate physical tilt.

The app adds `/calibrate/state` and POST routes `/calibrate/capture`,
`/calibrate/compute`, `/calibrate/use`, `/calibrate/reset`. The capture body is
`{revision, level}`; revision comes from state and level confirms the first pose.
These routes use the hub's existing local, single-worker trust model.

OpenCV (https://github.com/opencv/opencv, Apache-2.0; Python wheel packaging
https://github.com/opencv/opencv-python, MIT) supplies corner detection, overlays,
image encoding and camera fitting. NumPy (https://github.com/numpy/numpy,
BSD-3-Clause) holds and saves corner arrays. Both already exist in the shared
environment; no package was installed. They are now listed in app requirements.

```sh
$BENCH_PY -m pytest app/tests/test_calibration_ui.py -q -s -p no:cacheprovider
$BENCH_PY -m app.tests.calibration_browser_check
node --check app/static/calibrate.js
```

Tests serve generated checkerboard JPEGs through a fake capture HTTP transport and
exercise the real detector, saved pairs, background fitter and angle file reader.
The optional browser check intercepts requests, starts no server and tests three
viewport sizes and the capture/compute/use/reset controls. `CHROMIUM_PATH` can
select an existing browser executable. Physical cameras and browser rendering still
need verification on a machine that permits Chromium to launch.
