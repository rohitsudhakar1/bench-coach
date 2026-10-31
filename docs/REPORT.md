# Docs2 report

No event-contract changes proposed.

Updated the root README for the trainer, phone step view, camera calibration page,
NVIDIA tasks in Lab note, five teaching protocols, held-pipette angle rule, tip
pick-up rule and opt-in dispense dwell filter. Kept the hackathon authorship line.
Replaced the missing-scorecard claim with current results and chance baselines,
including weak real-video results and the synthetic and scripted limits.

Only README.md and this report are deliverables. There was no docs/ARCHITECTURE.md
in this checkout. The root README serves as the part guide under the brief's file
restriction. No packages were added. No prior docs2-specific README or report was
present in the earlier draft; its project README, scorecard, rehearsal and existing
docs report were reviewed as background.

## Checks

Local Markdown link targets exist. The selected result and chance table cells match
SCORECARD.md exactly. The public-path scan found no matches. No new benchmark was
run; the README quotes the existing scorecard dated October 30, 2026 at 22.40 EDT.
The full suite and browser checks were not rerun for this documentation change.

The isolated rehearsal command used the shared Python through BENCH_PY, sample
clips through BENCH_SAMPLES, disabled bytecode and pytest caching, and a temporary
folder in this worktree. It returned 1 failed, 1 passed in 24.03 s. All six parts
became ready and the test reached the lab note. Launcher shutdown then failed at
scripts/run_demo.py line 270 because the sandbox denied os.killpg(process.pid, 0).
This was not the known full-suite load timeout and does not establish an isolated
pass here. No code was changed to bypass the restriction. A subsequent listener
check found no servers on the test's ports 8840–8845. Temporary outputs were removed.
The README retains the reported isolated-pass guidance and states this local limit.

Whitespace checks passed.

## Commit log

Ran the required command before finishing.

```sh
git log --format='%h %an %aI %cI' main..HEAD
```

Output was empty. No commits were made. Files are left uncommitted for the lead.
