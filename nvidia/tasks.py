"""Turn a recorded run into question tasks with a checkable answer.

Three kinds, all using 1-based step numbers as a person would say them:
  skipped_step       which protocol steps were skipped
  missing_step_text  one step's text is blanked out; write it back
  tip_reuse          was the same tip reused before drawing from a given tube

The answer key comes from the run's own record: the coach's final step statuses and the vision
tip and draw events. It is what the system logged, not an independent check of the bench.
"""
from __future__ import annotations

import re
import zlib

from .runlog import RunFacts


def _protocol_view(facts: RunFacts, hide: int | None = None) -> list[dict]:
    out = []
    for i, s in enumerate(facts.steps):
        out.append({"number": i + 1, "text": "[missing]" if i == hide else s["text"],
                    "expect": s["expect"]})
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "x"


def build_tasks(facts: RunFacts) -> list[dict]:
    events = facts.visible_events()
    base = {"run": facts.run_id, "protocol_name": facts.protocol_name}
    tasks = []

    skipped = [i + 1 for i in facts.skipped]
    tasks.append({
        **base, "id": f"{_slug(facts.run_id)}-skipped-step", "kind": "skipped_step",
        "question": ("Compare the protocol with the bench events. Which protocol steps were "
                     "skipped? Answer with the step numbers separated by commas, or 'none'."),
        "context": {"protocol": _protocol_view(facts), "events": events},
        "truth": {"kind": "skipped_step", "skipped_steps": skipped},
        "answer": ", ".join(map(str, skipped)) or "none",
    })

    hide_list = facts.skipped or [zlib.crc32(facts.run_id.encode()) % max(len(facts.steps), 1)]
    for i in hide_list:
        if i >= len(facts.steps):
            continue
        text = facts.steps[i]["text"]
        tasks.append({
            **base, "id": f"{_slug(facts.run_id)}-missing-step-{i + 1}", "kind": "missing_step_text",
            "question": (f"Step {i + 1} of the protocol is missing its text. Using the other steps, "
                         "its expected evidence and the bench events, write the missing step as "
                         "one short instruction."),
            "context": {"protocol": _protocol_view(facts, hide=i), "events": events},
            "truth": {"kind": "missing_step_text", "step": i + 1, "text": text},
            "answer": text,
        })

    for n, d in enumerate(facts.draws()):
        if d["reused"] is None or not d["object"] or d["object"] == d["previous_object"]:
            continue
        cut = d["t"] - facts.start
        tasks.append({
            **base, "id": f"{_slug(facts.run_id)}-tip-reuse-{_slug(d['object'])}-{n}", "kind": "tip_reuse",
            "question": (f"At {cut:.1f} s the pipette drew from {d['object']}. Was the same tip "
                         f"reused from an earlier draw, with no fresh tip put on since then? "
                         "Answer yes or no."),
            # only the events up to that draw, so later tip changes do not confuse the question
            "context": {"protocol": _protocol_view(facts), "events": [e for e in events if e["t"] <= cut + 0.01]},
            "truth": {"kind": "tip_reuse", "object": d["object"], "reused": bool(d["reused"])},
            "answer": "yes" if d["reused"] else "no",
        })
    return tasks
