# Ten-minute soak report

No event-contract changes proposed. All files are uncommitted.

One ten-minute run completed on October 31, 2026, from approximately 08:02 to
08:12 UTC. Five recordings and five lab notes were produced. No service restarted,
no recorded frames were dropped, and all owned processes stopped. Note memory grew
and needs follow-up; this is not a clean memory-stability result.

## What ran

```sh
"$BENCH_PY" scripts/soak.py --base-port 8840 --output runs/soak-measured
```

`BENCH_SAMPLES` supplied the two `lsv_mv/CP-{Left,Right}-Front-414-445.mp4`
clips. The script ran the full `scripts/demo.sh` pipeline: looping capture,
Grounding DINO tiny with `--new-draw`, coach hint mode, angle and note. The mic was
silent, playback stayed off, hosted API keys were removed from the child environment,
and cached weights were used. No model or package was downloaded.

The available runtime reported `torch.backends.mps.is_available() == False`
although MPS was built into PyTorch. With no `VISION_DEVICE` override, the detector
therefore selected CPU. This is a real-vision CPU soak, not a GPU soak or a repeat
of the earlier GPU speed benchmark. The sandbox did not expose the available GPU.

The baseline checkout was `69b7793`. Exactly one full soak was run. Its service
processes loaded the code before the note retention fix below. That fix and the
final harness error-handling refinements were tested separately, not with a second
soak. Lightweight regression tests overlapped portions of the measurement, so this
is shared-runtime throughput, not an isolated performance benchmark.

## Measurements

There were 120 CSV samples from 0.041 to 595.082 seconds, with intervals of
4.576–5.164 seconds. The final Stop returned at 600.464 seconds. End memory means
the last five-second sample, before the final note build and shutdown. Startup,
the last note build and shutdown peaks are outside the sampled window.

| Part | Start RSS MiB | Peak RSS MiB | End RSS MiB | Second-half slope MiB/min | Mean CPU % | Peak CPU % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Hub | 70.4 | 70.5 | 64.7 | +0.3 | 0.31 | 0.75 |
| Capture | 266.2 | 1008.3 | 996.8 | +2.2 | 131.22 | 195.77 |
| Vision | 1970.2 | 2190.0 | 1980.2 | +4.7 | 389.52 | 456.34 |
| Angle | 105.0 | 119.4 | 85.3 | -4.3 | 4.86 | 6.10 |
| Coach | 62.2 | 62.2 | 33.0 | -0.8 | 0.16 | 0.24 |
| Note | 61.5 | 217.1 | 211.8 | +32.1 | 1.15 | 40.16 |

CPU means are arithmetic means of interval readings; 100% is one CPU core.
RSS covers each service's main process, not descendants or a GPU allocation.
The first CPU/rate readings are intentionally blank. There were no missing process
measurements, camera/vision probe errors or websocket reconnects.

Memory does grow in this run. Note was about 76.8 MiB at five minutes,
100.4 at six, 107.1 at eight and 211.7 at nine. It met the summary's steady-growth
heuristic: three increasing median blocks in the second half and a slope above
1 MiB/min. The rise includes a large step after the fourth note, so the fitted
32.1 MiB/min must not be extrapolated as a proven constant leak rate.

Capture grew sharply when recording started, then fluctuated near 1 GiB with dips
at recording boundaries. Its second-half slope was slightly positive but did not
meet that steady-growth heuristic. Vision ended close to its starting RSS and
below its early peak, despite a positive late slope. Hub, angle and coach ended
lower than they began. None of these observations proves an indefinite plateau.
Free memory remained at least 54%, above the 40% stop threshold.

| Counter | Whole-window rate / second | Lowest sampled rate | Highest sampled rate |
| --- | ---: | ---: | ---: |
| Camera 0 delivered frames | 30.000 | 27.922 | 32.104 |
| Camera 1 delivered frames | 30.000 | 27.855 | 32.104 |
| Vision detector results, camera 0 | 0.787 | 0.201 | 1.089 |
| Vision detector results, camera 1 | 0.787 | 0.201 | 1.089 |
| Hub events received | 2.264 | 0.779 | 4.495 |

Whole-window rates use counter differences divided by elapsed time. Camera FPS
uses capture sequence counters. Vision's health counters count completed results
consumed by its pipeline, not every internal model forward pass. Both views share
one asynchronous detector. CPU throughput here is below the earlier GPU target.

Two observed detector progress gaps exceeded three seconds, each affecting both
camera counters: 3.107 seconds beginning at 264.026 seconds, and 3.084 seconds
beginning at 482.902 seconds. Thus the JSON contains four per-stream gap entries.
The detector recovered after both. No camera or hub progress gap exceeded three
seconds. Half-second polling and request time mean these near-threshold durations
are estimates, not exact model latency measurements. Quiet angle/coach output
and note's between-run inactivity were not classified as stalls.

Logical disk use under the entire worktree's `runs/` rose from 9,889 to
211,728,582 bytes at the final sample (about 201.9 MiB). This includes recordings,
logs and temporary regression-test artifacts; it is not recording-only growth.
Final note/shutdown writes occurred after that last disk sample.

All five capture stop records reported `dropped: 0` and `error: null`.
Their frame totals per view were 3607/3608, 3586/3586, 3599/3599, 3592/3592 and
3582/3581. All five run start/stop pairs were acknowledged by capture. Five
`note.json` files exist. Hint questions were unanswered; the notes do not demonstrate
successful protocol completion or recognition accuracy. Angle was enabled with its
normal held-box gate; physical calibration and tilt accuracy were not tested.

## Small retention fix

Review found two clear, small lifetime problems in `note/service.py`:

- `_locks` retained a path string and lock for every distinct run forever. It now
  uses weak values, with a guard around lookup/creation. A builder or waiting
  builder holds a strong reference, so concurrent builds of one run still serialize.
- `app.state.built` accumulated run names on every build but was never read.
  That unused list and its append were removed.

Tests exercise 1,000 distinct lock lifetimes and concurrent builders waiting on the
same run; service route and run-stop note tests still pass. These small collections
cannot plausibly explain the measured 150 MiB increase over only five runs.
The larger note RSS rise remains unexplained: native decoder/allocator retention
is a possibility, not a diagnosis. Frame extraction already releases its video
capture in a `finally` block. No speculative allocator workaround was added.
The fix has not had a post-change ten-minute soak, per the one-run brief.

## Harness and validation

`scripts/soak.py` records CSV resource readings, stream rates and disk use; JSON
and text summaries contain memory trends, run transitions, stalls and restarts.
`scripts/run_demo.py --process-log FILE` records owned PIDs without exposing
commands or environment variables. This works where process enumeration is denied.
The final harness also rejects missing parts or capture failures and reports an
expired final-note deadline. Tests simulate the full 600-second schedule and those
failure paths without another model run.

Commands used with the prepared Python environment:

```sh
"$BENCH_PY" -m pytest scripts/tests note/tests/test_lock_lifetime.py note/tests/test_service.py -q -p no:cacheprovider --basetemp=runs/soak-regression
"$BENCH_PY" -m pytest scripts/tests/test_soak.py -q -p no:cacheprovider --basetemp=runs/soak-unit-errors
```

The broader run passed **74 tests, 4 skipped**. After the final harness refinements,
the focused run passed **13 tests**, including the sample-path check with
`BENCH_SAMPLES` set. The real soak exited 0. `sh -n scripts/demo.sh`, CLI help,
`git diff --check`, and the public-path audit passed.

After shutdown, direct PID checks confirmed all six recorded service PIDs were
absent. A socket scan found no listeners anywhere in 8800–8899. No servers were
left running.

Raw evidence remains in ignored runtime files:
`runs/soak-measured/samples.csv`, `summary.json`, `summary.txt`, `processes.jsonl`,
`launcher.log`, and `recordings/`. Do not add raw service logs to the public repo:
they can contain local sample paths. The public report uses only relative paths.
No dependency was installed; psutil was already available and is now declared in
`scripts/requirements.txt`, with its source and licence in the scripts README.

Required date audit:

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output: empty. No commits, author overrides, timestamp overrides or history rewrites
were made. All changes are left uncommitted for the lead.
