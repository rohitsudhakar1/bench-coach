# NVIDIA UI report

No event-contract changes proposed or made. Changes are confined to app/. Files are
uncommitted for the lead to review.

## What works

Stop queues the NVIDIA task exporter and rules solver in a background subprocess.
The Lab note tab shows questions, checker descriptions, task-folder ZIP links and
mean rules-solver reward. It works even when the note service is unavailable.
Make synthetic runs queues 20 runs and shows the engine, note generator and per-step
skip-rate comparison. Export and synthesis have separate status lines and retry controls.
Completed results survive restart. Pending jobs are deduplicated, serialized and limited
to 170 seconds; hub shutdown kills the worker. The latest stopped run's unfinished
export resumes on startup. Invalid IDs, unknown downloads and symlink trees are refused.
No audio was played, no packages installed, no server left running.

The earlier app reports described raw Markdown notes and download work. This change
preserves the rebuilt note iframe and puts the training section outside it. There is no
NVIDIA directory in the earlier draft; the rebuild's NVIDIA modules supply this feature.

## Checks and measured results

`$BENCH_PY -m pytest app/tests nvidia/tests -q -p no:cacheprovider`:
27 passed, 2 skipped in 1.10 seconds. The existing skips require external sample files
or Data Designer. New route tests use fake folders and exercise automatic export,
ZIP contents, synthesis, persisted results, errors, path refusal and worker shutdown.
A stalled-worker test verifies Stop returns, health responds and a new run can start
while export remains running. Existing app routes and NVIDIA module tests still pass.
`node --check app/static/app.js` passed. Both note-player JavaScript tests passed.

A direct check using the bundled fake run exported 4 tasks; the rules solver scored
1.0. Local synthesis produced exactly 20 runs, seed 0, prior 1, no hosted model call.
For protocol steps 1 through 8:

| Rate | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Recorded, smoothed | .125 | .125 | .125 | .625 | .625 | .125 | .125 | .125 |
| Synthetic | .20 | .10 | .15 | .65 | .30 | .25 | .05 | .20 |

These are synthetic pipeline checks, not recognition or independent answer accuracy.
The gap at step 5 shows the sampling variation in a small generated batch.

## Limits

Browser checks were extended for task links, the synthesis button/table and desktop
overflow. Chromium failed before opening a page with a sandbox MachPortRendezvous
permission error. Visual layout and browser interactions remain unverified here.
The HTML/CSS retain internal scrolling for the training section and note panel.

Data Designer and hosted Nemotron were not run in this worktree. The worker selects
the optional NVIDIA Python environment when present and delegates engine choice to
nvidia.synth. A broken optional environment reports failure rather than silently
claiming Data Designer ran. Error details are deliberately generic to avoid exposing
local paths or provider secrets. Synthesis uses only the selected stopped run, not
other runs with potentially different protocols. Completed jobs are reused rather
than regenerated on repeated clicks. The hub still requires a single worker.

The public-path scan found no prohibited absolute home paths in tracked files or
new app files. `git diff --check` passed. No dependencies or event-contract edits.

## Git log

Requested command: `git log --format='%h %an %aI %cI' main..HEAD`

Output was empty: this branch has no commits ahead of main. All changes remain
uncommitted; author settings and history were not changed.
