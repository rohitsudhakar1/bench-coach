# Note memory report

No event-contract changes proposed. Changes are confined to `note/` and remain
uncommitted. No sound, model downloads, package installs or hardware capture.

## What was retained

The service had no per-run HTML dictionary or persistent decoded-frame list.
HTML is rendered into local strings and saved to disk. The lock table already
used weak values, and the unused built-run list was already removed. Key-frame
capture already released in `finally`. Metadata capture in `video_info` did not
release if reading a property raised; it now does, with a regression test.

The capture builder's prior report measured small live Python allocations and
larger RSS growth. The fresh-process comparison here reproduces native decoder
retention with real sample clips, and larger growth when PDF export is included.
It does not identify a particular allocation inside OpenCV, WeasyPrint or Pango.
WeasyPrint documents and its font/image state were local, not a service cache;
local scope alone did not keep process RSS flat. No allocator purge or forced
collection was added to production code.

## Change

A build runs in a fresh Python process, including loading metadata, decoding
frames, optional wording and saving outputs. PDF rendering uses a separate fresh
worker. The parent receives only JSON or PDF bytes after worker exit. All native
allocations therefore end with that job, including allocations that native
libraries keep after Python objects are dropped. Same-run parent locks still
serialize writes. A process-wide semaphore allows one worker at a time, including
across different runs and PDF requests. Subprocess execution has a 180-second
timeout; timeout kills and reaps the worker, releases the slot and reports failure.
The worker uses the current Python executable and preserves relative-path meaning.

## Controlled measurements

Ten distinct fake folders, each with two videos, scripted step events and six
extracted JPEGs. Each build also rendered a real PDF. Fixture encoding ran in a
separate process before measuring. The synthetic videos are 60 seconds at
320 by 240; real inputs are the two short `414-445` sample clips supplied through
`BENCH_SAMPLES`. Baseline modules came from the unchanged branch HEAD and ran in
fresh processes, as did the changed code. No explicit garbage collection is used
by this probe. Numbers are current RSS in MiB, not process lifetime high water.

| Workload | Before first / tenth | After first / tenth | Before / after spread, runs 3–10 |
| --- | ---: | ---: | ---: |
| Synthetic build + PDF | 137.02 / 178.48 | 38.80 / 39.22 | 31.96 / 0.41 |
| Real sample build + PDF | 259.45 / 308.83 | 40.80 / 43.61 | 35.72 / 2.06 |
| Capture note probe, synthetic | 93.25 / 95.00 | 39.05 / 39.08 | 0.30 / 0.02 |
| Capture note probe, real samples | 158.97 / 179.72 | 40.86 / 42.73 | 16.92 / 0.98 |

The unchanged `capture/note_leak_probe.py` applies: it reads fixture timing and
replaces events with one warning, yielding two frames per build, no PDF, and
collects garbage before sampling. Its Python allocation reading was about
8.19–8.20 MiB before and 0.02 MiB after; locks were zero throughout. These readings
exclude Python inside an active worker, which has exited before each reading.

The build/PDF probe also samples parent plus owned worker RSS every 10 ms. After
the change, sampled peaks were 127.11–129.48 MiB for synthetic inputs and
193.31–210.42 MiB for real clips. Every worker had exited before post-build RSS
was sampled. This accounts for active child memory rather than merely hiding it.
Sampling can miss shorter peaks. Real build plus PDF time after warmup increased
from about 0.30 to 0.68 seconds; synthetic time increased from 0.08 to 0.44 seconds.

## Checks and reproduction

```sh
"$BENCH_PY" -m note.memory_probe note/.leak/probe --prepare
"$BENCH_PY" -m note.memory_probe note/.leak/probe --pdf
"$BENCH_PY" -m capture.note_leak_probe note/.leak/probe 1
"$BENCH_PY" -m pytest note/tests -q -p no:cacheprovider --basetemp=note/.leak/tests
```

Add `--samples` to preparation for real clips. Prepare again after the capture
probe, which overwrites the events. The memory test runs both variants, skipping
the real one when the environment variable or clips are absent. It checks ten
builds and PDFs, six frames each, zero retained locks, no surviving workers, less
than 12 MiB post-build RSS spread after two warmups and less than 48 MiB combined
sampled peak spread. Both baseline build/PDF comparisons fail the 12 MiB bound.
Separate checks cover timeout kill/reap, slot recovery after failure, and release
of a metadata capture when property access raises.

Final suite: 45 passed in 28.75 seconds with sample clips enabled. The real-clip
memory check separately skipped with BENCH_SAMPLES unset. `git diff --check` and
the tracked-file public-path audit passed; new files were checked separately.
All test server processes and owned workers exited.

Raw measurements are ignored under `note/.leak/`: `before.jsonl`, `after.jsonl`,
`real-before.jsonl`, `real-after.jsonl`, and the corresponding `capture` files.
Fixtures and baseline copies are ignored there too and must not be published.

## Limits

This is a ten-run regression, not a new full-pipeline or multi-hour soak. Worker
exit bounds native lifetime but does not reduce peak memory of one arbitrarily
large run. Native work is serialized, so simultaneous requests can wait behind
one another. The timeout covers execution, not time waiting for a slot. PDF and
ZIP response bytes remain buffered for the active request. Worker failure can
leave partial note files, as an interrupted build could before. The optional
hosted wording API was not exercised. Existing output/content tests still apply.

## History audit

`git log --format='%h %an %aI %cI' main..HEAD`

Output: empty (zero lines). No commits or identity/date/history overrides.
