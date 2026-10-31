"""Deterministic answer checker for Bench Coach tasks. Standard library only.

This same file is copied into every exported task as tests/verify.py, so it must not import
anything outside the standard library.

    python verify.py <answer.txt> <truth.json> [<reward.txt>]

prints a JSON line with the score and writes the reward (0 to 1) when a reward path is given.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PASS_RECALL = 0.6   # share of the missing step's key words an answer must contain

STOP = {"the", "a", "an", "of", "to", "into", "in", "it", "on", "and", "by", "from", "with",
        "for", "up", "down", "before", "after", "then", "at", "your", "its", "is", "be"}


def _int_set(text: str) -> set[int] | None:
    t = text.strip().lower()
    if not t or t.split()[0].strip(".,:") in {"none", "no", "nothing", "0"} and not re.search(r"[1-9]", t):
        return set()
    nums = re.findall(r"\d+", t)
    return {int(n) for n in nums} if nums else None


def _yes_no(text: str) -> str | None:
    words = re.findall(r"[a-z]+", text.lower())
    for w in words[:3]:
        if w in {"yes", "y", "true", "reused", "same"}:
            return "yes"
        if w in {"no", "n", "false", "fresh", "new", "changed"}:
            return "no"
    return None


def _tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z]+|\d+", text.lower()):
        if w in STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def check(kind: str, answer: str, truth: dict) -> tuple[float, dict]:
    """Score one answer. Returns (reward, details)."""
    answer = answer or ""
    if kind == "skipped_step":
        got = _int_set(answer)
        want = set(truth["skipped_steps"])
        ok = got == want
        return float(ok), {"got": sorted(got) if got is not None else None, "want": sorted(want)}
    if kind == "tip_reuse":
        got = _yes_no(answer)
        want = "yes" if truth["reused"] else "no"
        return float(got == want), {"got": got, "want": want}
    if kind == "missing_step_text":
        want = set(_tokens(truth["text"]))
        got = set(_tokens(answer))
        recall = len(want & got) / len(want) if want else 0.0
        return float(recall >= PASS_RECALL), {"recall": round(recall, 3), "need": PASS_RECALL}
    raise ValueError(f"unknown task kind {kind!r}")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    answer_path, truth_path = Path(argv[1]), Path(argv[2])
    truth = json.loads(truth_path.read_text())
    answer = answer_path.read_text() if answer_path.is_file() else ""
    reward, detail = check(truth["kind"], answer, truth)
    if len(argv) > 3:
        out = Path(argv[3])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{reward:g}")
    print(json.dumps({"kind": truth["kind"], "reward": reward, **detail}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
