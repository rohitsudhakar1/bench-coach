"""Make sense of what the person said, with plain word rules. No model or key needed.

Speech comes from Whisper, so the rules expect its usual mishearings: "tube bee", "tube see",
"tips on", "added it to be". Questions, plans and negations never count as a finished action.
"""
from __future__ import annotations

import re

# How Whisper tends to write single tube letters.
LETTERS = {
    "a": "a", "ay": "a", "eh": "a", "hey": "a",
    "b": "b", "be": "b", "bee": "b", "bea": "b",
    "c": "c", "see": "c", "sea": "c", "cee": "c", "si": "c",
    "d": "d", "dee": "d",
    "e": "e",
}
_LETTER_WORDS = "|".join(sorted(LETTERS, key=len, reverse=True))
VESSELS = "tube|plate|well|vial|bottle|flask"


def clean(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    return " ".join(re.sub(r"[^\w' ]", " ", text).split())


def _strip_fillers(t: str) -> str:
    return re.sub(r"^(?:(?:um|uh|oh|okay|ok|so|alright|right|and|well)\s+)+", "", t)


def objects_in(text: str) -> set[str]:
    """Vessels named in a sentence, with letters fixed: 'tube see' -> {'tube c'}."""
    t = clean(text)
    found = {f"{v} {LETTERS[l]}" for v, l in re.findall(rf"\b({VESSELS})s?\s+({_LETTER_WORDS})\b", t)}
    if not found:
        for l in re.findall(rf"\b(?:to|into|in|from|two)\s+({_LETTER_WORDS})\b(?!\s+\w+ing)", t):
            # "added it to b", "drew from a"; "to be" only when nothing else names a vessel
            found.add(f"tube {LETTERS[l]}")
    return found


# ---------- yes / no / done ----------

_YES = re.compile(r"^(?:yes|yeah|yep|yup|yea|ya|correct|sure|i did|uh huh|mm hmm|that's right|affirmative|i have)\b")
_NO = re.compile(r"^(?:no|nope|nah|not yet|negative|wrong|i didn't|i did not|didn't|i haven't)\b")
_DONE = re.compile(r"^(?:i'?m |i am |it'?s |that'?s |step )?(?:done|finished|next|did it|got it|complete|step done)"
                   r"(?: now| with that| with it)?$")


def is_yes(text: str) -> bool:
    return bool(_YES.match(_strip_fillers(clean(text))))


def is_no(text: str) -> bool:
    return bool(_NO.match(_strip_fillers(clean(text))))


def is_done(text: str) -> bool:
    return bool(_DONE.match(_strip_fillers(clean(text))))


# ---------- questions ----------

_Q = [
    ("repeat", re.compile(r"\brepeat\b|\bsay (?:that|it) again\b|\bwhat did you say\b|\bcome again\b|\bpardon\b")),
    ("how_much", re.compile(r"\bhow much\b|\bwhat volume\b|\bhow many (?:micro|mil|ul)")),
    ("did_i", re.compile(r"\b(?:did|have|haven't|didn't) i\b")),
    ("where", re.compile(r"\b(?:which|what) step\b|\bwhere am i\b|\bhow many (?:steps )?(?:are )?left\b")),
    ("next", re.compile(r"\bwhat'?s next\b|\bwhat is next\b|\bnext step\b|\bwhat (?:do|should) i do\b"
                        r"|\bwhat now\b|\bwhat'?s the step\b|\bwhat comes next\b")),
]


def question(text: str) -> str | None:
    t = clean(text)
    for name, rx in _Q:
        if rx.search(t):
            return name
    return None


# ---------- spoken statements of a finished action ----------

_NEG = re.compile(r"\b(?:not|never|didn't|haven't|can't|don't|won't|did i|have i|should|will|going to|"
                  r"gonna|need to|about to|want to|shall|let me|i'll)\b")
_ASK = re.compile(r"^(?:is|are|can|could|did|do|does|have|has|should|would|will|shall|what|which|how|where)\b")
_NUM = {"one": "1", "once": "1", "two": "2", "twice": "2", "three": "3", "four": "4", "five": "5"}


def statement(text: str) -> dict | None:
    """'I drew from tube A' -> {'kind': 'draw', 'object': 'tube a', 'count': None}, else None."""
    t = _strip_fillers(clean(text))
    clause = re.split(r"\b(?:what|which|how|where)\b", t)[0].strip()   # "tip's on, what's next" keeps the first part
    if not clause or _NEG.search(clause) or _ASK.match(clause):
        return None
    kind = None
    if re.search(r"\b(?:ejected|removed|discarded|dropped|took off|popped off|threw)\b.*\btip\b|\btip(?:'s| is|s)? off\b", clause):
        kind = "tip_off"
    elif (re.search(r"\btip(?:'s| is|s)? on\b|\btip zone\b|\b(?:put|fitted|attached|changed|got|grabbed|added)\b.*\b(?:tip|tips)\b", clause)
          or re.fullmatch(r"(?:a )?(?:fresh|new|clean) tip(?: on)?", clause)):
        kind = "tip_on"
    elif re.search(r"\b(?:drew|drawn|aspirated|took up|pulled up|sucked up|picked up)\b", clause):
        kind = "draw"
    elif re.search(r"\b(?:added|dispensed|transferred|put it|emptied|pushed it)\b", clause):
        kind = "dispense"
    elif re.search(r"\bmixed\b", clause):
        kind = "mix"
    if kind is None:
        return None
    names = objects_in(clause) if kind not in ("tip_on", "tip_off") else set()
    if len(names) > 1:
        return None   # "from A into B" names two things; ask for one at a time
    m = re.search(r"\b(\d+|one|two|three|four|five) times\b|\b(once|twice)\b", clause)
    count = _NUM.get((m.group(1) or m.group(2)), m.group(1) or m.group(2)) if m else None
    return {"kind": kind, "object": next(iter(names), None), "count": count}


def claim_fits(step, claim: dict) -> bool:
    """Does a spoken claim describe this step?"""
    def same(a, b):
        return clean(a) == clean(b)

    if claim["kind"] == "mix":
        text = clean(step.text)
        if "mix" not in text:
            return False
        m = re.search(r"\b(\d+|one|two|three|four|five) times\b", text)
        if m and claim["count"] and _NUM.get(m.group(1), m.group(1)) != claim["count"]:
            return False
        return claim["object"] is None or any(same(o, claim["object"]) for o in step.objects())
    for e in step.expect:
        if e.kind != claim["kind"]:
            continue
        if e.object is None or claim["object"] is None or same(e.object, claim["object"]):
            return True
    return False
