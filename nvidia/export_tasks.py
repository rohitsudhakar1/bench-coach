"""Export a recorded run as Harbor tasks.

    python -m nvidia.export_tasks runs/<run> [--out DIR]

Writes one folder per task in Harbor's layout (https://harborframework.com/docs/tasks):

    <task>/instruction.md          what the solver reads
    <task>/task.toml               config and metadata
    <task>/environment/Dockerfile  copies context/ into /workspace
    <task>/environment/context/task.json   protocol and bench events the solver may use
    <task>/solution/solve.sh       writes the reference answer
    <task>/tests/test.sh           runs verify.py, writes /logs/verifier/reward.txt
    <task>/tests/truth.json        the answer key (never inside environment/)
    <task>/tests/verify.py         deterministic checker, standard library only

The default output folder is runs/<run>/tasks/.
"""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from .runlog import find_runs, load_run
from .tasks import build_tasks

HERE = Path(__file__).resolve().parent

DOCKERFILE = """FROM python:3.12-slim
WORKDIR /workspace
COPY context/ /workspace/context/
CMD ["/bin/bash"]
"""

TEST_SH = """#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
python3 /tests/verify.py /workspace/answer.txt /tests/truth.json /logs/verifier/reward.txt
"""


def _toml_str(s: str) -> str:
    return json.dumps(s)   # a JSON string is a valid TOML basic string


def task_toml(task: dict) -> str:
    return f"""version = "1.0"

[task]
name = {_toml_str("bench-coach/" + task["id"])}
authors = [{{ name = "Bench Coach" }}]

[metadata]
difficulty = "easy"
category = "lab-protocol-audit"
tags = ["bench-coach", {_toml_str(task["kind"])}]
run = {_toml_str(task["run"])}
protocol = {_toml_str(task["protocol_name"])}

[verifier]
timeout_sec = 60.0

[agent]
timeout_sec = 600.0

[environment]
build_timeout_sec = 300.0
cpus = 1
memory_mb = 1024
storage_mb = 2048
"""


def instruction_md(task: dict) -> str:
    proto = "\n".join(f"{s['number']}. {s['text']}   (evidence: {json.dumps(s['expect'])})"
                      for s in task["context"]["protocol"])
    return f"""# {task['protocol_name']}: {task['kind'].replace('_', ' ')}

A person ran this pipetting protocol at a bench. Cameras logged what the pipette did.
The full protocol and event log are in `/workspace/context/task.json`
(times are seconds from the start of the run).

## Protocol

{proto}

## Question

{task['question']}

## Answer

Write only your answer to `/workspace/answer.txt`.
"""


def write_task(task: dict, out: Path) -> Path:
    d = out / task["id"]
    for sub in ("environment/context", "solution", "tests"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "instruction.md").write_text(instruction_md(task))
    (d / "task.toml").write_text(task_toml(task))
    (d / "environment/Dockerfile").write_text(DOCKERFILE)
    visible = {k: task[k] for k in ("id", "kind", "question", "run", "protocol_name")}
    visible["context"] = task["context"]
    (d / "environment/context/task.json").write_text(json.dumps(visible, indent=1))
    (d / "solution/solve.sh").write_text(
        "#!/bin/bash\nset -e\nmkdir -p /workspace\n"
        f"printf '%s\\n' {shlex.quote(task['answer'])} > /workspace/answer.txt\n")
    (d / "tests/test.sh").write_text(TEST_SH)
    (d / "tests/truth.json").write_text(json.dumps(task["truth"], indent=1))
    (d / "tests/verify.py").write_text((HERE / "verify.py").read_text())
    for f in (d / "solution/solve.sh", d / "tests/test.sh"):
        f.chmod(0o755)
    return d


def export_run(run_folder: str | Path, out: str | Path | None = None) -> list[Path]:
    run_folder = Path(run_folder)
    facts = load_run(run_folder)
    out = Path(out) if out else run_folder / "tasks"
    out.mkdir(parents=True, exist_ok=True)
    return [write_task(t, out) for t in build_tasks(facts)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("runs", nargs="+", help="run folder(s), or a folder of run folders")
    ap.add_argument("--out", help="output folder (default: <run>/tasks; with several runs, one subfolder each)")
    a = ap.parse_args(argv)
    folders = find_runs(a.runs)
    if not folders:
        ap.error("no run folder with an events.jsonl found")
    total = 0
    for f in folders:
        out = (Path(a.out) / f.name) if (a.out and len(folders) > 1) else a.out
        try:
            dirs = export_run(f, out)
        except (ValueError, FileNotFoundError) as e:
            print(f"skip {f}: {e}")
            continue
        total += len(dirs)
        print(f"{f.name}: {len(dirs)} tasks in {dirs[0].parent if dirs else out}")
    print(f"{total} tasks written")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
