# note: report

## Contract proposals (docs/EVENTS.md not edited)

1. Add the note service's other routes to the ports table: `GET /note/{run}.pdf`,
   `GET /note/{run}.zip`, `GET /note/{run}?format=md|json`, `GET /runs/{run}/{file}` and `GET /health`.
   `POST /note` stays for manual rebuilds; the app does not need it.
2. The `note` event's `path` is `/note/<run>` on the note service and `url` is its full address,
   as the decisions say. Worth one line in the table.
3. Say in the contract that only events between a run's start and stop belong to it. The earlier
   draft's rehearsal log has `smoke-tube` draws written an hour after its stop; the note drops them.

## Note memory follow-up

No new contract changes proposed. Builds and PDF renders now use one short-lived
worker per job, with bounded concurrency, a timeout and kill/reap on timeout.
Metadata captures also release on property-read errors. Outputs and event shapes
are unchanged; all changes remain uncommitted under `note/`.

Ten real-sample builds with PDFs: parent RSS was 259.45 to 308.83 MiB before and
40.80 to 43.61 MiB after. After two warmups, spread fell from 35.72 to 2.06 MiB.
Combined parent/worker sampled peaks after the change were 193.31–210.42 MiB.
Build plus PDF takes about 0.68 seconds instead of 0.30 seconds after warmup.
The capture note probe also passed; see `LEAK-REPORT.md` for all measurements,
commands, diagnosis and limits. This is not a full-pipeline or multi-hour soak.

Final validation with sample clips enabled: 45 note tests passed in 28.75 seconds.
The real-clip memory regression separately skipped with BENCH_SAMPLES unset.
Timeout cleanup, worker recovery, metadata failure, PDF/ZIP contents, service
routes and hub-triggered builds are covered. `git diff --check` and the required
tracked-file public-path audit passed; new source/test/report files were checked
separately. No dependency was installed. No test server or worker was left running.

Required current history audit:
`git log --format='%h %an %aI %cI' main..HEAD`
Output: empty (zero lines). No commits, date/identity overrides or history edits.
The original build report and its historical audit follow below.

## What works

- `python -m note build <run folder>` writes note.md, note.html, note.json and key frames.
- `python -m note serve` on 8705: builds the note on the hub's run stop (after the hub has logged the
  stop and capture has closed timing.jsonl), posts the note event with `{run, path, url}`, or prints
  it when the hub is down. Serves HTML, Markdown, JSON, PDF, the run zip and the recordings.
- Steps with done, skipped, unsure, not finished or not reached, seen/said/seen+said labels and times.
  Seen steps are timed at the camera event that proved them. With no coach events the note walks the
  protocol's evidence itself.
- Hints never count as proof, for steps or for the tip check.
- Angle per step against the limit; readings without a step go to the step that was current.
- Fresh or reused tip per draw. Coach warnings that repeat the note's own lines are dropped.
- Up to 3 key frames, one per camera, for the most serious problems.
- Every time links into each camera file using capture's frame rows, so late starts and dropped
  frames give the right spot. The scripted run drops 5 s of camera 2 and the stills show it.
- A4 PDF through WeasyPrint with page numbers and embedded frames; zip without videos.
- Optional Claude wording pass that is checked line by line and can only reword.

## Measured

- Tests: `$BENCH_PY -m pytest note/tests -q`: 32 passed with BENCH_SAMPLES set (8.6 s); without it
  31 passed and the real-clip test skips. One test runs a stand-in hub websocket and the real note
  service on free ports in 8840-8899 and waits for the note event; both servers are stopped after.
- The earlier draft's real rehearsal run (copied, two real camera files, 3,364 frame rows): note built
  in 0.44 s including 6 key frames. Result: 5 done, 3 skipped, tilt during step 2 (4 of 4 readings
  over, highest 28°), reused tip into tube C, skipped steps 4, 5, 7, as the rehearsal script expected.
  The `smoke-tube` events after the stop were left out.
- Scripted run: PDF is 3 A4 pages, 59 KB; run zip 102 KB. Checked by eye: page, PDF and stills.
- Live service check on port 8805: POST /note, GET html, pdf and zip all 200; server stopped by PID.

## What does not work yet / not tested

- The Claude wording pass has not run against the real API: no key on this machine. It is tested
  with a stand-in that checks good rewrites are kept and rewrites with a changed number, a new name
  or a new tube letter are thrown away. Without a key the note is rule-based.
- Not tried against the rebuilt hub, coach, angle and capture parts yet; built to docs/EVENTS.md,
  the draft's real run folder and a scripted run.
- Speech filtering is by keywords, so an important sentence with no number or observation word is
  left out.
- The note is only as right as the coach's step events and vision's evidence; it does not check them
  against the video.

## Git log

`git log --format='%h %an %aI %cI' main..HEAD` before this report's own commit:

```
928fac6 Andy Bei 2026-10-30T20:18:49-04:00 2026-10-30T20:18:49-04:00
9a74345 Andy Bei 2026-10-30T20:17:55-04:00 2026-10-30T20:17:55-04:00
367976d Andy Bei 2026-10-30T20:15:57-04:00 2026-10-30T20:15:57-04:00
```
