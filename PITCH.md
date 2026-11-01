# Three-minute pitch

What the presenter says, in order. The live run follows REHEARSAL.md. If the camera fails, play
the video made with `scripts/render_demo.py` from a real recorded run (the 9am checklist in
scripts/README.md) and say which steps on it are marked hand-labelled. Do not use the older
`runs/demo-video` video: its camera clips and its scripted steps do not match.

## 0:00 to 0:30, the problem

Every new scientist learns pipetting by being watched. A trainer stands behind them and checks
the tip, the tube, the angle and the order. Trainers run out of time long before students run out
of mistakes, and the mistakes that matter (a reused tip, a skipped reagent) stay invisible until
the experiment fails days later.

## 0:30 to 0:45, what we built

Bench Coach is two cameras and a microphone on the bench. It follows the written protocol as the
person works, says the next step out loud, warns the moment something goes wrong, and writes the
lab note when they finish.

## 0:45 to 2:00, live run

Load the protocol. Press Start. Work the steps while the page follows along.

- Fresh tip on. The step ticks and the coach says what is next.
- Draw from tube A with the pipette leaning. The angle gauge turns red and the coach says to
  bring it upright.
- Go into tube C with the same tip. The coach says the tip was used twice.
- Skip the dispense into B. The coach names the skipped step.
- Ask out loud "what's next?" and "did I add tube C?". It answers in about a second and a half.
- Press Stop. The lab note opens with every step, its time, what went wrong with pictures from
  both cameras, and each time jumps the recording to that moment. Download the PDF.

## 2:00 to 2:30, NVIDIA open problem 04

NVIDIA's talk asked teams to turn private lab data into environments and training signal. Every
Bench Coach run already is that data: the protocol, what the cameras saw, what the person said,
and which step went wrong. `python -m nvidia.export_tasks` turns a run into tasks in Harbor's
layout with checkers ("which step was skipped", "rewrite the missing step", "was the tip
reused"), and `python -m nvidia.synth` uses NeMo Data Designer to make synthetic runs that keep
the skip rates without names, speech or times.

## 2:30 to 2:45, how it works

Capture keeps both cameras and the mic on one clock. Vision follows the pipette and the tubes.
Angle measures lean from two views. The coach is a rule-based state machine that owns every step;
Claude only words answers and the note, and Gemini Live is an optional voice. Steps the person
confirms out loud are marked "said", steps the cameras saw are marked "seen".

## 2:45 to 3:00, where it goes

Teaching labs and biotech training programs first: one coach per bench, a note per student, and a
trainer who reviews the flagged moments instead of watching the whole session.

## If a judge asks how well it works

Say the numbers in SCORECARD.md as they are. Table mode, with colour-coded tubes and a camera
looking straight down, was tested on synthetic video before the table; say what the preflight and
the live run showed. General vision on other labs' video finds the pipette but catches draws and
dispenses unreliably, which is why the coach never ticks a step from a guess; in hint mode it asks
the person instead. Voice understood 4 of 4 test questions over bench noise. Angle was off by
1.3 degrees on average on hand-boxed still frames; on a real bench it needs the two-minute
calibration. The NVIDIA task answers come from what Bench Coach logged, so a vision miss makes an
answer key wrong too.

## If a judge asks what existed before

The team has earlier related work on recording lab bench video. Everything in this repository was
written during the hackathon, with AI coding agents, and the commit history shows when.
