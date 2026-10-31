# CI report

No event contract changes proposed.

## Delivered

The workflow runs on pushes and pull requests on `ubuntu-latest` with Python
3.12, read-only repository permissions, dependency caching and a ten-minute
job deadline. Superseded runs on a branch are cancelled. It installs FFmpeg,
Pango, HarfBuzz, image/font libraries and Poppler before the Python packages.

`requirements-ci.txt` installs the CPU test dependencies separately from the
parts' full model stacks. Linux torch is pinned to `2.9.0+cpu` from the PyTorch
CPU index; the workflow asserts that its CUDA runtime is absent. Headless
contrib OpenCV retains the shaft tracker. WeasyPrint remains at version 70.

Root `pytest.ini` collects every part and registers `models` with strict marker
validation. The workflow runs `python -m pytest -m "not models" -q -ra
--durations=15` with `BENCH_SAMPLES` unset. Hugging Face and Transformers run
in offline mode and vision selects CPU. No model weights are installed.

Six tests are deselected: two recorded-speech Whisper tests, two Whisper CLI
tests, the vision CLI test that loads MediaPipe even with `--no-detector`, and
file-only system speech synthesis, which requires an installed speech voice.
Mocked model clients, synthetic audio gates and cached detection checks run.
No current test requires a real GPU, Docker daemon or physical audio device.
New such tests must carry the same marker.

The table integration test now probes for an available six-port block in the
allowed test range instead of requiring one fixed block. It still verifies
that its ports are released after shutdown and never stops other services.

## Validation

- `actionlint .github/workflows/tests.yml`: passed with actionlint 1.7.12.
- Installed only `requirements-ci.txt` and its dependencies into a fresh Python
  3.12 virtual environment using the prescribed package helper. Dependency
  installation resolved 49 packages; `python -m pip check` passed.
- The first full run had 335 passes, eight skips and one failure in 120.81 s.
  The file-only system speech test produced a zero-duration file. It is now
  marked as requiring a system speech voice; no application assertion changed.
- A second run had 334 passes and one failure because the table test
  required an occupied fixed port. An isolated rerun confirmed the same
  conflict. The test now selects a free block; no assertions were weakened.
- The CPU runtime assertion passed.
- Final clean-environment suite: **335 passed, 8 skipped, 6 deselected**
  in **93.76 seconds**, with one upstream deprecation warning. The table
  integration and its port-release assertions passed in 25.35 seconds.

The skips cover absent sample videos, an empty sample protocol parameter set
and optional Data Designer. The synthetic table end-to-end test, PDF export,
HTTP/WSS transport, launcher cleanup and all parts' logic remain in the run.
The first run's longest test was the table integration check at 25.49 s.
No sound was played and no hosted model calls were needed.

## Limits

The hosted Linux job has not run from this uncommitted branch. Its package
installation and total cold-start time still need a hosted measurement; the
ten-minute limit enforces the budget. Local validation used the platform's
torch wheel and installed system PDF/media libraries. It does not prove the
Linux native library installation. Browser scripts and JavaScript tests are
outside pytest and outside this brief. No live recognition, GPU, Docker or
physical microphone result is claimed. One upstream Starlette/AnyIO
deprecation warning appeared during validation.

## Public repository and history checks

`git diff --check` passed. The required tracked-file scan for private home
paths returned no matches; new deliverables were checked separately. Temporary
environments and raw logs are removed before handoff.

`git log --format='%h %an %aI %cI' main..HEAD` produced no output.
No commits were made; all changes are left for the lead to review and commit.
