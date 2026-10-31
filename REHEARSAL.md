# Dress rehearsal

Every part ran together through `scripts/demo.sh` on ports 8820-8825: capture on the two
`lsv_mv` 12-27 clips, the hub and page, angle, coach, note, and `scripts/fake_vision.py` in
vision's place. A run was started, played through and stopped. The coach followed all eight
steps, printed its warnings, angle events reached the page, and the note was built and shown on
the Lab note tab. Real vision was then run on the same clips for one minute (results below).
Nothing is spoken out loud in any of these commands; the coach prints its lines to
`runs/logs/coach.log` and the page shows them.

## Commands

Set these once in the terminal (the python that has every part's packages, and the folder
that holds `lsv_mv/`):

```sh
export BENCH_PY=/path/to/.venv/bin/python
export BENCH_SAMPLES=/path/to/labprint-work
```

Check nothing else holds the demo ports, then start from the repo root:

```sh
lsof -nP -iTCP:8700-8705 -sTCP:LISTEN
```

Wait for `Demo ready`, open http://127.0.0.1:8700 full screen. Ctrl-C stops every part. Logs go
to `runs/logs/`, each run to `runs/<run>/`. For a practice run beside the live demo add
`--base-port 8820` and open http://127.0.0.1:8820 instead.

Sample videos, scripted vision (the safe stage demo; always plays the same 50 seconds):

```sh
./scripts/demo.sh --source "$BENCH_SAMPLES/lsv_mv/CP-Left-Front-12-27.mp4" \
  "$BENCH_SAMPLES/lsv_mv/CP-Right-Front-12-27.mp4" --fake-vision
```

`--fake-speed 2` plays the script twice as fast. Scripted events carry source `vision`; the two
tilt readings carry source `fake_vision` and `scripted: true`, so the recording and note show
they were staged.

Sample videos, real vision:

```sh
./scripts/demo.sh --source "$BENCH_SAMPLES/lsv_mv/CP-Left-Front-12-27.mp4" \
  "$BENCH_SAMPLES/lsv_mv/CP-Right-Front-12-27.mp4" --timeout 150
```

Vision takes about 8 seconds to load its models here; `--timeout 150` covers a cold start.
It uses Grounding DINO tiny. On this Mac's GPU, with both cameras sharing one detector, it
measured 4.04 detector runs a second per camera on October 31 (3.69 before the batching change).
Keep tiny for the stage. `--detector florence2` draws better boxes on still frames but found
7 of 18 held-out draws against tiny's 15 of 18, and only reaches 2.15 runs a second per camera.

Live cameras (microphone on, the coach listens; still no speaker):

```sh
./scripts/demo.sh --cams 0 1 --timeout 150
```

The coach's hearing was tuned on October 31 (a slightly quicker noise gate and a list of lab
words for Whisper). On three reused recordings it went from 12 of 18 right to 18 of 18. A larger
set made with computer voices went from 81 of 99 to 90 of 107, almost all in one voice. No real
person at a real bench microphone has been scored, so say a test question before going on stage.

Angle measures only inside a vision pipette box marked as held in a hand. To measure a known spot instead, pass the
shaft's crop from a still of `http://127.0.0.1:8701/cam/0/frame.jpg`, once per camera, and the
calibration file if one was made (see `angle/CALIBRATE.md`):

```sh
./scripts/demo.sh --cams 0 1 --angle-roi X,Y,W,H --calibration angle/calibration.json
```

Hint mode (vision's guesses become yes/no questions from the coach; works with `--cams` too):

```sh
./scripts/demo.sh --source "$BENCH_SAMPLES/lsv_mv/CP-Left-Front-12-27.mp4" \
  "$BENCH_SAMPLES/lsv_mv/CP-Right-Front-12-27.mp4" --hints --timeout 150
```

With sample videos the mic is silent, so questions go unanswered and close after 10 seconds.
Add `--mic-file some.wav` to feed recorded answers.

Table mode (overhead camera, coloured caps, magenta band on the tip). First save the layout
with vision off, so the setup tool can read capture's frame:

```sh
./scripts/demo.sh --cams 0 1 --without vision angle
# second terminal, caps closed, pipette out of view:
"$BENCH_PY" -m vision.table setup --source http://127.0.0.1:8701/cam/0/frame.jpg --config vision/table.json
```

Ctrl-C the launcher, then:

```sh
./scripts/demo.sh --cams 0 1 --table --table-source capture:0 --without angle
"$BENCH_PY" scripts/preflight.py --capture http://127.0.0.1:8701 --hub http://127.0.0.1:8700 \
  --camera-sources 0 1 --no-angle
```

Table mode and hint mode cannot be combined, and `--fake-vision` replaces both. Table mode was
not run on a real table in this rehearsal (no overhead camera or caps here); `scripts/tests` cover it with
generated footage only. On October 31 it was reworked for coloured and dim light, hue shifts,
shadows, clutter, shake and a tilted camera. On 60 drawn runs over 15 such conditions it found
360 of 360 events, against 142 of 360 before, but those drawn runs were also used while making
the changes. Setup now prints a quality score and preflight prints FIX below 70. Treat the first
real table run as the real test (`vision/TABLEROBUST-REPORT.md`).

Check the whole chain in about 25 seconds before going on stage:

```sh
"$BENCH_PY" -m pytest tests/test_rehearsal.py -q -p no:cacheprovider
```

It runs the scripted demo at three times speed on a free block of ports in 8840-8899 and a
temporary runs folder, and checks steps, warnings, angle events, recordings, the note and that
every port is released. It skips itself when `BENCH_SAMPLES` is unset.

Check every launcher mode in about two minutes (scripted, real vision, hints, table on a
generated overhead video, three benches, and the bundled replay):

```sh
"$BENCH_PY" -m pytest tests/test_modes_smoke.py -q -p no:cacheprovider
```

Each mode starts through `scripts/demo.sh`, starts and stops a run, fetches the note and its
PDF, ZIP, Benchling, eLabFTW and CSV downloads, the NVIDIA tasks and a task ZIP, opens
`/trainer`, `/class`, `/step` and `/calibrate`, and checks that no part logged a traceback and
every port is released. Real vision and hints skip when the vision models are not cached; the
sample-video modes skip without `BENCH_SAMPLES`. All six passed in 111 s on October 31.
`SWEEP-REPORT.md` has the results. It shows that each mode runs and serves its pages, not that
recognition is right.

For three benches with scripted vision (bench 2 is at `/benches/2/`, the overview at `/class`):

```sh
./scripts/demo.sh --benches 3 --source "$BENCH_SAMPLES/lsv_mv/CP-Left-Front-12-27.mp4" \
  "$BENCH_SAMPLES/lsv_mv/CP-Right-Front-12-27.mp4" --fake-vision --without angle note
```

Without note the Lab note tab says the note service is not running; the training tasks still
appear. Each bench has its own page, run and recordings. Classroom mic input is silent, so use
each bench's phone Done button (`/benches/<id>/step`). Only three benches on scripted vision
have been checked.

With note running, the Lab note tab has PDF, run ZIP, Benchling HTML, eLabFTW `.eln` and steps
CSV downloads. The sweep fetched all five in every mode with note. Pasting into Benchling and
importing into eLabFTW were not tried.

## Run of show (3 minutes, scripted vision)

Times count from the Start click. The coach leaves at least 3 seconds between spoken lines, so
some lines show up to 3 seconds after the event that caused them.

| When | Presenter | Page and coach |
| --- | --- | --- |
| -0:30 | Two cameras on one bench, the protocol on the right. | Both feeds play (the clips loop). Status dots top right: angle stays amber with scripted vision, because it only measures inside a vision box. Eight steps listed. |
| 0:00 | Clicks Start run. | Step 1 current. Coach: "First: put a fresh tip." |
| 0:05 | "A tip goes on." | Step 1 ticked (seen). Coach: "Next: draw 100 microlitres of water from tube A." |
| 0:11 | "Watch the angle while drawing." | Red banner and coach: "Pipette looks tilted about 27 degrees. Keep it under 20." Gauge reads 28. |
| 0:12 | | Step 2 ticked. Coach: "Next: dispense it into tube B." Gauge back to 11 at 0:14. |
| 0:22 | | Step 3 ticked. Coach: "Next: eject the tip." |
| 0:33 | "Now the mistake everyone makes: same tip into the dye." | Coach: "Same tip used twice. Change it before tube C." Steps 4 and 5 skipped, step 6 ticked. Coach: "Next: dispense it into tube B and mix three times." |
| 0:45 | "And they forget the dispense." | Step 7 skipped, step 8 ticked. Coach: "You skipped step 7: dispense it into tube B." then "Run finished. Check steps 4, 5 and 7." |
| ~0:55 | Clicks Stop run. | "Run complete. Review your lab note." The Lab note tab opens with a dot within about a second. |
| 1:00 | Reads the note on the page. | 5 done, 3 skipped. Technique: the 28 degree tilt in step 2, the reused tip. Each item has a time for camera 1 and camera 2. |
| 1:30-3:00 | Clicks Open full note, then a time under "What went wrong". | The recording jumps to that moment; the stills sit under each problem. Close on the note. |

Clicking Start again resets the page and replays the same script.

## Real vision on the sample videos (one minute)

The 12-27 clips show someone disinfecting gloved hands (`CP-12-27-protocol.txt`); there is no
pipetting in them. That makes them a good check for false alarms, not for detection.

- First minute (before the fixes below): 239 `objects` events (about 2 per second per camera,
  as the contract allows). Labels: waste 190, tip box 130, plate 104, tube rack 91, pipette tip
  76, pipette 14, tube 1. Actions: 4 `tip_off` at 0.6 confidence, all false (there is no waste
  bin in these clips). No draw, dispense, tip_on or hand_touch. The first false `tip_off`, 1.8
  seconds in, made the coach mark steps 1-3 skipped and warn "You skipped steps 1, 2 and 3."
  Angle sent nothing: it only measures within 2 seconds of a draw or dispense.
- Second minute (after the fixes): 226 `objects`, 1 false `tip_off`, now at 0.5. The coach
  stayed on step 1 and said nothing more. The note read "1 not finished, 7 not reached. Nothing
  went wrong that the system could see." That is the right answer for this clip.
- Hint mode, 30 seconds: 4 draw and 3 dispense hints (objects "bottle" or none), 1 `tip_on`
  (0.5, marked unsure). The hints opened angle's window and angle read 29, 56, 30, 33, 53, 28,
  60, 81 and 9 degrees, all marked rough, mostly from vision "pipette" boxes. On these clips
  vision's "pipette" box sits on a camera tripod in both views (checked by drawing the cached
  boxes on frames). Angle raised three tilt warnings from those; the coach repeated them.

## Problems found

Fixed, each with a test:

1. No way to run scripted vision through the launcher. Added `scripts/fake_vision.py` and
   `demo.sh --fake-vision` / `--fake-speed`. Test: `scripts/tests/test_launcher.py`
   (`test_fake_vision_and_angle_without_roi`), `tests/test_rehearsal.py`.
2. Angle warned of a 66 degree tilt that was a bench edge. With no vision box and no chosen
   crop, angle measured the longest line in the whole frame. It now has `--need-box`, which the
   launcher passes unless `--angle-roi` is given. Test: `angle/tests/test_feed_events.py`
   (`test_need_box_never_measures_the_full_frame`).
3. The coach stayed silent when Start was clicked. It had said "First" when it launched
   and treated Start, 48 seconds later, as a repeat. It now skips the line only if it was said
   in the last 5 seconds. Test: `coach/tests/test_brain.py`
   (`test_start_pressed_long_after_the_coach_started_says_the_first_line`).
4. The note printed rough angle readings as firm numbers. It now adds "(rough camera
   estimate)" when angle marked a reading rough. Test: `note/tests/test_build.py`
   (`test_rough_angle_keeps_its_caveat`).
5. The launcher refused to restart right after a demo ("app: port 8820 already in use")
   because closed connections leave the port in TIME_WAIT for a while. The port check now
   matches how the servers bind, so only a live listener blocks. Test:
   `test_port_in_time_wait_is_free_but_listener_is_not`.
6. One false `tip_off` skipped three steps. Vision's tip rules sent 0.6 confidence, exactly
   the coach's line for proof, though vision's own report scores them 0 right of 2 on labelled
   clips. Both tip rules now send 0.5, like draw and dispense, so the coach treats them as
   unsure and never skips on them. A real tip pick-up now shows as "unsure", not "done".
   Test: `vision/tests/test_events.py`
   (`test_tip_rules_stay_below_the_coachs_solid_evidence_line`).
7. After Stop the coach kept going. It asked "Did you just draw from tube A?" and warned
   about tilt after the run had ended. It now ignores camera and angle events after Stop until
   the next Start (it still answers questions). Test:
   `test_after_stop_camera_events_are_ignored_until_the_next_start`.
8. The coach dropped angle's "rough estimate" caveat and said "looks tilted" about a tripod.
   For rough readings it now says "Pipette may be tilted about 53 degrees." Test:
   `test_rough_angle_is_hedged`.
9. Preflight always failed its protocol check. It imported `load_protocol` from the coach,
   which is called `load`. Five tests in `scripts/tests/test_preflight.py` were red on main and
   are green now.

Still open (for the part owners):

10. Vision's "pipette" box lands on camera tripods in both 12-27 views. Fixed on October 31.
    Each pipette box says whether a hand is holding it, and angle measures only a held pipette
    during a draw or dispense, never after Stop; readings over 45 degrees are logged as likely
    mis-detections instead of warned about (angle/TILTFIX-REPORT.md). The sweep's saved hint-mode
    replay, which had warned about 55 and 89 degrees, now gives no tilt warning. A real bench with
    a held pipette is still the only test of the gauge itself; the scripted demo or a chosen
    `--angle-roi` remains the reliable way to show it on stage.
11. Real vision found no draw or dispense in two minutes of these clips, which is correct
    here because there is no pipetting in them. Detection on real pipetting was not checked in
    this rehearsal; vision's REPORT.md has the labelled-frame numbers.
12. The coach says "First: put a fresh tip." when it launches, before any run, so the line
    shows in the conversation panel before Start. Harmless; it says it again at Start.
13. Angle and vision keep sending events after Stop. The hub keeps them in its history but
    not in the page state, and the note ignores them. Harmless.
14. The reused-tip warning has no step number (`step: null`); the other warnings have one.
15. Table mode was not tried on a real bench; no overhead camera or caps were available.
    The October 31 sweep did run table mode through the launcher on the generated overhead video
    from `tests/test_table_e2e.py`: 5 done and 3 skipped, as scripted.

