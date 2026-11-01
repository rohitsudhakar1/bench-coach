# One camera: report

Proposed change to docs/EVENTS.md: none. The event kinds and ports are the same; a run with
one camera simply has no `cam: 1` events and no `cam1.mp4`.

## What works with one camera

- Launcher: `./scripts/demo.sh --source one.mp4`, `--cam N` and `--cams N`. Capture gets one
  source, vision gets `--cams 1`, angle gets one view. Two cameras work as before.
- Capture serves and records one camera (`cam0.mp4` only, timing rows for cam 0 only). Asking
  for camera 1 gives 404. Three sources are refused.
- Hub page: `/config` now reports how many cameras capture has (2 if capture is down). With one,
  the single view spans both columns, the second panel is hidden, the label reads "01 / BENCH
  VIEW", boxes stay lined up with the picture, the footer says "One view", and the angle panel
  says "One camera: rough lean only, not a measured tilt".
- Angle: kept on, as a one-view lean estimate. The angle part already accepted one source; the
  launcher now gives it one.
- Preflight: one camera passes the camera check ("one fresh view ... one-camera mode") and the
  calibration check is skipped with a pass line, since stereo calibration needs two views.
- Live view (`app/live.py`): already single-source; checked on 414-445 at 30 fps picture and
  5 detections a second.
- Demo video: `render_demo.py` with one `--video` already had a one-camera layout. Two fixes:
  long title-card lines now wrap (one ran off the right edge), and the credit line says "what
  the camera saw" for one view.

## End-to-end check

`./scripts/demo.sh --source $BENCH_SAMPLES/lsv_mv/CP-Left-Front-414-445.mp4 --base-port 8800`:
all six parts came up, a 32 s run was started and stopped through the hub, the run folder held
`cam0.mp4`, `mic.wav`, `timing.jsonl`, `events.jsonl` and a lab note. Events in that run: one
`tip_on`, two `tip_off`, three `step`, two `say`. The note (default pipetting protocol, which
does not match this clip) marked step 1 unsure and listed the rest as not reached. Everything was
stopped afterwards; no listener was left on 8800-8806 or 8810.

Screenshots are outside the repo, in the team's plan folder (`shots-onecam/`): idle, running,
note, three window widths, and the live view.

The demo video `bench-coach-demo-onecam.mp4` (47.5 s, 1920x1080) was rendered from the left
view of 414-445 with the same cards and hand labels as the two-camera video, and copied to the
team's shared videos folder. Vision pass 22.5 s, redraw 6.2 s. Frames checked by eye: title
card, two mid-clip frames, note card, end card.

## What does not work, or was not checked

- No angle reading appeared during the 32 s run. Angle waits for vision's held-pipette box,
  and none came through in that window. A one-view lean is an image projection, not a tilt.
- Preflight with one camera was tested with a mock capture, not against a live one.
- A physical USB camera with `--cam 0` was not tried (no camera on this machine during the check).
- On narrow windows (under 1000 px) the one-camera layout gives the camera row more height,
  but the page was only checked at 900, 1280 and 1600 px wide.
- The note still describes `cam0.mp4` as "camera 1" in its links, which is correct but there is
  no wording that says the run had one camera.

## Tests added

- `scripts/tests/test_launcher.py`: one-camera plans for `--source`, `--cam` and `--cams`, and
  bad one-camera arguments.
- `capture/tests/test_capture.py`: one camera serves, records and refuses camera 1.
- `scripts/tests/test_preflight.py`: one camera passes, mismatched `--camera-sources` fails,
  full preflight with one camera has no FIX lines.
- `app/tests/test_hub.py`: `/config` camera count (2, 1, and 2 when capture is down); the page
  has the one-camera layout.
- `scripts/tests/test_render_demo.py`: one-camera wording and the wrapped title card.

Full suite: 708 passed, 2 skipped (6 min 38 s).

## Git log

```
ab3b77c Rohit Sudhakar 2026-10-31T20:24:43-04:00 2026-10-31T20:24:43-04:00
4809105 Rohit Sudhakar 2026-10-31T20:23:48-04:00 2026-10-31T20:23:48-04:00
192bda2 Rohit Sudhakar 2026-10-31T20:23:48-04:00 2026-10-31T20:23:48-04:00
847544a Rohit Sudhakar 2026-10-31T20:23:48-04:00 2026-10-31T20:23:48-04:00
8eac8e9 Rohit Sudhakar 2026-10-31T20:23:48-04:00 2026-10-31T20:23:48-04:00
```
