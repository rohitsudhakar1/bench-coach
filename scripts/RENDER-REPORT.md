# Demo video renderer: report

Proposed change to docs/EVENTS.md: none.

## What it does

`python -m scripts.render_demo --video a.mp4 [--video2 b.mp4] --protocol p.txt --out out.mp4`
makes a 1920x1080, 30 fps H.264 video from one or two camera files. It has a 3 s title card, then the
footage. Every detected object is outlined and named, hands are drawn with finger points, and
a panel on the right ticks off the protocol steps with times and how each step was known.
Warnings appear in red under the cameras as they happen, and the coach's lines are captioned
there too. The video ends with the lab note held for 12 s.

It runs the repo's own parts offline. Vision runs the same per-frame code as the live demo:
Grounding DINO tiny boxes, MediaPipe hands and the event rules. The coach's rules turn those
events into steps, warnings and lines, and the note part builds the note. Boxes glide between
detector runs and hand points are blended between samples, so the overlay does not flicker.

Honest marking: the credits line on the panel, title card and note says what drew the boxes
and that steps are ticked by rules. `--labels` adds hand-labelled events. Every step they decide
says "hand-labelled" in amber, and an amber credits line says so too. There is no scripted mode.

## Measured (this Mac, MPS, other builders running, load about 4.5)

| Clip | Footage | Vision pass | Drawing + encoding | Total |
| --- | --- | --- | --- | --- |
| lsv_dji pcr_105, one camera | 122 s | 94 s | 18 s | 112 s |
| pcr_105 + cas9_correct, two cameras | 122 s | 143 s | 18 s | 160 s |
| lsv_mv 414-445, two cameras | 31 s | 35-42 s | 6 s | 41-48 s |
| lsv_mv 12-27, two cameras | 15 s | 21 s | 4 s | 25 s |
| Pi 1280x720 HEVC .mov, one camera | 30 s | 21 s | 6 s | 27 s |

A 2-minute two-camera clip takes under 3 minutes against the 10-minute target. `--reuse`
redraws from the saved vision pass in about 6 s per 30 s of footage.

Rohit's Pi footage (1280x720 HEVC .mov) works unchanged. Outputs were checked frame by frame.

## What does not work well

- Detection quality is whatever the vision part gives. On the lsv_mv clips a phone on the
  person's chest is boxed as a "tip box". On the Pi clip, which has no pipetting, a lotion
  bottle is boxed as a "pipette tip". The vision rules fired 4 tip_on events on that clip, so step 1 shows
  "Not sure". MediaPipe sometimes calls both hands "left".
- On the real lsv_mv clips, detected events alone tick at most step 1 ("not sure"). The
  414-445 render reaches 4 of 4 steps only with the hand labels from the earlier draft
  (`scripts/samples/cp-414-445-labels.jsonl`), and every one of those steps is marked hand-labelled on screen.
  No real clip produced a warning. The warning view was checked with labels that skip a step on the Pi clip.
- No pipette angle: the angle part is not run offline, so no tilt warnings.
- The note's date is the video file's modified time minus its length. A copied file shows the copy date.
- Label pills can overlap when two objects sit close together.
- Disk was at 4.1 GB free near the end (other jobs); outputs total about 290 MB.

## Files

`scripts/render_demo.py` (passes and drawing), `scripts/render_layout.py` (layout, timeline,
smoothing; no video or fonts), `scripts/tests/test_render_layout.py` and
`scripts/tests/test_render_demo.py` (33 tests, under 1 s), the checklist in `scripts/README.md`.
Libraries already in the environment: OpenCV (Apache-2.0) for decoding and drawing, Pillow (HPND) for
text, ffmpeg (LGPL/GPL, h264_videotoolbox or libx264) for encoding. No packages added.
Whole `scripts/tests` folder: 124 passed, 4 skipped.

## Git log

