"""Short lines the coach says. Every line stays under 12 words."""
from __future__ import annotations

import re

MAX_WORDS = 11

_COMPRESS = [
    (r"\bby pipetting up and down (\w+) times\b", r"\1 times"),
    (r"\bpipetting up and down\b", "pipetting"),
    (r"\bon the pipette\b", ""),
    (r"\bmicroliters?\b|\bmicrolitres?\b", "microlitres"),
]


def short(text: str, limit: int = MAX_WORDS) -> str:
    """Cut a line to `limit` words, dropping the amount first, then stopping at a clause break."""
    for pat, rep in _COMPRESS:
        text = re.sub(pat, rep, text, flags=re.I)
    text = " ".join(text.split()).replace(" .", ".").replace(" ,", ",")
    if len(text.split()) <= limit:
        return text
    slim = re.sub(r"\b\d+(\.\d+)?\s+\w+\s+of\s+\w+\s+", "", text)
    if len(slim.split()) <= limit:
        return slim
    head = " ".join(slim.split()[:limit])
    for brk in (",", ";", " and ", " then "):
        cut = head.rfind(brk)
        if cut > len(head) // 3:
            return head[:cut].rstrip(" ,;") + "."
    return head.rstrip(" ,;.") + "."


def lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def step_line(step, prefix: str = "Next:") -> str:
    return short(f"{prefix} {lower_first(step.say or step.text)}")


def numbers(items: list[int]) -> str:
    """[4, 5, 7] -> '4, 5 and 7'."""
    s = [str(i) for i in items]
    return s[0] if len(s) == 1 else ", ".join(s[:-1]) + " and " + s[-1]


def finish_line(status: list[str]) -> str:
    """The last line of a run. Never praise a run with a skipped or unsure step."""
    check = [i + 1 for i, s in enumerate(status) if s in ("skipped", "unsure")]
    if not check:
        return "All steps done. Nice work."
    return f"Run finished. Check step{'s' if len(check) > 1 else ''} {numbers(check)}."
