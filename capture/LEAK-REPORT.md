# Capture memory investigation

No event-contract changes proposed. Changes are uncommitted and confined to
`capture/`. No sound played, hardware opened, packages installed or models downloaded.

## Diagnosis

The original recorder was measured in a fresh process through ten three-second
recordings of the two `414-445` sample videos, with looping cameras and a silent
microphone. A tracemalloc snapshot was taken before recording and after the tenth
stop; current RSS was sampled during recording and after Stop plus garbage collection.

| Measurement | Original first / tenth | Fixed first / tenth |
| --- | ---: | ---: |
| Capture RSS during recording, MiB | 906.94 / 1030.47 | 233.45 / 239.08 |
| Capture RSS after Stop, MiB | 462.98 / 519.61 | 236.08 / 241.61 |
| Live Python allocations after Stop, MiB | 10.85 / 10.87 | 10.85 / 10.87 |
| Capture plus encoders during recording, MiB | same as capture | 321.19 / 326.28 |

The growing RSS is outside tracemalloc. The largest Python increase was 63,262
bytes of fake-microphone chunks as its fixed 100-chunk deque filled. Writer queues,
frames and timing rows did not accumulate between successful recordings. Replacing
only the encoder path plus bounding queue bytes removed the large native increase.
This isolates the principal problem to the in-process OpenCV H.264 encoding path;
it does not identify a particular allocation inside OpenCV, FFmpeg or its codec.
The original encoder already called `release()`. Garbage collection did not
recover the native RSS. No allocator purge workaround was added.

## Change

H.264 now runs in two owned FFmpeg processes, one per camera, with two encoder
threads each, the veryfast preset and zerolatency tuning. Zero lookahead reduces
buffering; process exit releases native allocations at every Stop. The writer still
runs off the camera threads. Stop drains accepted frames, closes encoder input,
waits for both processes and reports encoder failures. A finalization timeout kills
and reaps its encoder. The start-failure path attempts every resource cleanup even
if an earlier encoder's cleanup fails.

When ffmpeg is absent, recording uses OpenCV mp4v. This fallback avoids the native
H.264 path but is not supported by all browsers. An installed FFmpeg must include
libx264; an unavailable encoder is reported as a recording error. Codec names and
all timing fields remain unchanged. FFmpeg is already a documented project
prerequisite; no new executable was installed.

The queue now also limits image/JPEG/PCM payloads to 32 MiB, in addition to its
256-item limit. One frame being written and codec buffers are outside that limit.
Large frames could previously consume hundreds of MiB in the count-limited queue.
Failed recording queues are explicitly emptied so queued frames are released even
when another caller still holds the stopped state. Drop counts remain visible.

## Regression tests

`capture/tests/test_memory.py` starts an isolated process, records the sample pair
ten times for three seconds each, measures capture and both owned encoder PIDs,
and checks that no encoder survives Stop. It skips when BENCH_SAMPLES is unset or
either sample is absent. It checks every MP4's reported frame count against timing
and decodes a frame; each recording must have audio, zero drops and no error.
After two warmup runs the allowed RSS spread is 24 MiB for capture and 48 MiB
including encoders. First-to-last stopped capture growth must be below 32 MiB;
Python allocation spread must be below 8 MiB. The original stopped growth of
56.63 MiB fails that bound. The measured fixed run's late capture spread was
4.09 MiB and its total active spread was 2.77 MiB.
The final memory test, including media decode checks, passed in 31.76 seconds.

The sample probe tracks owned PIDs directly because process enumeration is not
available in the test environment. Encoder memory is included rather than hidden
by the move to subprocesses. No MJPEG or microphone client is opened by this
memory probe; the transport tests cover those paths separately.

Commands from the repository root:

```sh
"$BENCH_PY" -m pytest capture/tests note/tests/test_lock_lifetime.py note/tests/test_service.py -q -p no:cacheprovider
"$BENCH_PY" -m capture.leak_probe --output runs/capture-memory
"$BENCH_PY" -m capture.note_leak_probe runs/capture-memory 4
VISION_DEVICE=cpu "$BENCH_PY" scripts/soak.py --seconds 300 --run-seconds 60 --base-port 8860 --output runs/leak-soak
```

The targeted suite passed 27 tests in 41.83 seconds. Tests include encoder exit
failure, kill/reap on finalization timeout, byte-bound overflow and immediate queue
release after a failed write. The no-samples memory test also skips as required.

## Note service

The existing weak per-run locks and removal of the unused built-run list are
already present. No additional note code change was justified by these checks.
Forty offline builds over ten distinct recorded folders extracted two problem
frames each. Python allocations stayed at 8.20–8.22 MiB and the lock table was
empty after every build. RSS rose from 164.75 MiB to 201.20 by build four and
228.77 by build forty; builds 31–38 stayed near 223.34–223.36 MiB before one
further small rise. This supports native decoder/allocator warmup rather than a
retained Python run collection, but does not prove a permanent plateau or locate
a native allocation. A further fresh-process check completed 100 builds: RSS
went from 168.17 to 242.95 MiB, with builds 51–100 spanning 234.22–242.95 MiB;
Python allocations went from 8.20 to 8.24 MiB and locks stayed at zero. This is
small residual native growth, not evidence that all note memory growth is fixed.
Video decoders are released in `finally`. No forced garbage
collection or native allocator workaround was added to the service.

## Five-minute soak

The unchanged `scripts/soak.py` completed 300.325 measured seconds with real CPU
vision, looping sample cameras, silent microphone, hint coach, angle, hub and note.
Five recordings started/stopped at roughly one-minute intervals and five notes
were built. Sixty samples were collected at five-second intervals. Hosted keys
were removed by the harness and cached model weights were used.

| Part | Start RSS MiB | Peak | End | Second-half slope MiB/min |
| --- | ---: | ---: | ---: | ---: |
| Capture | 241.547 | 282.469 | 281.375 | +0.459 |
| Note | 61.438 | 221.062 | 218.250 | +3.888 |

Capture's steady-growth heuristic was false, versus +8.7 MiB/min and true in the
prior GPU soak. These runs differ in duration and vision device; the separate
before/after ten-recording probe is the controlled comparison. The soak samples
service RSS only, excluding encoder children; the regression above includes them.
Initial capture warmup still adds about 40 MiB. Near-zero late growth over five
minutes is not a proof about multi-hour sessions.

All five stops reported zero drops and no errors. Frame totals by camera were
1807/1807, 1794/1793, 1794/1794, 1809/1809 and 1794/1794. There were no service
restarts or progress gaps above three seconds. Free memory stayed at least 62%.
The harness exited successfully, reported ports released, and direct checks of all
six recorded service PIDs found none alive. Stop waited for each encoder to exit.

Note still met the soak's growth heuristic, so its native-memory behavior is not
claimed fixed. The longer offline build check above narrows the investigation.
CPU vision also grew (+14.224 MiB/min late); it was not changed by this task.

## Evidence and limits

Raw measurements and logs remain ignored under `runs/`: `leak-before.txt`,
`leak-after.txt`, `leak-memory-test.txt`, `leak-final-memory.txt`, `leak-note-40.txt`,
`leak-final-tests.txt`, `leak-note-100.txt`, and `leak-soak/`. Logs can contain sample paths and must not
be added to the public repository. This is a shared-runtime test, not a hardware
camera, browser playback, multi-hour endurance or encoder-quality benchmark.


`git diff --check` passed. The required tracked-file public-path audit returned no
hits, and the new untracked source/test/report files were checked separately.
All changes remain uncommitted for the lead.

Required history audit:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty (zero lines). No identity, date or history overrides were used.
