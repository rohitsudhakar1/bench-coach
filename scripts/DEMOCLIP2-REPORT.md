# Demo video report (democlip2)

No change proposed to docs/EVENTS.md.

## What there is

`runs/demo-video/bench-coach-demo.mp4` (not committed): 2:26, 1920x1080, H.264 at 30 fps, no
audio track, 26 MB. `scripts/make_demo_video.py` makes it from scratch in about 3 minutes.

The first part is unchanged: title card, both feeds and the protocol, Start, steps ticking, the
tilt, the reused tip, the skipped step, Stop, the Lab note tab and the full note with the two
jump-to-moment clicks (players went from 0 s to 11 s, then to 33 s). The student name
"Student A" is now typed before Start so the run can be found later. After the note:

1. The Lab note tab with Download PDF, Benchling HTML, eLabFTW .eln and Steps CSV outlined
   together (5 s), then the PDF download and its first page, as before.
2. The "Training tasks (NVIDIA)" section outlined (7 s). It showed "Rules solver: 0.8 / 1
   across 5 tasks" with the task questions and their zip links.
3. `/trainer` for about 6.5 s, with this run's row outlined (matched by run id).
4. `/class` for about 6.5 s with three bench cards: Bench 2 and Bench 3 first, both "Trainer
   needed", then Bench 1 "Run stopped".

The yellow tag "Sample clips as cameras. Vision events scripted for this recording." stays on
through the run and the trainer page. On `/class` it changes to "Benches 2 and 3 replay the
same scripted events on the sample clips.", and those students are named "Student B
(replayed)" and "Student C (replayed)".

## How it works

The launcher now starts with `--benches 3`, so it uses ports 8700 to 8717 (six per bench),
not just 8700 to 8705. The script refuses to start if any of the 18 is taken, and all 18
were free before and after each take. Every bench runs `scripts/fake_vision.py` on the same
12-27 clip pair. Bench 2's run starts when the page returns to the Lab note tab and bench 3's
after the PDF page, so at `/class` one is at step 7 and one at step 3. Both are stopped
before the launcher shuts down.

## What I checked

- The script fetches each download link and records status, size and first bytes. Last take:
  PDF 200, 287 KB, `%PDF-`; Benchling 200, 4.6 KB, `<!DOC`; eLabFTW 200, 341 KB, a zip;
  CSV 200, 1 KB, starts `step,`.
- Trainer listed 3 rows (this run plus the two replayed ones); `/class` showed 3 cards.
- I pulled a frame every 8 seconds (18 frames) and looked at each one. Two problems found
  and fixed: the trainer row highlight was a pale fill that washed out the light text (now
  a dark amber fill), and runs left over from my own failed first take showed up on the
  trainer page. I deleted those run folders before the final take. A presenter recording
  on a machine with older runs will see them listed too; this run's row is still the one
  outlined.
- `pytest scripts/tests/test_make_demo_video.py`: 11 passed. New tests cover the three
  benches, the "replayed" labels, the four download buttons and NVIDIA panel existing in
  the page, and the per-page tag text. The finished-video test now expects 120 to 150 s.

## Not done / limits

- The extra benches are labelled as replayed on `/class` only, and in their names on
  `/trainer`. Their cards have no feeds; they reuse the same footage.
- At 2:26 the cut is near the 2:30 ceiling. Any added scene needs a cut elsewhere.

## Date check

`git log --format='%h %an %aI %cI' main..HEAD` before this report was committed:

```
34e8a7e Andy Bei 2026-10-31T04:10:46-04:00 2026-10-31T04:10:46-04:00
969df32 Andy Bei 2026-10-31T04:02:00-04:00 2026-10-31T04:02:00-04:00
```
