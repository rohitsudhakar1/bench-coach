Andy: do not order a batch on the strength of these clips; dependable label reading is not demonstrated.
Apple Vision compiles but fails at runtime here: all 36 sampled attempts were unavailable, so its accuracy and confidence are unmeasured.
A separate Tesseract check reads “shea better” on the lit USB bottle after straightening; 2× enlargement also recovers “vanilla cashmere”.
USB heading letters are about 12–14 pixels high and fine print 4–6; the ribbon heading is about 18–24 pixels high but blurred and unread.
First test one camera with front lighting, correct focus and upright labels; aim for 20-pixel letters, roughly 1440p for this USB heading or closer framing.

# Label OCR camera check

No event-contract changes are proposed. `vision.labels` is a standalone analysis tool;
it does not tell the coach which reagent or tube is present. All changes remain uncommitted.

## What runs

`vision/labels.py` samples a constant-frame-rate video, optionally crops and enlarges it,
and calls `vision/labels.swift`. The helper uses `VNRecognizeTextRequest` in accurate mode,
English, without language correction. No model or package was downloaded. Swift compiles
successfully. The Python parser converts Vision's normalized lower-left boxes to original
pixel coordinates with a top-left origin. Temporary images and binaries are removed.

The required output fields are `t`, `text`, `confidence`, `box`. `t` defaults to media seconds,
with an explicit `--time-origin` for epoch alignment. Boxes are `[x,y,width,height]`.
No-text results and unavailable engines are different: `status: "no_text"` has empty text;
`status: "unavailable"` has null text/confidence/box and an explanation. Unavailable exits 2.
The non-Mac path uses only the standard library and never tries Swift or video decoding.

## Apple Vision measurement is blocked

Both sources were decoded successfully:

| Clip | Dimensions | FPS | Frames | Duration |
| --- | --- | --- | --- | --- |
| `pi-usb-cam.mov` | 1280 × 720 | 30 | 900 | 30.000 s |
| `pi-ribbon-cam.mov` | 1280 × 720 | 30 | 770 | 25.667 s |

For each camera, tested 0, 6, 12, 15, 18 and 21 seconds in three forms: full frame,
bottle crop at native size, and the same crop enlarged 2× with bicubic interpolation.
USB crop: `(400,150,700,570)`; ribbon crop: `(0,150,1000,570)`.
There were **36 attempts; all 36 returned unavailable**, not empty recognized text.
Their helper calls took 2.581 s total, excluding compilation and decoding. This is failure
latency and must not be quoted as OCR throughput.

The actual native error was `Foundation._GenericObjCError.nilError` (localized as
`Foundation._GenericObjCError error 0`). Explicit CPU selection, older request revisions,
direct CGImage input and a software Core Image context also failed. Image decoding itself
worked. A CVPixelBuffer probe additionally reported
`kIOSurfaceMethodSetCoreVideoBridgedKeys failed: 10000003`.
This is consistent with a restricted runtime problem, but the underlying cause is not proven.
No permission or sandbox change was attempted. The shipped helper keeps the normal Vision
compute selection rather than retaining an unsuccessful workaround.

**There are no measured Apple Vision strings, confidence scores, read/missed-text sizes,
or native-versus-2× accuracy gains from this session.** Framework failures cannot establish
that either camera's pixels are unreadable. A successful native run outside this failing
execution environment is still required before this part can be called working end to end.

## Separate, limited pixel diagnostic

To get some evidence about the footage despite that blocker, ran the already-installed
Tesseract 5.5.2, English, sparse-text mode (`--psm 11`). It is an optional evaluator backend,
not a replacement in `vision.labels`, and never runs automatically as its fallback.

The two reference phrases, checked visually on the lit USB image, are “shea better” and
“vanilla cashmere”. The six sample times and three image variants above gave 36 successful
Tesseract invocations. At the five bottle-visible times (6, 12, 15, 18, 21 s), neither reference
phrase was recovered intact in either camera, in any of the three variants: **0/5 frames
per camera per variant**. There were many fragments and wrong guesses from USB. Counting all
returned words as correct would badly overstate performance. These five frames are a small,
selected diagnostic set, not a camera accuracy benchmark or a complete transcript score.
At time 0, a phone covers the label and is excluded from the bottle denominator.

One positive control: the ribbon full frame at 0 s reads the phone timer `00:11.73`, confidence
**0.924**, box **94 px high**. This is an axis-aligned whole-word box; the glyphs are roughly
75–85 px tall. A large bright phone display is not a bottle-label test. The USB timer was only
partially read in that unrotated frame. The clips are not aligned to the same phone time.

Then tried a manually selected, lit 18 s crop, rotating it to make its text horizontal.
This is extra preprocessing, chosen after inspecting the image, not an automatic feature or
an independent test. USB crop `(570,350,420,370)`, rotation −20 degrees; ribbon crop
`(20,230,610,490)`, rotation −7 degrees. Rotated crops use a white border and unchanged canvas
size. There were four additional Tesseract calls, for 40 in total.

| Camera and processing | Exact text recovered | Word confidence | Word height in source-scale pixels |
| --- | --- | --- | --- |
| USB, straightened, 1× | `shea better` | 0.813 / 0.962 | 13 / 12 |
| USB, straightened, 2× | `shea better` | 0.963 / 0.963 | 14 / 12 |
| USB, straightened, 2× | `vanilla cashmere` | 0.859 / 0.953 | 7 / 8 |
| USB, straightened, 2× | `Shea Butter` | 0.601 / 0.891 | 7.5 / 6.5 |
| Ribbon, straightened, 1× or 2× | neither reference phrase | no correct-word score | heading approximately 18–24 px, visually estimated |

Tesseract confidence is its own word score divided by 100, not a probability and not comparable
to Vision confidence. Heights in the table are word boxes after rotation, divided by the
scale factor. They exclude most of the original tilt; they are still not exact character
x-heights. Manual inspection puts USB heading glyphs at about **12–14 px**, ordinary body text
at **6–9 px**, and fine print at **4–6 px**. Ribbon heading glyphs are approximately **18–24 px**,
body text **10–16 px**, but soft edges merge strokes. These manual ranges are approximate.

2× helps in this one straightened USB crop: reference phrases go from **1/2 to 2/2**. It does
not yield a reliable full label: the small paragraph, lists and bottom print still contain
many omissions and wrong words. For example, the visible word “luxurious” is returned as
`tumuriovs`. The unrotated paired crops and ribbon crops show no reference-phrase improvement.
Enlarging the image changes the recognizer's input scale; it does not recover new sensor detail.

The darker early images hide label contrast; illumination improves later. The ribbon image is
larger but visibly softer than USB at 18 s. That could reflect focus, motion, optics or exposure;
these recordings do not isolate the cause. No tubes, handwriting, reagent names or multiple
similar bottles were tested. Nothing here proves reliable reagent identification.

Machine-readable selected results and the failure counts are in
[`eval/labels_results.json`](eval/labels_results.json). Only selected reference words and the
phone control are kept, not full screen transcripts. Sample videos and extracted images are
not included.

## Pixel arithmetic and recommendation

Use **20 px letter height as a proposed setup target**, with 20–30 px offering more margin
than the 12–14 px heading that sometimes works here. This is a planning choice, not a measured
Apple Vision minimum or a universal OCR guarantee. The observed blurred ribbon heading already
approaches that size and fails, so pixel count alone cannot fix the setup.

For the same field of view and real optical detail:

`new letter height = current letter height × new vertical resolution / 720`

For the same camera and lens, with a front-facing label approximately perpendicular to the
viewing axis:

`new distance ≈ current distance × current letter height / target letter height`

| Current USB letter height | At 1080p (1.5×) | At 1440p (2×) | At 2160p / 4K (3×) | Scale to reach 20 px | Distance fraction to reach 20 px at 720p |
| --- | --- | --- | --- | --- | --- |
| 12 px heading | 18 px | 24 px | 36 px | 1.67×, about 1200 vertical pixels | 0.60× |
| 8 px body text | 12 px | 16 px | 24 px | 2.5×, about 1800 vertical pixels | 0.40× |
| 5 px fine print | 7.5 px | 10 px | 15 px | 4×, about 2880 vertical pixels | 0.25× |

Thus **720p at the shown framing is not demonstrated sufficient for dependable whole-label
reading**. It has enough pixels for some large text in favorable USB frames with preprocessing.
For the main heading, test 1440p at the same framing or move the 720p camera to roughly 60% of
the present distance. For 8 px body text, 4K could provide about 24 px if the lens resolves it.
Fine print around 5 px still falls short of the proposed target at 4K; closer framing or a
separate close-up view is the more direct test. Real camera distance and lens field of view
were not supplied, so there is no justified distance in centimeters. If the present distance
were 50 cm, the heading calculation would give 30 cm; that is an example, not a measured setup.

Before buying several cameras: light the label from the front, remove the bright-window
backlight, focus at the actual label distance, keep text facing the camera, and verify the
smallest required name or number occupies at least the chosen pixel target. Test one camera
on the actual bottles and tubes, including motion and curved labels. A sensor's advertised
resolution or a software upscale is not a promise of resolved letter strokes.

## Reproduce

Set `BENCH_PY` to the agreed Python and `BENCH_SAMPLES` to the folder containing the two clips.
The commands below use only those environment variables; no videos are bundled.

```sh
"$BENCH_PY" -m vision.labels --source "$BENCH_SAMPLES/pi-usb-cam.mov" --sample-rate 1
"$BENCH_PY" -m vision.labels --source "$BENCH_SAMPLES/pi-ribbon-cam.mov" --sample-rate 1

# Six times; repeat without --crop for the full-frame baseline and with --upscale 2.
"$BENCH_PY" -m vision.eval.labels_camera --source "$BENCH_SAMPLES/pi-usb-cam.mov" \
    --crop 400,150,700,570
"$BENCH_PY" -m vision.eval.labels_camera --source "$BENCH_SAMPLES/pi-ribbon-cam.mov" \
    --crop 0,150,1000,570

# Separate optional diagnostic; repeat at --upscale 1 for the comparison.
"$BENCH_PY" -m vision.eval.labels_camera --backend tesseract \
    --source "$BENCH_SAMPLES/pi-usb-cam.mov" --times 18 \
    --crop 570,350,420,370 --deskew -20 --upscale 2
"$BENCH_PY" -m vision.eval.labels_camera --backend tesseract \
    --source "$BENCH_SAMPLES/pi-ribbon-cam.mov" --times 18 \
    --crop 20,230,610,490 --deskew -7 --upscale 2
```

For the 36 native attempts here, the evaluator reused one compiled copy of the shipped helper
instead of recompiling for every variant. Each inference invocation was short. Model work
paused when the observed free-memory percentage fell below the shared 40% floor.

## Checks and unfinished work

- OCR tests: **28 passed in 0.40 s**, without Swift. They cover crop parsing, coordinate mapping,
  malformed output, confidence bounds, non-Mac fallback, missing Swift, compilation failure,
  helper failure/timeout, actual synthetic-video sampling, crop enlargement and empty frames.
- Vision tests excluding the existing model-marked test: **140 passed, 1 skipped, 1 deselected
  in 10.66 s**. No model download or live service was involved.
- Swift helper compiled. Native recognition failed; successful macOS recognition remains
  unverified. Non-Mac behavior was simulated in unit tests, not run on a physical Linux host.
- No hub integration, tube-identity tracking, handwriting validation or live-camera testing.
- No packages added, no sound, no servers, no commits or pushes, and no sample media in changes.

Date audit command: `git log --format='%h %an %aI %cI' main..HEAD`.
Output: empty. There are no branch-only commits. Git identity and dates were not changed.
