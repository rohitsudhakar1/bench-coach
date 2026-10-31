# Two-camera calibration

The first paired images must show the board lying on a level surface. Its normal defines
gravity in camera 0 coordinates. Software cannot verify that the surface is level. Keep
both cameras fixed after calibration, and use the same source order and resolution.

## Six-step table checklist

| Step | Do this | Check before continuing |
| --- | --- | --- |
| 1. Print | Print `angle/checkerboard-25mm.png` landscape at 100%, fit-to-page off, on rigid flat backing. | Image is 270 × 195 mm at 254 dpi. Pattern is 10 × 7 squares, 9 × 6 inner corners. Measure several 25 mm squares with a ruler. Set width to 270 mm if the printer ignores PNG scale. |
| 2. Fix cameras | Show the full board in both views, with roughly 45–90° between viewing directions. Lock focus, zoom and resolution. | Same camera order and resolution for calibration and measurement; enough common working volume. |
| 3. Set gravity | Lay the board flat on a level bench. Start collection and hold it until “Accepted 1 … level reference saved”. | First pair is the level reference. A missing first board or bad first timestamp is REDO; the program never substitutes a later pose. |
| 4. Vary poses | Over a minute, collect 20–25 still poses, tilting 20–40° about both axes and varying depth and image position. | At least 12 distinct accepted pairs and 15° fitted normal spread. Entire board visible, little blur. Mark one corner in the margin and retain consistent orientation in both views. |
| 5. Read verdict | Inspect GOOD or REDO and the saved pairs. | All camera/stereo and individual-image RMS must be ≤2 pixels; prefer under 1 pixel. Baseline at least one square. GOOD describes fit only. REDO preserves the previous file, which is not this run's result. |
| 6. Check pencil | Run the procedure below on the calibrated system. | Accepted readings say STEREO and `rough:false`; record actual errors and missing holds before claiming physical accuracy. Recalibrate if either camera moves. |

Regenerate the printable PNG with `python -m angle.print_board`. Its physical-size metadata
and pixel square spacing are tested; the actual paper must still be measured.

## Collect and fit

From the repo root, using capture's existing feeds (this tool starts no server):

```sh
PY=$BENCH_PY
$PY -m angle.calibrate --capture http://127.0.0.1:8701 \
  --duration 60 --interval 2 --save-pairs angle/calibration/table-01 \
  --output angle/calibration.json
```

Use a new save directory each time. The HTTP requests run concurrently; delivery timestamps
must advance, be no more than one second old, not be future dated, and be within
`--max-skew .1` seconds. Hold the board still through each sample; delivery times do not
prove exposure synchronization. Later missing boards or invalid capture pairs are skipped;
resolution changes stop the fit.

Two folders with matching, zero-padded `.png`, `.jpg` or `.jpeg` filenames:

```sh
$PY -m angle.calibrate --left angle/calibration/table-01/left \
  --right angle/calibration/table-01/right --output angle/calibration.json
```

Two already synchronized videos, with the level board at their first sampled time:

```sh
$PY -m angle.calibrate --source left-board.mp4 --source right-board.mp4 \
  --duration 60 --interval 2 --right-offset 0 --save-pairs angle/calibration/video-01
```

Calibration video sampling assumes constant-rate aligned media. Unlike the measurement
runner, the calibration video command does not consume timing ledgers. For dropped-frame
recordings, prepare timestamp-paired still folders and keep the level reference first.
Board dimensions can be changed with `--cols`, `--rows`, `--square-mm`; defaults match the
supplied printable board. The source movies used for shaft evaluation have no checkerboard
and cannot supply a real calibration.

The saved JSON contains `K1`, `D1`, `K2`, `D2`, `R`, `T`, `gravity`, `size` plus fitted RMS,
per-pair errors, board size and provenance. `R,T` map camera 0 to camera 1, translation in
millimetres. Both views require the same resolution. It uses pinhole calibration with five
distortion coefficients, not a fisheye model. Failed fits do not replace the old JSON;
successful writes are atomic. Low RMS does not guarantee good coverage or correct gravity.
Corner ordering can be reversed between views on ambiguous poses; retake those pairs if
stereo RMS is high rather than trusting a good monocular fit alone.

## Pencil check — pending

1. Fix a printed protractor vertically to a rigid stand. Use a plumb line to set zero from
   gravity. Mark 0°, 10°, 20°, 30° and 45° on each side. Fix a pencil against the marks so
   the hand is not the angle reference.
2. Start the bounded two-view command below. Select the same straight pencil section in
   both views. Hold each of 0°, 10°, 20°, 30°, 45° for three seconds, returning to zero
   between holds. Record reference hold times. Repeat with the protractor plane turned 90°
   about the plumb line. Use separate bounded runs for the two planes if needed.
3. For each hold, discard its first and last half-second. Take the median accepted stereo
   angle from the remaining interval and subtract the reference. Keep rough fallbacks
   separate. Count missing holds; do not replace missing measurements with zero.
4. Report every hold's absolute error, mean absolute error, largest error and missing-hold
   count. Retain both overlays, JSONL events, source order, calibration RMS and marked
   reference photos. A practical demo target is stereo MAE below 5° and no missing holds.
5. Check that 0° and 10° give no warning and 30° and 45° warn within a second when outside
   the five-second cooldown. Exact `>20°` behavior is tested synthetically; a physical
   hand-positioned reference cannot settle the strict boundary.
6. Stop only the processes you started, by their own PIDs. Record the actual date and
   results in `angle/REPORT.md`. If this check fails, keep the rough description and report
   observed errors. This build has not performed the physical check.

```sh
mkdir -p angle/runs
$PY -m angle --source http://127.0.0.1:8701/cam/0/frame.jpg \
  --source http://127.0.0.1:8701/cam/1/frame.jpg \
  --calibration angle/calibration.json --select-roi --always --hub '' --no-server \
  --duration 45 --preview --overlay angle/runs/pencil-plane1.mp4 \
  > angle/runs/pencil-plane1.jsonl
```

For a rough single-camera check, align the protractor plane parallel to the image plane,
level the camera, use one source and omit calibration. Record it separately as projected
angle accuracy, not 3D validation. Use only 8800–8899 for builder-owned test servers; the
8701 URL above refers to an already running demo capture service.
