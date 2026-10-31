# Offline start: report

Goal: at a hackathon the Wi-Fi is slow, and a fresh setup downloads models on its first run.
Fetch them ahead of time, prove they load with no network, and make the demo start with
Wi-Fi off.

## What changed

- `scripts/fetch_models.py` lists every model the default demo loads, with size, licence
  and whether it is on disk. `--download` fetches the missing ones into the normal caches
  and then checks them. `--check` loads each one in its own process with `HF_HUB_OFFLINE=1`,
  `TRANSFORMERS_OFFLINE=1` and every outside DNS lookup and connection refused, runs it once
  on a blank input, and fails if a file is missing, a load fails or anything tried the network.
  `--with florence2 gdino-base owlv2` adds the optional detectors.
- The launcher (`run_demo.py`) works out which models the parts it starts will load. When all
  of them are on disk it sets the two offline flags for the parts and says so. A flag the user
  set is kept. When one is missing it names it and says the first start will download it.
- `vision/hands.py` no longer tries to download the hand model when the offline flags are set,
  and a failed download names the fix and leaves no half-written file.
- Preflight has an `offline ready` line that runs `fetch_models.py --check` (`--no-offline`
  skips it).
- README "Try it in five minutes" has the one prefetch command; `scripts/README.md` has a
  "Models and Wi-Fi off" section.

## Models the default demo needs

| Model | Size | Licence | Loaded by |
| --- | --- | --- | --- |
| Grounding DINO tiny (`IDEA-Research/grounding-dino-tiny`) | 690 MB | Apache-2.0 | vision |
| MediaPipe hand landmarker (`vision/models/hand_landmarker.task`) | 8 MB | Apache-2.0 | vision |
| faster-whisper small.en (`Systran/faster-whisper-small.en`) | 486 MB | MIT | coach, preflight mic |
| Silero VAD (inside the faster-whisper package) | 2 MB | MIT | coach |
| Optional: Florence-2 base, Grounding DINO base, OWLv2 | 470, 935, 622 MB | MIT, Apache-2.0, Apache-2.0 | vision `--detector` |

About 1.2 GB for the defaults. Not covered: YOLOE (AGPL, downloads itself into
`VISION_YOLO_DIR`) and the unadopted tip model (built locally). Hosted wording and answers
(Anthropic, Gemini, NVIDIA) need a key and a network; without either they already fall back
to the rule-based text, which was checked in the code, not changed.

## Measured on this Mac

No model was downloaded. The Hugging Face models were already cached; the hand model was
copied from another local checkout.

- `fetch_models.py --check`, default four: all pass, 4.6 s wall time (Grounding DINO 2.8 s,
  hand 0.6 s, Whisper 1.2 s, VAD 0.2 s), zero network attempts. With the three optional
  detectors too: all seven pass in 16.5 s.
- With the hand model removed the check prints `FAILED hand-landmarker missing ...; run
  scripts/fetch_models.py --download` and exits 1; preflight's line becomes
  `FIX offline ready`.
- Why the launcher sets the flags: with the network cut off and no flags, the cached
  Grounding DINO took 71 s to load, because the Hub library retried five times before
  falling back to the cache. That is past the launcher's 60 s startup deadline. With the
  flags it took 2.1 s. faster-whisper fell back in under a second either way.
- Full demo with the network cut off: a `sitecustomize.py` on `PYTHONPATH` refused and logged
  every outside DNS lookup and connection in every Python process. The launcher was started
  with no offline flags, no API keys, the two 12-27 sample views and a recorded question as
  the microphone. It printed `models: all on disk, parts start offline` and reached
  **Demo ready** in 6 s with all six parts. A 25 s run gave 92 vision object events, tip
  events and a lab note. The log showed zero network attempts. Ctrl-C exited 0 and freed
  every port. (The guard was confirmed live: the same setup blocks and logs a plain request
  to huggingface.co.)
- Coach's own Whisper loader under the launcher's flags and the same guard transcribed the
  recorded "What's next?" correctly in 1.2 s with zero network attempts. In the demo above
  the coach loads Whisper only when speech arrives, so this was tested separately.

## Tests

`scripts/tests/test_fetch_models.py` (13 tests): the default list matches vision's own model
id and hand path, cache lookups on a fake cache (missing, partial, empty file, complete),
the listing, a missing model failing the check with the fix, download refused with the
flags set or a short disk, the network guard blocking outside but allowing localhost, the
hand loader never downloading offline and never leaving a half file, the launcher's
model needs per mode, the flags set only when everything is here and never over a
user's own setting, and a real offline check that runs when the default models are on disk.
`scripts/tests/test_preflight.py` gained two tests for the new line; the older preflight
tests stub the model check so they stay fast and need no models. Full suite: 492 passed,
20 skipped (sample clips not set) in 205 s.

## What does not work or was not tested

- `--download` was not run for real here (no new downloads on this machine). The Hugging
  Face path is `snapshot_download` with the same file list the check needs, and the hand
  path is the same URL vision already used.
- The Wi-Fi-off test cut the network inside Python, not by turning the Mac's Wi-Fi off.
  Anything outside Python (ffmpeg, the browser) was not covered; the dashboard loads no
  outside assets.
- If a user keeps `HF_HUB_OFFLINE=0` set with Wi-Fi off, vision still takes about 70 s to
  load, as before.
- Whisper models other than small.en (`--whisper-model`) are not listed.

## Git log

```
9b426e8 Andy Bei 2026-10-31T05:14:52-04:00 2026-10-31T05:14:52-04:00
14f0d1c Andy Bei 2026-10-31T05:11:07-04:00 2026-10-31T05:11:07-04:00
33f3f88 Andy Bei 2026-10-31T05:08:58-04:00 2026-10-31T05:08:58-04:00
2ff8f72 Andy Bei 2026-10-31T05:04:46-04:00 2026-10-31T05:04:46-04:00
(this last commit adds the log itself)
```
