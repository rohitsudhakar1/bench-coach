# Live view report

Proposal for docs/EVENTS.md: add the live view to the ports table as `live | 8706 | the live page at /, GET /data, GET /stream.mjpeg, GET /frame.jpg`. 8706 sits next to the demo's 8700-8705 and nothing else uses it. The hub's `/live` redirects there (`LIVE_URL` overrides).

## What it does

`python -m app.live --source 0` (or a file with `--realtime`) shows the camera picture with every detected object outlined and named, both hands drawn as skeletons, and the protocol steps, warnings and coach lines beside it, updating live. How to start it for the event and the fallback are in app/README.md, "Live view".

The picture is drawn on the server on every frame with the newest outlines and hands, then sent as MJPEG, so it never waits for the box model. Grounding DINO tiny runs in its own thread on the newest frame. Outlines come from OpenCV GrabCut inside each box (on a copy about 96 pixels across), computed once per detection, not per frame. MediaPipe hands run on every frame. Steps and coach lines are read from the hub's `/state`.

## Measured on this Mac (M-series, MPS, other builders running at the same time)

| Source | Picture | Detections | Detector run | Outlines per run | Hands per frame | Draw + JPEG |
| --- | --- | --- | --- | --- | --- | --- |
| CP-Left-Front-414-445.mp4, real time | 30.0 fps (the clip's rate) | 5.0-6.0 per s | 141-164 ms | 13-50 ms | 7-13 ms | 1.5-2 ms |
| Same clip, `--no-outlines` | 30.0 fps | 4.5 per s | 194-257 ms | 0 | 12 ms | 1.5 ms |
| Rohit's pi-usb-cam.mov (1280x720) | 30.0 fps | 3.4-3.6 per s | 212-289 ms | 26-58 ms | 12-14 ms | 1.5-2 ms |
| multicam camA.mp4 | 30.0 fps | 4.2 per s | 186-204 ms | 41-44 ms | 12-13 ms | 2 ms |
| Mac camera 0 | 30.0 fps (the camera's rate) | 4.3-4.4 per s | 247-255 ms | 5 ms | 8-9 ms | 1.2 ms |

The picture always kept the source's full rate. The detection rate depends on how busy the machine was: the same clip gave 5.5 per second early and 4.5 later with outlines off, because other jobs were using the GPU. With the Mac to itself expect about 6 per second. Each box therefore lags the picture by up to about a quarter of a second.

`--post-events` was checked against a test hub for 25 s of CP-Left-Front-414-445: 41 objects, 2 hand_touch and 1 tip_on events arrived.

Screenshots (headless Chromium, looked at): kept outside the repo in the plan folder `shots-live/` (live-1440.png, live-1440-b.png, live-1280-c.png).

## Tests

`app/tests/test_live.py`, 13 tests: box and outline drawing, labels kept inside the picture and moved apart when they would cover each other (this test fails on the earlier drawing code), hand skeletons and box-only hands, GrabCut outline area on a drawn disc, the rate counter, the hub summary, the `/data`, `/frame.jpg` and `/live` routes with a fake detector, real-time file playback dropping late frames, the missing-camera fallback, the hub's `/live` redirect, and (with BENCH_SAMPLES) a sample clip holding 24+ fps with a detector slower than the real one. `pytest app vision`: 168 passed.

## What does not work, or was not checked

- Grounding DINO tiny often misses the pipette body itself, labels some things wrongly (a dark bag as "waste", a bench as "tube rack"), and its scores are low (0.25-0.4). The live view shows what the model sees; it does not fix the model.
- GrabCut outlines are rough: good on solid objects (tip box, tubes, bags), loose on thin ones (pipette tips) and on big boxes like racks and the bench, where it often gives up and the box is drawn instead. Real masks (SAM-type models) were not tried: none is downloaded and the disk had 6.7 GB free.
- Hand sides follow vision's rule for unmirrored bench cameras; on a camera facing the person the sides come out swapped unless `--mirrored` is given.
- Not tried: the capture part and the live view opening the same camera at once, and `--source http://.../cam/0/mjpeg` from capture. Run the live view on the camera directly for the event.
- Only one camera per live view process.

## Git log

```
814ded5 Rohit Sudhakar 2026-10-31T19:42:09-04:00 2026-10-31T19:42:09-04:00
e78fcdc Rohit Sudhakar 2026-10-31T19:42:05-04:00 2026-10-31T19:42:05-04:00
f66a23c Rohit Sudhakar 2026-10-31T19:40:37-04:00 2026-10-31T19:40:37-04:00
72930b3 Rohit Sudhakar 2026-10-31T19:36:44-04:00 2026-10-31T19:36:44-04:00
061b59b Rohit Sudhakar 2026-10-31T19:35:45-04:00 2026-10-31T19:35:45-04:00
(plus the commit that adds this log)
```
