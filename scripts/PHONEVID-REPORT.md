# Phone video report

Goal: Andy films the 9am run on his iPhone, AirDrops it, and the team runs
`scripts/render_demo.py --video` and `./scripts/demo.sh --source` on it with no manual step.

## What changed

- New `scripts/phonevid.py`. It checks a video with ffprobe. If the file is HEVC, bigger than
  1920x1080, over 30 fps, variable frame rate, rotated, 10-bit or HDR, it writes
  `<name>.bench.mp4` beside it: H.264, upright, constant 30 fps, at most 1920x1080 (portrait
  stays portrait), SDR BT.709, audio kept as AAC. It prints one line saying why. A plain
  1080p30 H.264 file is used as it is, and a file ffprobe cannot read is passed through
  unchanged (the old behaviour). The copy is reused while it is newer than the original.
- `render_demo.py` and `run_demo.py` (behind `demo.sh`) call it on every `--video`/`--source`
  file. Render keeps the original file name for the on-screen camera label.
- HDR (HLG, PQ, Dolby Vision 8.4 which is HLG underneath) is tone-mapped with a 3D lookup table
  the module writes itself, so it only needs ffmpeg's built-in `lut3d`. This ffmpeg has no
  `zscale` or `libplacebo`, so the usual `tonemap` chain was not available.
- Rotation is applied by ffmpeg's own autorotate while converting; the output has no tag left.
- Before this, every reader (vision, capture, render) took frame N as time N/fps, so a
  variable-rate phone file drifted, and HDR files read through OpenCV came out grey.

Why always convert instead of per-reader fixes: four separate readers (capture, vision file
source, render's analysis pass, render's draw pass) would each need rotation, VFR timing and
HDR handling. One conversion step fixes all of them at once.

## Measured

Test clips were made from `lsv_mv/CP-Left-Front-414-445.mp4` (12 s each, HEVC .mov) plus one
real iPhone file (Rohit's `device-1628.mov`: HEVC 10-bit, Dolby Vision profile 8.4 HLG,
rotation -90, variable rate, 164 s). Clips are outside the repo; nothing was committed.

| clip | convert | matches source frame for frame | render (8 s) | events |
|---|---|---|---|---|
| HEVC 1080p30 | 0.7 s | yes, mean diff 1.5/255 | ok, 1920x1080 | 3 steps, 1 tip_on |
| HEVC 1080p60 | 0.7 s | yes, 1.4 | ok | same |
| HEVC 4K30 | 1.0 s | yes, 1.2 | ok | same |
| rotation 90 | 0.7 s | yes, upright, 1.5 | ok | same |
| rotation 180 | 0.7 s | yes, upright, 1.5 | ok | same |
| variable frame rate (about 30% of frames dropped) | 0.6 s | yes, no time offset | ok | same |
| 10-bit HLG HDR | 2.0 s | 3.2 (read raw through OpenCV: 8.8 and less saturated) | ok | same |
| real iPhone DV/HLG portrait, 164 s | 8.4 s | not comparable; checked by eye, colour and writing on paper kept | ok | (no pipetting in it) |

The vision events were identical for all seven generated clips, so rotation, frame rate,
resolution and HDR no longer change what the coach sees.

Tone map: BT.2408 puts SDR white at 203 nits, but on the real iPhone clip that clipped the
paper and a sticky note to flat white. White at 300 nits with a soft roll-off from half of
white kept the handwriting, so those are the settings.

Live path: `./scripts/demo.sh --source hrot90.mov --base-port 8840 --without angle coach note`
converted the file, came up ready, capture served upright 1920x1080 frames at 30 fps and vision
reported detections and an event. Stopped with Ctrl-C; no listeners left on 8840-8845.

Tests: `scripts/tests/test_phonevid.py`, 13 tests on clips generated from ffmpeg's test pattern
(HEVC passthrough check, caching, rotation 90/-90/180 with a check that the picture is not
flipped, 60 fps, variable rate, 4K, portrait 4K, HLG round trip versus naive read, curve end
points, unreadable file, plus one on the sample footage when `BENCH_SAMPLES` is set).
Whole `scripts/tests`: 164 passed, 1 skipped. `test_launcher.py::test_second_crash_fails_and_stops_all`
failed once in an earlier full run and passed on rerun with and without this change (timing).

## Not done / limits

- Dolby Vision's dynamic metadata is ignored; the HLG base layer is tone-mapped with one fixed
  curve. Fine for a bench under room light; a very bright window would look flatter.
- PQ (HDR10) is handled by the same curve with an assumed 1000-nit peak but was only checked
  with a unit test, not a real file.
- Everything is converted to 30 fps; 60 fps detail is dropped on purpose (vision runs at 10 fps).
- Only `render_demo` and `demo.sh` convert automatically. Running `python -m vision --source
  phone.mov` directly does not; run `scripts/phonevid.py phone.mov` first.

## Libraries

- FFmpeg / ffprobe (https://ffmpeg.org, LGPL/GPL): probing, decoding HEVC, autorotate, fps,
  scale, lut3d, H.264 encode (libx264 or VideoToolbox).
- NumPy (BSD-3-Clause): building the HLG/PQ to SDR lookup table (BT.2100 HLG and PQ formulas,
  BT.2087 gamut matrix).

## git log main..HEAD

```
GITLOG
```
