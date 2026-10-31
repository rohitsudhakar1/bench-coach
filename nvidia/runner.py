"""Score an answer function on exported tasks, without Docker.

    python -m nvidia.runner runs/<run>/tasks [--answer rules|reference|blank|claude]

An answer function takes the task as the solver sees it (environment/context/task.json) and
returns answer text. The score comes from the same verify.py the Harbor test runs.

  rules      a plain solver that reads only the protocol and events (no answer key)
  reference  the solution/solve.sh answer, as a check that every task can be passed
  blank      always answers nothing, the floor
  claude     asks Claude; needs ANTHROPIC_API_KEY (model from BENCH_TASK_MODEL, default claude-haiku-5-5)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .verify import check

AnswerFn = Callable[[dict], str]


def _matches(ev: dict, expect: list[dict]) -> bool:
    for x in expect:
        if x.get("kind") == ev.get("kind") and (not x.get("object") or x["object"] == ev.get("object")):
            return True
    return False


def rules_answer(task: dict) -> str:
    proto, events = task["context"]["protocol"], task["context"]["events"]
    if task["kind"] == "skipped_step":
        pos, skipped = 0, []
        for ev in events:
            for j in range(pos, len(proto)):
                if _matches(ev, proto[j]["expect"]):
                    skipped += [s["number"] for s in proto[pos:j]]
                    pos = j + 1
                    break
        return ", ".join(map(str, skipped)) or "none"
    if task["kind"] == "tip_reuse":
        draws = [i for i, e in enumerate(events) if e["kind"] == "draw"]
        if len(draws) < 2:
            return "no"
        between = events[draws[-2] + 1:draws[-1]]
        return "no" if any(e["kind"] == "tip_on" for e in between) else "yes"
    if task["kind"] == "missing_step_text":
        gap = next(s for s in proto if s["text"] == "[missing]")
        for s in proto:
            if s is not gap and s["expect"] == gap["expect"]:
                return s["text"]
        words = {"tip_on": "Put a fresh tip on the pipette", "tip_off": "Eject the tip",
                 "draw": "Draw from", "dispense": "Dispense into"}
        parts = [f"{words.get(x.get('kind'), x.get('kind'))} {x.get('object') or ''}".strip()
                 for x in gap["expect"]]
        return ". ".join(parts) + "." if parts else "unknown"
    return ""


def blank_answer(task: dict) -> str:
    return ""


def claude_answer(task: dict) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    import anthropic
    client = anthropic.Anthropic()
    prompt = (f"{task['question']}\n\nProtocol and bench events (JSON):\n"
              f"{json.dumps(task['context'])}\n\nReply with only the answer, no explanation.")
    msg = client.messages.create(model=os.environ.get("BENCH_TASK_MODEL", "claude-haiku-5-5"),
                                 max_tokens=120, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def _reference_answer(task_dir: Path) -> str:
    solve = (task_dir / "solution/solve.sh").read_text()
    m = re.search(r"printf '%s\\n' (.+) > /workspace/answer.txt", solve)
    return shlex.split(m.group(1))[0] if m else ""


def load_tasks(root: str | Path) -> list[Path]:
    root = Path(root)
    return sorted(p.parent for p in root.rglob("task.toml"))


def score(root: str | Path, answer: str | AnswerFn = "rules") -> dict:
    fns = {"rules": rules_answer, "blank": blank_answer, "claude": claude_answer}
    results, by_kind = [], defaultdict(list)
    for d in load_tasks(root):
        task = json.loads((d / "environment/context/task.json").read_text())
        truth = json.loads((d / "tests/truth.json").read_text())
        if answer == "reference":
            text = _reference_answer(d)
        else:
            fn = fns[answer] if isinstance(answer, str) else answer
            try:
                text = fn(task)
            except Exception as e:   # one failed call should not stop the whole score
                text = ""
                print(f"{d.name}: answer failed ({type(e).__name__})")
        reward, detail = check(truth["kind"], text, truth)
        results.append({"task": d.name, "kind": truth["kind"], "reward": reward, "answer": text, **detail})
        by_kind[truth["kind"]].append(reward)
    summary = {k: {"tasks": len(v), "mean": round(sum(v) / len(v), 3)} for k, v in sorted(by_kind.items())}
    total = [r["reward"] for r in results]
    return {"tasks": len(results), "mean": round(sum(total) / len(total), 3) if total else None,
            "by_kind": summary, "results": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score an answer function on exported tasks.")
    ap.add_argument("tasks", help="folder holding task folders (searched recursively)")
    ap.add_argument("--answer", default="rules", choices=["rules", "reference", "blank", "claude"])
    ap.add_argument("--json", action="store_true", help="print every result as JSON")
    a = ap.parse_args(argv)
    if a.answer == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; use --answer rules for the offline solver.")
        return 2
    out = score(a.tasks, a.answer)
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        for r in out["results"]:
            print(f"{r['reward']:.0f}  {r['kind']:<18} {r['task']}")
        print(f"{a.answer}: mean {out['mean']} over {out['tasks']} tasks  "
              + "  ".join(f"{k} {v['mean']}" for k, v in out["by_kind"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
