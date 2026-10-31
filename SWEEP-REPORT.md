# Mode sweep, October 31

After about ten features merged on October 30, every launcher mode was run end to end through
`scripts/demo.sh` on check ports 8820-8837, with a temporary runs folder and no API keys.
Each mode started and stopped a run from the page's Start/Stop button, then opened the Lab note
tab and its downloads (PDF, run ZIP, Benchling HTML, eLabFTW `.eln`, steps CSV), the NVIDIA
training tasks (one task ZIP and **Make synthetic runs**), `/trainer`, `/class`, `/step` and
`/calibrate`. Playwright screenshotted each page and the screenshots were looked at. Nothing was
played out loud.

## Results

| Mode | Demo ready | Steps after the run | Note and 5 downloads | NVIDIA tasks | Pages |
| --- | --- | --- | --- | --- | --- |
| Scripted vision, `--fake-speed 3`, 22 s run | 3.1 s | 5 done, 3 skipped (as scripted) | all 200 | 5 tasks, solver 0.8 | all load, no JS errors |
| Real vision, 30 s run | 7.1 s | step 1 unsure, on step 2 | all 200 | 2 tasks, solver 0.5 | all load, no JS errors |
| Hints, 30 s run | 6.6 s | on step 1, no questions | all 200 | 3 tasks | all load, no JS errors |
| Table, generated overhead video, 30 s run | 2.5 s | 5 done, 3 skipped (as scripted) | all 200 | 5 tasks, solver 0.8 | all load, no JS errors |
| Three benches, scripted vision, runs on all three | 4.1 s | each bench 5 done, 3 skipped, own run id | note excluded | 5 tasks | `/class` shows all three recording |
| Replay of `app/samples/demo.jsonl` | 0.5 s | steps 1 and 2 done | note excluded | 2 tasks, solver 1.0 | all load; camera images refused (no capture) |

In every mode with note, the note reached the page within 1.5 s of Stop. On the scripted run
the PDF was 286 KB, the run ZIP 861 KB, the `.eln` 305 KB, Benchling HTML 4.5 KB and the CSV
1 KB; each began with the right file signature. Synthetic runs finished with the seeded NumPy
fallback (NeMo Data Designer is not in this environment), and the page says so. Every launcher
exited 0 on Ctrl-C and released all its ports.

The 12-27 sample clips show glove disinfection, not pipetting, so real vision and hints say
nothing about detection accuracy here.

## Fixed, each with a test

1. The coach printed an asyncio traceback ("Event loop is closed", "Task was destroyed but it
   is pending") on every Ctrl-C, in every mode. It cancelled its health server before the server
   finished shutting down. It now lets the server stop first. Test:
   `coach/tests/test_shutdown.py`.
2. With `--without note` (the documented three-bench command and the replay), the Lab note tab
   said "Waiting for the note service to finish this run…" forever. It now says the note service
   is not running and that training tasks still work. Test: `app/tests/eln-downloads.test.cjs`
   (`node --test app/tests/*.test.cjs`).
3. The README said the bundled replay shows "a question and an angle warning". It does not: the
   hub shows warnings, the gauge and questions only during a run, and a replay cannot start one.
   The README now says what the replay shows. No code change; the smoke test checks the two
   ticked steps.

## Added

`tests/test_modes_smoke.py` runs all six modes the same way (shorter runs) and checks the run,
note, downloads, NVIDIA export and task ZIP, every page, JavaScript errors when Playwright's
Chromium is present, tracebacks in every part log, and port release. All six passed in 111 s.
Without `BENCH_SAMPLES`, replay and table still run and the other four skip (28 s). Real vision
and hints also skip when the Grounding DINO or hand model is not cached. Put back on the old
coach, the test fails on the coach log traceback.

`REHEARSAL.md` now lists the smoke check and the three-bench command. No existing command
changed. App, launcher and rehearsal tests: 104 passed, 1 skipped. Coach tests: 121 passed,
1 skipped. Node page tests: 16 passed.

## Seen but not changed (for the part owners)

- Hints on the 12-27 clips: vision sent 5 draw and 5 dispense hints in the first 30 s, but the
  current step wanted a tip, so the coach asked nothing. That is the designed rule. Angle still
  measured inside vision boxes marked held and the coach warned "Pipette may be tilted about
  55 degrees" and "about 89 degrees" on clips with no pipette in hand. Vision owner (same as
  rehearsal item 10).
- After Stop, vision's boxes keep being drawn on the camera feeds, because the hub keeps
  applying `objects` events after a run. Harmless; rehearsal item 13.
- During a real-vision run the angle and coach dots turn amber ("quiet") until they send
  something. That is how the part check is written, but it can look like a fault on stage.
- Clicking Start during a replay clears the replayed steps from the page. Expected, not documented.
- The NVIDIA task list scrolls inside a 220 px box, so the **Make synthetic runs** button sits
  right under a cut-off question. Readable, slightly cramped.

## Git log check

Run before this file was added: `git log --format='%h %an %aI %cI' main..HEAD`

```
bb1e915 Andy Bei 2026-10-31T03:18:12-04:00 2026-10-31T03:18:12-04:00
e583aef Andy Bei 2026-10-31T03:16:42-04:00 2026-10-31T03:16:42-04:00
e221403 Andy Bei 2026-10-31T03:11:23-04:00 2026-10-31T03:11:23-04:00
706c74b Andy Bei 2026-10-31T03:10:08-04:00 2026-10-31T03:10:08-04:00
95df2a3 Andy Bei 2026-10-31T03:04:31-04:00 2026-10-31T03:04:31-04:00
```
