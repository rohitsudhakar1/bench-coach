# nvidia: bench runs as training tasks and synthetic data

NVIDIA's BioNeMo talk at the hackathon listed open problem 04: turn private lab records into
tasks with a checker, and make synthetic training data that keeps the statistics without the
secrets. A Bench Coach run is that kind of record. It holds the protocol, the camera event log,
which steps were skipped, and the lab note. This part does two things with it.

1. `python -m nvidia.export_tasks runs/<run>` turns a recorded run into question tasks in
   Harbor's task folder layout. Each task has a checker that scores an answer against what the
   run recorded.
2. `python -m nvidia.synth` makes a corpus of synthetic bench runs that follow the measured skip
   rates, tilt and timing of the recorded runs. Nothing a person said, no names and no real run
   ids, times or file paths go into it.

## Run it at the table

Run these from the repository root. They need no key and no network.

```sh
PY="${BENCH_PY:-python3}"

# tasks from one run (written to runs/<run>/tasks/), then score the offline solver on them
$PY -m nvidia.export_tasks runs/<run>
$PY -m nvidia.runner runs/<run>/tasks                 # rules solver
$PY -m nvidia.runner runs/<run>/tasks --answer reference   # should be 1.0
$PY -m nvidia.runner runs/<run>/tasks --answer blank       # should be 0.0

# 50 synthetic runs from every run under runs/ (or nvidia/samples if runs/ is empty)
$PY -m nvidia.synth --n 50                             # local seeded generator
nvidia/.venv/bin/python -m nvidia.synth --n 50         # NeMo Data Designer
$PY -m nvidia.export_tasks nvidia/out/synth/runs --out nvidia/out/synth-tasks

# tests
$PY -m pytest nvidia/tests -q
```

To try it without a recorded run, use the two samples: `nvidia/samples/run-real` is a run
the hub logged on Oct 30 with partly scripted events, cut to its start and stop, and
`nvidia/samples/run-fake` is the scripted run from `note/fakerun.py`.

With `ANTHROPIC_API_KEY` set, `--answer claude` asks Claude (`claude-haiku-5-5`, or
`BENCH_TASK_MODEL`). With `NVIDIA_API_KEY` set and the Data Designer environment, each
synthetic run also gets a two-sentence practice note from a Nemotron model on build.nvidia.com.
That model sees only the sampled numbers and the protocol, never recorded text.

Setting up the Data Designer environment (about 460 MB, kept apart from the shared `.venv`
because installing it there would downgrade huggingface-hub under the vision part):

```sh
uv venv --python 3.12 nvidia/.venv
uv pip install --python nvidia/.venv/bin/python -r nvidia/requirements.txt
```

## The tasks

| Kind | Question | Answer key | Checker |
| --- | --- | --- | --- |
| `skipped_step` | Which protocol steps were skipped? | the coach's final step statuses | same set of step numbers, or "none" |
| `missing_step_text` | One step's text is blanked; write it back | the protocol text | at least 60% of the step's key words present |
| `tip_reuse` | Was the same tip reused before drawing from tube C? | tip_on and draw events | yes or no |

The solver sees the protocol and only the vision and angle events. Coach steps, warnings and
spoken lines are left out, because they give the answer away or can hold names. The answer key is
what Bench Coach logged, not an independent check of the bench. If vision missed a tip change,
the key is wrong in the same way.

Each task folder follows Harbor's layout: `instruction.md`, `task.toml`, `environment/Dockerfile`
(copies `context/task.json` into `/workspace`), `solution/solve.sh`, and `tests/test.sh`. The test
script runs `tests/verify.py` against `tests/truth.json` and writes `/logs/verifier/reward.txt`.
`verify.py` uses only the standard library, so it runs in a bare `python:3.12-slim` image.

## The synthetic runs

`nvidia/out/synth/` gets:

- `runs/synth-NNNN/events.jsonl`: events in the `docs/EVENTS.md` format, source `synth`
- `runs/synth-NNNN/note.txt`: Nemotron note with a key, otherwise a fixed template
- `rows.jsonl`: the sampled settings behind each run
- `stats.json`: the recorded statistics next to the same statistics measured on the synthetic runs
- `manifest.json`: which engine ran, whether a model was called, the seed

Each run is one sampled row. The row says which steps were skipped (one coin per step, weighted
by that step's recorded skip rate), whether two neighbouring steps swapped, peak tilt, whether
the tilt went over the limit, and seconds per step. Tip reuse is not sampled on its own. It
happens when the tip steps are skipped, as it does at the bench. With few recorded runs a raw
rate is 0 or 1, so `--prior 1` (the default) pulls each step's skip rate toward that protocol's
average, as if there were one more run. Use `--prior 0` for raw rates.

The command prints the engine it used. "local seeded NumPy generator ... Data Designer was not
used" means the fallback ran. The Data Designer samplers are not seeded, so a rerun gives new
rows; the local generator gives the same rows for the same `--seed`.

## NVIDIA tools: what is used and what is not

| Tool | Used here | Key or GPU |
| --- | --- | --- |
| NeMo Data Designer (`data-designer` 0.9.7) | Yes. Its sampler columns (Bernoulli, category, Gaussian) generate the run settings, with no key | Samplers need no key and no GPU. The note column needs `NVIDIA_API_KEY` |
| NIM on build.nvidia.com (Nemotron 3.5 Lightning, `nvidia/nemotron-3.5-lightning-30b-a3b`, Data Designer's `nvidia-text` default) | Only with `NVIDIA_API_KEY`, for the notes. Not tried live: no key on this Mac | Hosted, key needed |
| Harbor task format | Yes, the export layout. The Harbor CLI and a real container run were not tried (Docker was not running) | No |
| NeMo Anonymizer | No. It expects a local GLiNER detector plus an OpenRouter-hosted model. We keep speech out of the data entirely instead | Model endpoints needed |
| NeMo Safe Synthesizer | No. It is a NeMo platform microservice for tabular data, too heavy for a weekend laptop | Microservice deployment |
| BioNeMo Framework | No. It is for biomolecular models, not bench event logs | NVIDIA GPU, compute capability 8.0 or higher |

Data Designer collects telemetry by default. The synth command sets `NEMO_TELEMETRY_ENABLED=false`
and turns off its metrics server, which would otherwise listen on port 9464.

## Sources and licences

Read on 2026-10-30.

- NeMo Data Designer, Apache-2.0: <https://github.com/NVIDIA-NeMo/DataDesigner>, docs <https://docs.nvidia.com/nemo/datadesigner/latest/concepts/deployment-options.md>. Used the library.
- NeMo Anonymizer, Apache-2.0: <https://github.com/NVIDIA-NeMo/Anonymizer>. Read only.
- NeMo Safe Synthesizer, Apache-2.0: <https://github.com/NVIDIA-NeMo/Safe-Synthesizer>, <https://docs.nvidia.com/nemo-platform/latest/documentation/synthesize-safe-data/about.md>. Read only.
- Harbor, Apache-2.0: <https://github.com/laude-institute/harbor>, task layout <https://docs.harborframework.com/core-concepts/tasks/overview.md>. The `task.toml` fields follow its `adapters/aa-lcr/template`. Used the format, no code.
- BioNeMo Framework, Apache-2.0, free NGC account for the container: <https://docs.nvidia.com/bionemo-framework/latest/main/references/FAQ/>. Read only.
- NVIDIA NIM / build.nvidia.com endpoint `https://integrate.api.nvidia.com/v1`, as set in Data Designer's provider list. Hosted under NVIDIA's service terms.
- Data Designer pulls in pandas (BSD-3-Clause), pyarrow (Apache-2.0), duckdb (MIT), scipy (BSD-3-Clause) and others in `nvidia/.venv` only.
- Shared environment: PyYAML (MIT), NumPy (BSD-3-Clause), Anthropic SDK (MIT), pytest (MIT).
