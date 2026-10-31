# Classroom report

Contract proposal: add the string envelope field `bench`, default `"1"`. This is
implemented and documented in `docs/EVENTS.md` as requested by the classroom brief.
There are no new event kinds or clock changes. Scoped producer URLs assign the field
at the hub boundary; undelivered producer stdout keeps the existing format, in logs
whose filenames identify their bench.

## What works

One hub process hosts independent bench apps. Every bench has separate state, history,
subscribers, control locks, capture requests, run IDs, health probes and restart recovery.
Original root URLs keep operating bench 1. Classroom producers receive scoped URLs, so
one coach cannot consume another bench's events. Root event batches can carry explicit
IDs; unknown benches and reserved kinds reject the batch. A late note cannot append to
another bench's run log. Shared run folders keep trainer review working across benches.

`/class` polls every second, shows student, instruction, step and last warning, and puts
benches with a warning or open question first. A red dot also has a text label. Cards link
to the full scoped page, including camera feeds, Start/Stop, protocol, health, notes and
task downloads. The phone Done control and calibration requests use the scoped URL too.
The latest warning keeps a bench flagged until Stop; open questions clear with coach events.
Connection loss retains the last cards and visibly marks them stale.

`--benches N` launches one capture, vision, angle, coach and note per bench, subject to
`--without`, and one hub. `--source` reuses a pair; `--bench-source` and `--bench-cams`
select individual pairs. Defaults assign distinct camera numbers. Duplicate device
numbers are rejected. Ports use six-slot blocks, logs include bench IDs, and restarts
remain limited to one per coach/note instance. Calibration saves are separate per bench
and load on restart. The launcher now continues shutdown escalation when signal-zero
process-group probes are denied, rather than abandoning child cleanup.

Changes are limited to app, scripts and the expressly requested contract document.
No dependency, model, footage, generated recording or credential was added.

## Checks and measured results

- `python -m pytest app/tests scripts/tests -q -p no:cacheprovider`:
  89 passed, 4 skipped in 39.50 seconds. Optional sample-dependent checks were skipped
  in this run without `BENCH_SAMPLES`; the classroom sample check was run separately.
- With `BENCH_SAMPLES` set, `python -m pytest app/tests/test_classroom.py
  scripts/tests/test_classroom_launcher.py -q -p no:cacheprovider`:
  5 passed in 11.85 seconds.
- The three-bench integration launched ten processes: one hub, three captures,
  three real coaches and three scripted vision services. Every bench used both multicam
  sample videos. All three reached eight step outcomes, each had its own student and
  warnings, each saved two nonempty camera recordings plus timing and event logs,
  and every event log contained only its own bench ID. All owned processes stopped and
  all 18 reserved port slots were free afterward. Checks used only ports 8800–8899.
- Fake-bench route tests cover three concurrent runs, independent progress and warnings,
  attention ordering, scoped websocket/history delivery, mismatched-ID rejection, Stop
  affecting only its own capture, late-note isolation and independent recovery.
- Existing JavaScript tests: 14 passed. Syntax checks passed for app, classroom, phone
  and calibration JavaScript, and the shell launcher. `git diff --check` passed.
- The optional browser check could not launch Chromium: the sandbox denied its startup
  registration. No layout or browser interaction pass is claimed. The check is saved at
  `app/tests/classroom_browser_check.py` for three viewport sizes, grid ordering, red dot,
  scoped click-through, websocket and Start routing.

An early integration attempt completed all bench assertions but hit the existing denied
process-group probe on shutdown. After the launcher fix, a second attempt collided with
another concurrently running test's free-port selection. The final integration ran alone
and passed, including shutdown and port release. No existing listener was stopped.

## Limits

The sample check uses scripted actions, not vision recognition or physical bench evidence.
Physical cameras, live speech, per-bench note generation and browser layout were not
measured in the three-bench check. Existing note routes and tests still pass. No sound,
physical microphone, hosted API or model inference was used.

Classroom mode uses silent microphones unless a shared recorded mic file is supplied.
Separate physical microphone assignment is not implemented. Use each bench's phone Done
control for confirmations. Shared ROI/table/calibration CLI options require geometry
appropriate to every bench; saved classroom calibrations are separate. Browser camera
URLs use loopback service addresses, as in the local demo; remote classroom devices need
network-address configuration beyond this local launcher. This is not an authenticated
multi-user service. Scaling beyond three benches was not measured.

No packages were installed. Existing library sources and licences are in app/README.md
and scripts/README.md. No earlier-draft source was copied.

## Public repository and history audit

The required tracked-file scan for private absolute home paths returned no hits. New
untracked implementation, tests and this report were scanned as well; no private paths,
usernames or credentials were found. Commands in public docs use `BENCH_PY` and
`BENCH_SAMPLES`. All changes are uncommitted; no history, author settings or dates changed.

Command: `git log --format='%h %an %aI %cI' main..HEAD`

Output was empty:

```text
```
