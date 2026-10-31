# nvidia part report (Oct 30, 2026)

No contract change. Synthetic runs use the `docs/EVENTS.md` format with `source: "synth"`.

## Works, checked here
- Two sample runs export to 9 tasks in Harbor's folder layout. Reference answers score 1.0, blank answers 0.0. A rules solver that sees only the protocol and events scores 0.889; it fails only "write step 7", because no other step has the same text.
- One task's own `tests/test.sh`, run with its container paths pointed at temp folders, wrote reward 1 for a right answer and 0 for a wrong one.
- `python -m nvidia.synth` with no key runs a seeded local generator, says so, and repeats exactly for the same seed. With NeMo Data Designer 0.9.7 installed in a separate environment (`nvidia/.venv`, not committed), Data Designer's own samplers draw the run settings with no key, network or GPU. Over 40 runs the per-step skip rates landed within about 0.1 of the recorded ones, except step 5 in the Data Designer draw (0.875 against 0.77).
- No speech, names, real run ids, times or paths reach the synthetic runs; a test checks this.
- Tests: 9 passed, 1 skipped in the shared environment (the skipped one needs Data Designer); 10 passed with Data Designer installed.

## Not working or not tried
- No `NVIDIA_API_KEY` or `ANTHROPIC_API_KEY` here. The Nemotron note column passed Data Designer's offline config check with a dummy key; no live call was made. `--answer claude` was only checked for refusing without a key.
- Harbor CLI and Docker runs were not tried (Docker daemon off).
- Synthetic tip reuse comes out at 0.68 locally and 0.86 with Data Designer, against 1.0 recorded from only 2 draws; reuse follows from skipped tip steps rather than being sampled directly.
- The statistics come from two runs of one protocol, so the default `--prior 1` smoothing is a choice, not data. The answer key is what Bench Coach logged, so a vision miss makes the key wrong too.
- NeMo Anonymizer needs a GLiNER detector plus an OpenRouter key, and Safe Synthesizer is a platform microservice, so neither is used; speech is dropped instead.

## Choices
- Data Designer lives in its own environment because installing it in the shared one would downgrade huggingface-hub under vision.
- Step numbers are 1-based. Tasks default to `runs/<run>/tasks/`, synthetic output to `nvidia/out/synth/`.
