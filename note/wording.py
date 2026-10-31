"""Optional wording pass with Claude. It may only reword lines the rules already wrote. Every
rewrite is checked and thrown away if it brings in a number or a name that was not there."""
from __future__ import annotations

import json
import os
import re
import sys

MODEL = os.environ.get("BENCH_NOTE_MODEL", "claude-opus-5-5")
NUM = re.compile(r"\d+(?:\.\d+)?")
CAP = re.compile(r"\b[A-Z][a-zA-Z]*\b")
WORD = re.compile(r"[A-Za-z]+")
# Sentence-start words a rewrite may add without adding a fact.
ALLOWED = {"Step", "Steps", "The", "An", "Pipette", "Coach", "Same", "Fresh", "No", "Nothing",
           "Drew", "Tip", "Run", "It", "This", "During", "Over", "Highest", "Angle", "Tube", "There", "One"}

PROMPT = """You tidy the wording of a lab note that a program wrote from a recorded bench run.
Rewrite each line so it reads like a careful scientist's notebook: plain, short, past tense.
Rules: keep every fact exactly. Do not add, drop or change any number, unit, tube name, step
number or person. Do not add causes or advice. If a line already reads well, return it unchanged.
Reply with only a JSON object mapping each id to its line.

Lines:
"""


def safe_rewrite(original: str, new, allowed: set[str] = ALLOWED) -> bool:
    if not isinstance(new, str) or not new.strip() or len(new) > 2 * len(original) + 40:
        return False
    if sorted(NUM.findall(new)) != sorted(NUM.findall(original)):
        return False
    words = set(WORD.findall(original))
    lower = {w.lower() for w in words if len(w) > 1}
    for w in CAP.findall(new):
        if w in words or w in allowed:
            continue
        if len(w) == 1 or w.lower() not in lower:  # single letters are tube names: exact match only
            return False
    return True


def lines_of(note: dict) -> dict[str, str]:
    # Quotes of the person and the protocol's own step text are never sent.
    lines = {"summary": note["summary"]}
    for key in ("technique", "problems"):
        for i, item in enumerate(note[key]):
            if item.get("kind") != "warning":
                lines[f"{key}.{i}"] = item["text"]
    return lines


def call_claude(lines: dict[str, str]) -> dict | None:
    import anthropic
    client = anthropic.Anthropic(timeout=40.0, max_retries=1)
    resp = client.messages.create(model=MODEL, max_tokens=4000,
                                  messages=[{"role": "user", "content": PROMPT + json.dumps(lines, indent=1)}])
    if resp.stop_reason == "refusal":
        return None
    text = "".join(getattr(b, "text", "") for b in resp.content)
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def polish(note: dict, call=None) -> dict:
    """Reword in place when ANTHROPIC_API_KEY is set (or `call` is given, for tests)."""
    if call is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return note
        call = call_claude
    lines = lines_of(note)
    try:
        out = call(lines) or {}
    except Exception as exc:  # network, key, bad JSON: the rule-based note stands
        print(f"note: wording pass skipped ({type(exc).__name__})", file=sys.stderr)
        return note
    kept = 0
    for key, original in lines.items():
        new = out.get(key) if isinstance(out, dict) else None
        if not safe_rewrite(original, new):
            continue
        if key == "summary":
            note["summary"] = new.strip()
        else:
            group, idx = key.split(".")
            note[group][int(idx)]["text"] = new.strip()
        kept += 1
    if kept:
        note["wording"] = f"reworded by Claude ({MODEL}) and checked against the rule-based lines"
    return note
