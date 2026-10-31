# Fresh clone report

Goal: a judge clones the public repo and gets the demo running without help.

## What I tried, in a throwaway folder outside the project

1. `git clone https://github.com/rbei001/bench-coach` (main at e64228a).
2. Setup. Before this branch there was no setup script and no single requirements file; a judge had to read seven part folders and install each list by hand. Two of those lists ask for `opencv-python` and two for `opencv-contrib-python`, and installing both makes them overwrite each other's `cv2`.
3. Replay demo on a check port: hub came up, page loaded, steps ticked off and the coach lines showed. The only browser console errors were the camera feeds, which is expected with capture left out.
4. Full sample-video demo (all six parts) from the clone's own venv: every part reported ready, Start run, Stop run and the lab note all worked, and the run folder held both videos, the mic file, events and the note.
5. Test suite without `BENCH_SAMPLES`: 342 passed, 10 skipped (they need the sample clips or NeMo Data Designer). One test failed in each of two full runs, a different one each time (`scripts/tests/test_launcher.py` restart check, then `tests/test_table_e2e.py`). Both passed when rerun alone. Each failure matched another builder's test server sitting on a check port at the same moment, so I count them as machine contention, not fresh-clone faults.
6. Repeated steps 2, 3 and 5 on a clean clone of this branch. Setup took about 25 seconds with uv's cache warm; the venv is 1.3 GB.

## What I changed

- `scripts/setup.sh`: makes `.venv` in the repo with uv (Python 3.12) or, without uv, with `python3 -m venv` after checking for Python 3.10 to 3.12. Installs `requirements.txt`, warns if ffmpeg is missing, checks the core imports, and prints the next commands.
- `requirements.txt` at the root: every part's packages with the limits the parts set (`pydantic>=2`, `faster-whisper>=1.2`, `google-genai>=2.34`, `weasyprint>=70,<71`). It lists only `opencv-contrib-python`, which already contains the plain build. NeMo Data Designer stays out because nvidia keeps it in its own environment. `playwright` is in, for the browser checks.
- `tests/test_fresh_clone.py`: fails if a part adds a package or pin the root file lacks, if both OpenCV builds come back, if the quick-start files stop being committed, or if a tracked file contains a home-folder path.
- README: a "Try it in five minutes" section at the top.

## Not covered

- The pip fallback (no uv) was only checked for its version guard; a full pip install of PyTorch would have used most of the free disk.
- Model weights and the Playwright browser were already in this Mac's user caches, so a judge's first sample-video run and first browser check will download more than mine did. The README and setup output say so.
- Sample videos are not in the repo, so the full pipeline needs the judge's own two clips.

## Git log

```
83bcecb Andy Bei 2026-10-30T23:09:52-04:00 2026-10-30T23:09:52-04:00
3f191b2 Andy Bei 2026-10-30T23:05:26-04:00 2026-10-30T23:05:26-04:00
79ecafb Andy Bei 2026-10-30T22:57:09-04:00 2026-10-30T22:57:09-04:00
```
(plus the commit that adds this report)
