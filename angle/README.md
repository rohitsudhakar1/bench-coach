# Pipette angle

Measures pipette lean from vertical, with a warning strictly above 20 degrees. The service
uses OpenCV LSD inside the latest vision pipette box for each camera. A hand-selected
shaft crop is the fallback, followed by CSRT tracking. A lost track returns to the selected
box on the next frame. No API keys, model weights, or other Bench Coach services are needed
for file replay.

Calibrated two-view measurement intersects the planes defined by the two image lines and
their camera centres. It compares that direction to gravity from a level checkerboard.
Endpoints need not match, but both lines must be on the same straight shaft. Nearly
coincident planes are refused. Without usable calibration and two valid axes, the most
supported view gives an explicitly **rough** image-vertical angle. Projections are never
averaged into a 3D claim. A rough estimate can greatly understate or overstate physical tilt.

## Run from the repository root

```sh
PY=$BENCH_PY
$PY -m angle --source $BENCH_SAMPLES/lsv_mv/CP-Left-Front-414-445.mp4 \
  --roi 441,356,22,95 --start 2 --duration 4 --always --hub '' --no-server \
  --overlay angle/runs/left.mp4
```

This exact crop applies to this clip at two seconds. For other inputs, use `--select-roi`
to drag a tight box around the straight shaft, centred in the crop, excluding the hand,
handle and bench edges. Repeat `--source` and `--roi` for two views. `--fixed-roi` disables
tracking. `--preview` opens an overlay window; q stops it. `python -m angle.run` is an alias.

```sh
$PY -m angle --source left.mp4 --source right.mp4 --select-roi \
  --calibration angle/calibration.json --always --hub '' --no-server \
  --duration 30 --overlay angle/runs/stereo.mp4
```

There is deliberately no real-camera calibration bundled. Generate one using
[the six-step checklist](CALIBRATE.md), then pass `--calibration` explicitly. Calibration
resolution and camera order must match. The old right-view sample crop produced false
large tilts in rehearsal; it is not offered as a dependable demo preset.

For the live demo, with capture and hub already running:

```sh
$PY -m angle --source http://127.0.0.1:8701/cam/0/frame.jpg \
  --source http://127.0.0.1:8701/cam/1/frame.jpg \
  --calibration angle/calibration.json --select-roi --duration 60
```

No ROI is required when vision supplies boxes. The service defaults to port 8703 with
`GET /health`; use `--port 8833` for an isolated check or `--no-server`. Health gives status,
processed frames, last emitted event and its rough flag. The last event is historical;
`axis_missing`, `waiting_activity`, `source_unavailable` or `recording_gap` is not a fresh
measurement. EOF, duration, q and Ctrl-C close the owned readers, socket, server and writer.
No audio is produced. A numeric `--source 0` opens a webcam, but use capture for verified
live timestamps and stereo.

## Hub observations and events

`--hub` defaults to `http://127.0.0.1:8700`. The reconnecting `/ws` subscriber accepts
vision `objects`, `draw`, `dispense`, coach `step`, and app `run` events. The highest-score
valid label containing “pipette” wins per camera. Coordinates are original-image
`[x1,y1,x2,y2]` pixels, clipped to the image. Boxes expire at one second; empty updates
clear them. Future, malformed and out-of-order events cannot refresh the cache. One
`--cam` per source maps nonstandard camera IDs; defaults are 0 then 1.

A fresh vision crop takes priority over manual tracking. If it contains no shaft, the
service abstains instead of silently switching to another region. Manual trackers keep
following while idle. With neither vision nor manual crop, a full-image central-line
fallback remains available, always marked rough with `crop_sources:["local"]`. It has
no pipette identity and can pick a bench edge; choose a manual crop for useful standalone
measurement. Two views are not associated by identity, so multiple pipettes are a limit.

Measurement runs only within two seconds of a vision draw/dispense unless `--always` is
set. A vision hint can open that window, but remains `activity_hint:true`; checking a
possible technique error does not establish that a transfer happened. The latest coach
`status:current` supplies the step index. Done/skipped clears that same current step;
a done/skipped event for another step cannot steal it. Run start/stop clears cached
observations and step state. This corrects the earlier draft's tagging of every step status
as current. There is no history fetch after reconnect; a new current-step event is needed.

`angle` details include the contract's `deg`, `ok`, `limit`, plus `rough`, `method`, `views`,
`cams`, `crop_sources`, and activity metadata. Rough results include all available projected
angles and the fallback reason. Confidence is an edge-support heuristic, not a probability;
rough confidence is capped at 0.4. Missing axes never become zero-degree readings. Warnings
also retain the rough flag and say “Rough camera estimate” when applicable. Downstream
consumers should preserve that caveat when speaking or displaying an angle.

Events use current Unix epoch seconds, source `angle`, object `pipette`, null hand, and
current step or null. Default rate is 2 Hz, configurable up to 3 Hz with `--hz`; warning
cooldown is five seconds. `--limit` defaults to 20. Events post to `/events`; failed delivery
prints each undelivered JSON object to stdout with a five-second reconnect backoff. Diagnostics
use stderr. `--hub ''` disables both network paths. `--fast` skips replay pacing and sends
only to stdout, while retaining the wall-clock event throttle; it is not an exhaustive
per-frame event export. It still subscribes if a hub is configured.

## Time and replay

Capture JPEGs require `X-Frame-Time`. Reads are concurrent. Stale (>1 second), future,
and repeated frames are not measured. Stereo requires verified timestamps within
`--max-skew .1` seconds. Direct cameras/generic streams remain rough. Capture timestamps
are delivery times and do not prove simultaneous exposure; hold the shaft still for checks.

Without a ledger, paired files are sampled at the same media time and assumed synchronized
constant-rate inputs. Use `--right-offset 0.1` if the right file must be sampled 0.1 seconds
later. This assumption must be checked with a visible clap/flash. `--start` selects the
first time, `--fps` defaults to 10 for processing and overlay output, and `--duration`
bounds the run. Fast file mode bounds media duration; paced/live mode bounds elapsed time.

For capture recordings, the runner discovers `timing.jsonl` beside each file or accepts
`--timing PATH`. It reads contract frame rows (`cam`, `frame`, `t`) and selects the actual
recorded frame nearest a shared epoch-relative time, within 0.1 seconds. It refuses pairs
outside the skew threshold and skips recording gaps instead of inventing frames. Ledger
frame indices must be zero-based encoded-video indices; `sequence` may contain capture
drops. Both videos must use the same recording clock origin. `--right-offset` is disallowed
with a ledger. Original epochs are retained in `recorded_frame_times`; emitted `t` always
uses the current clock. Overlay files contain processed pairs, so gaps shorten an overlay;
they are inspection artifacts, not replacements for capture's recording/timing ledger.

## Reproduce the checks

```sh
mkdir -p angle/runs
$PY -m pytest angle/tests -q -p no:cacheprovider --basetemp angle/runs/pytest
$PY -m angle.evaluate
# Optional: regenerate the labelled frames from the supplied videos.
$PY -m angle.extract_samples
```

The twelve stills and manual endpoint labels are inherited test data from the earlier
project draft. They cover paired Left/Right views at 2, 6 and 10 seconds in clips 249–269
and 414–445. They are not fresh independent labels. Endpoints define the reference angle;
only their bounds plus padding are supplied to the detector. The earlier centre preference
was tuned on this same set, so this is a development regression check, not held-out accuracy.

Rerun: 9/12 shafts detected; three abstentions; mean absolute projected-angle error
1.3154 degrees, maximum 4.9135 degrees on detections. No physical 3D error can be measured
on these clips because real camera calibrations and known physical tilts are unavailable.
The earlier longest-edge baseline reported 7.66 degrees mean and 45.48 degrees maximum
error; that historical result was not recomputed here. `samples/results.json` contains
fresh per-frame results, and `samples/evaluation.jpg` shows reference lines in magenta,
detected lines in orange, and crops in cyan.

Seeded synthetic stereo, 1,000 directions: exact pixels accepted 794 and rejected 206,
maximum error 1.73e-12 degrees. With independent 0.5-pixel endpoint noise: 791 accepted,
209 rejected, mean error 0.8710 degrees, 95th percentile 2.6776 degrees, maximum 5.0359 degrees.
These assume correct camera parameters and correct shaft lines. The calibration test renders
16 board pairs and uses the actual corner detector and CLI: rotation error 0.3083 degrees,
translation error 1.2295 mm, gravity-normal error 0.2310 degrees. Physical camera accuracy
and the [pencil check](CALIBRATE.md#pencil-check-pending) remain pending.

## Libraries

Versions and licence names were checked in installed package metadata. No package was added,
and no weights were downloaded. All implementation is under `angle/`; supplied video files
were read only as test data. Python standard-library code is under PSF-2.0.

| Library and source | Tested version | Licence | Use |
| --- | --- | --- | --- |
| [OpenCV / contrib wheels](https://github.com/opencv/opencv-python) | 5.0.0.93 | Apache-2.0 (wheel packaging MIT) | LSD, CSRT, video, corners, calibration, overlays |
| [NumPy](https://github.com/numpy/numpy) | 2.5.4 | BSD-3-Clause; bundled terms also 0BSD, MIT, Zlib, CC0-1.0 | Geometry and numeric arrays |
| [FastAPI](https://github.com/fastapi/fastapi) | 0.162.0 | MIT | Health endpoint |
| [Uvicorn](https://github.com/Kludex/uvicorn) | 0.54.0 | BSD-3-Clause | HTTP service |
| [HTTPX](https://github.com/encode/httpx) | 0.28.1 | BSD-3-Clause | Capture JPEG reads and event delivery |
| [websockets](https://github.com/python-websockets/websockets) | 17.4 | BSD-3-Clause | Hub subscription |
| [pytest](https://github.com/pytest-dev/pytest) | 9.2.0 | MIT | Automated checks |
