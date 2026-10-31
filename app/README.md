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
