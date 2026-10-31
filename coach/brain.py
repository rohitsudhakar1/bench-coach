"""The coach's state machine. It does no input or output itself.

`handle(event)` and `hear(text)` return a list of `Out` items: events to send to the hub and
lines to say. That keeps every rule testable with scripted events and transcripts.

Rules:
- An event that matches the current step marks it done, or unsure when the confidence is weak
  or vision only said "tube" for a step that names a tube. Either way the coach moves on.
- Strong evidence for a step up to `lookahead` ahead marks the ones in between skipped, and warns.
- A repeat of the step just finished (a second tip, dispensing again to mix) is not a skip; it
  upgrades that step to done if it was unsure.
- Spoken confirmations ("tip's on", "added it to B", "done") finish the current step with
  evidence "said". A recent camera event that disagrees gets a gentle question instead.
- Hints (`detail.hint: true`) are never evidence. With hints on, a fitting hint makes the coach
  ask "Did you just draw from tube A?"; a yes finishes the step as seen+said.
- Warnings: the same tip used for two tubes, drawing with no tip, a skipped step, the pipette
  tilted past the step's limit, a step done too fast. Each topic has a cooldown.
- The last line never praises a run with a skipped or unsure step; it says which to check.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from dataclasses import dataclass, field

from . import heard, phrases
from . import protocol as protocol_mod
from .protocol import Protocol, Step

ACTIONS = {"tip_on", "tip_off", "draw", "dispense", "hand_touch"}
PRI_WARNING, PRI_ANSWER, PRI_STEP = 3, 2, 1
SOURCE_LABEL = {"vision": "seen", "speech": "said", "hint+speech": "seen+said"}


def is_hint(ev: dict) -> bool:
    return (ev.get("detail") or {}).get("hint") is True


def pretty(obj: str) -> str:
    """'tube c' -> 'tube C'."""
    w = obj.split()
    return " ".join(w[:-1] + [w[-1].upper()]) if len(w) > 1 and len(w[-1]) == 1 else obj


def named(obj) -> bool:
    """'tube C' names one vessel. Vision's bare 'tube' or 'bottle' does not."""
    return len(heard.clean(obj or "").split()) > 1


@dataclass
class Out:
    kind: str                  # "emit" (send an event) or "say" (speak a line)
    event: dict | None = None
    text: str = ""
    priority: int = PRI_STEP
    topic: str = ""


@dataclass
class Tip:
    on: bool | None = None     # None: not known yet
    drawn: set = field(default_factory=set)


class Brain:
    hint_gap = 8.0          # never two hint questions closer than this
    hint_wait = 10.0        # an unanswered question closes after this
    hint_retry = 15.0       # after a no or a timeout, wait this long before asking again
    hint_limit = 3          # questions per step
    reminder_after = 45.0   # hint mode: "say done" reminder after this long on one step

    def __init__(self, protocol: Protocol, strong: float = 0.6, lookahead: int = 3,
                 cooldown: float = 8.0, answerer=None, hints: bool = False, clock=time.time):
        self.protocol = protocol
        self.strong = strong
        self.lookahead = lookahead
        self.cooldown = cooldown
        self.answerer = answerer   # optional callable(brain, question, rule_answer) -> str
        self.hints = hints
        self.clock = clock
        self.reset()

    # ---------- state ----------

    def reset(self):
        n = len(self.protocol.steps)
        self.status = ["pending"] * n
        self.evidence: list[str | None] = [None] * n
        self.started_at: list[float | None] = [None] * n
        self.current = 0
        self.log: list[dict] = []
        self.tip = Tip()
        self.warned: dict[str, float] = {}
        self.last_said = ""
        self.now = self.clock()
        self.question: dict | None = None   # {"step", "t", "text", "kind"}
        self.asked: dict[int, int] = {}
        self.closed_at: dict[int, float] = {}
        self.reminded: set[int] = set()
        self.last_asked = float("-inf")
        if n:
            self.status[0] = "current"

    def finished(self) -> bool:
        return self.current >= len(self.protocol.steps)

    def step(self) -> Step | None:
        return None if self.finished() else self.protocol.steps[self.current]

    def state(self) -> dict:
        return {
            "protocol": self.protocol.name,
            "current": self.current,
            "finished": self.finished(),
            "steps": [{"index": s.index, "text": s.text, "status": self.status[s.index],
                       "evidence": self.evidence[s.index],
                       "source": SOURCE_LABEL.get(self.evidence[s.index])}
                      for s in self.protocol.steps],
            "tip": {"on": self.tip.on, "drawn": sorted(self.tip.drawn)},
            "question": self.question["text"] if self.question else None,
            "warnings": [{"topic": k, "t": v} for k, v in self.warned.items()],
        }

    def said(self, text: str):
        """The runner calls this once a line has really gone out."""
        self.last_said = text

    def _remember(self, ev: dict):
        self.log.append(ev)
        del self.log[:-500]

    # ---------- building output ----------

    def _event(self, kind, detail, step=None, obj=None, confidence=None) -> Out:
        return Out("emit", event={"t": self.now, "source": "coach", "kind": kind, "object": obj,
                                  "hand": None, "step": step, "confidence": confidence,
                                  "detail": detail})

    def _step_event(self, i: int, status: str, confidence=None) -> Out:
        detail = {"index": i, "status": status, "text": self.protocol.steps[i].text}
        if self.evidence[i]:
            detail["evidence"] = self.evidence[i]
            detail["source"] = SOURCE_LABEL[self.evidence[i]]
        if self.hints:
            q = self.question
            detail["question"] = q["text"] if q and q["step"] == i and status == "current" else None
        return self._event("step", detail, step=i, confidence=confidence)

    def _say(self, text, priority=PRI_STEP, topic="") -> Out:
        return Out("say", text=text, priority=priority, topic=topic)

    def _answer(self, text) -> Out:
        return self._say(phrases.short(text), PRI_ANSWER, "answer")

    def _warn(self, text, topic, step=None, post=True) -> list[Out]:
        last = self.warned.get(topic)
        if last is not None and self.now - last < self.cooldown:
            return []
        self.warned[topic] = self.now
        outs = [self._event("warning", {"text": text}, step=step)] if post else []
        return outs + [self._say(text, PRI_WARNING, topic)]

    def _strong(self, ev) -> bool:
        c = ev.get("confidence")
        return c is None or c >= self.strong

    # ---------- matching ----------

    def _match(self, step: Step, ev: dict) -> str | None:
        """'exact' when the event fits the step, 'generic' when vision only said 'tube'."""
        best = None
        obj = ev.get("object")
        for e in step.expect:
            if e.kind != ev.get("kind"):
                continue
            if e.object is None or heard.clean(obj) == heard.clean(e.object):
                return "exact"
            if obj is None or not named(obj):
                best = "generic"
        return best

    # ---------- entry points ----------

    def start(self) -> list[Out]:
        if not self.protocol.steps:
            return []
        self.started_at[0] = self.now
        return [self._step_event(0, "current"),
                self._say(phrases.step_line(self.protocol.steps[0], "First:"), PRI_STEP, "step")]

    def handle(self, ev: dict) -> list[Out]:
        if ev.get("source") == "coach":
            return []   # our own events coming back from the hub
        self.now = float(ev.get("t") or self.clock())
        outs = self.tick(self.now)
        kind = ev.get("kind")
        self._remember(ev)
        if kind == "run":
            return outs + self._on_run(ev)
        if kind == "speech":
            return outs + self.hear((ev.get("detail") or {}).get("text", ""), emit=False)
        if kind == "angle":
            return outs + self._on_angle(ev)
        if kind == "warning":
            text = (ev.get("detail") or {}).get("text", "")
            if not text:
                return outs
            topic = "angle" if ev.get("source") == "angle" else f"ext:{ev.get('source')}:{text}"
            return outs + self._warn(text, topic, ev.get("step"), post=False)  # already on the hub
        if kind in ACTIONS and is_hint(ev):
            return outs + self._on_hint(ev)
        if kind in ACTIONS:
            tip = self._check_tip(ev.get("kind"), ev.get("object"), self._strong(ev))
            return outs + tip + self._advance(ev, tip_warned=bool(tip))
        return outs

    def hear(self, text: str, t: float | None = None, emit: bool = True) -> list[Out]:
        """Something the person said, from the mic or a replayed speech event."""
        text = " ".join((text or "").split())
        if not text:
            return []
        if t is not None:
            self.now = t
        outs = self.tick(self.now)
        if emit:
            ev = self._event("speech", {"text": text},
                             step=None if self.finished() else self.current).event
            self._remember(ev)
            outs.append(Out("emit", event=ev))

        if self.question is not None and not self.finished():
            reply = self._reply_to_question(text)
            if reply is not None:
                return outs + reply

        if heard.is_done(text) and not self.finished():
            return outs + self._finish_current(1.0, "speech")

        claim = heard.statement(text)
        if claim:
            return outs + self._on_claim(claim)

        rule = self.rule_answer(text)
        answer = rule
        if self.answerer is not None:
            try:
                answer = self.answerer(self, text, rule) or rule
            except Exception:
                answer = rule
        return outs + [self._answer(answer or "Sorry, I can only help with this protocol.")]

    def tick(self, now: float) -> list[Out]:
        """Close an unanswered question and give the 'say done' reminder, even in silence."""
        self.now = max(self.now, now)
        outs = []
        q = self.question
        if q is not None and self.now - q["t"] >= self.hint_wait:
            self.question = None
            self.closed_at[q["step"]] = q["t"] + self.hint_wait
            if q["step"] == self.current and not self.finished():
                outs.append(self._step_event(self.current, "current"))
        if self.hints and not self.finished():
            i = self.current
            st = self.started_at[i]
            if st is not None and self.now - st >= self.reminder_after and i not in self.reminded:
                self.reminded.add(i)
                outs.append(self._say(f"Say done when step {i + 1} is finished.", PRI_ANSWER, "reminder"))
        return outs

    # ---------- spoken answers ----------

    def rule_answer(self, text: str) -> str:
        steps, cur = self.protocol.steps, self.current
        kind = heard.question(text)
        if kind == "repeat":
            return self.last_said or "I haven't said anything yet."
        if kind == "how_much":
            for s in steps[cur:cur + 3]:
                m = re.search(r"\d+(?:\.\d+)?\s*(?:microlit\w*|µl|ul|millilit\w*|ml)\b.*",
                              s.text, re.I)
                if m:
                    return phrases.short(m.group(0).rstrip(".")) + "."
            return "This step has no volume."
        if kind == "did_i":
            t = heard.clean(text)
            if "tip" in t:
                if self.tip.on and not self.tip.drawn:
                    return "Yes, your tip is fresh."
                return "No. Put on a fresh tip." if self.tip.on is not None else "I haven't seen a tip yet."
            objs = heard.objects_in(text) & {heard.clean(o) for o in self.protocol.objects()}
            if objs:
                return self._did_i(next(iter(objs)))
        if kind == "where":
            if self.finished():
                return "All steps are finished."
            return f"Step {cur + 1} of {len(steps)}, {len(steps) - cur} left."
        if kind == "next":
            return "All steps are finished." if self.finished() else phrases.step_line(steps[cur], "Now:")
        return ""

    def _did_i(self, obj: str) -> str:
        name = next((o for o in self.protocol.objects() if heard.clean(o) == obj), obj)
        verbs = {"draw": "drew from", "dispense": "added to", "hand_touch": "touched"}
        for ev in reversed(self.log):
            if (ev.get("kind") in verbs and not is_hint(ev) and self._strong(ev)
                    and heard.clean(ev.get("object")) == obj):
                return f"Yes, you {verbs[ev['kind']]} {name}."
        for s in self.protocol.steps:
            if self.status[s.index] in ("done", "unsure") and any(heard.clean(o) == obj for o in s.objects()):
                how = "you said so" if self.evidence[s.index] != "vision" else "camera saw it"
                return f"Step {s.index + 1} is ticked, {how}."
        return f"No, nothing with {name} yet."

    def _on_claim(self, claim: dict) -> list[Out]:
        step = self.step()
        # A recent camera event of the same action naming a different vessel: ask, don't tick.
        lo = self.started_at[max(0, self.current - 1)] if self.protocol.steps else None
        if claim["object"] and lo is not None:
            for ev in reversed(self.log):
                if ev.get("t", 0) < lo:
                    break
                if (ev.get("source") == "vision" and ev.get("kind") == claim["kind"]
                        and not is_hint(ev) and self._strong(ev) and named(ev.get("object"))):
                    if heard.clean(ev["object"]) != claim["object"]:
                        said = pretty(claim["object"])
                        return [self._answer(f"The camera saw {ev['object']}, you said {said}. Which one?")]
                    break
        if step is not None and heard.claim_fits(step, claim):
            return self._finish_current(1.0, "speech")
        prev = self.current - 1
        if prev >= 0 and heard.claim_fits(self.protocol.steps[prev], claim):
            outs = []
            if self.status[prev] == "unsure":
                self.status[prev] = "done"
                self.evidence[prev] = "speech"
                outs.append(self._step_event(prev, "done"))
            line = "All steps are finished." if step is None else phrases.step_line(step)
            return outs + [self._answer(line)]
        if step is None:
            return [self._answer("All steps are finished.")]
        return [self._answer(f"That doesn't match step {self.current + 1}. Please check.")]

    # ---------- hint questions ----------

    def _hint_fits(self, step: Step, ev: dict) -> bool:
        obj = ev.get("object")
        for e in step.expect:
            if e.kind != ev.get("kind"):
                continue
            if e.object and named(obj) and heard.clean(obj) != heard.clean(e.object):
                continue   # names a different tube
            return True
        return False

    def _question_text(self, step: Step, kind: str) -> str:
        obj = next(iter(step.objects(kind)), None)
        if kind == "draw":
            return f"Did you just draw from {obj}?" if obj else "Did you just draw?"
        if kind == "dispense":
            return f"Did you just dispense into {obj}?" if obj else "Did you just dispense?"
        if kind == "tip_on":
            return "Did you just put on a tip?"
        if kind == "tip_off":
            return "Did you just eject the tip?"
        return f"Did you just touch {obj}?" if obj else "Did you just do this step?"

    def _on_hint(self, ev: dict) -> list[Out]:
        if not self.hints or self.finished() or self.question is not None:
            return []
        i = self.current
        if self.asked.get(i, 0) >= self.hint_limit or self.now - self.last_asked < self.hint_gap:
            return []
        closed = self.closed_at.get(i)
        if closed is not None and self.now - closed < self.hint_retry:
            return []
        step = self.protocol.steps[i]
        if not self._hint_fits(step, ev):
            return []
        text = self._question_text(step, ev.get("kind"))
        self.question = {"step": i, "t": self.now, "text": text, "kind": ev.get("kind")}
        self.asked[i] = self.asked.get(i, 0) + 1
        self.last_asked = self.now
        return [self._step_event(i, "current"), self._say(text, PRI_ANSWER, "hint")]

    def _reply_to_question(self, text: str) -> list[Out] | None:
        if heard.is_no(text):
            self.closed_at[self.current] = self.now
            self.question = None
            return [self._step_event(self.current, "current"),
                    self._answer(f"Okay. Still on step {self.current + 1}.")]
        claim = heard.statement(text)
        if heard.is_yes(text) or heard.is_done(text) or (claim and heard.claim_fits(self.step(), claim)):
            return self._finish_current(1.0, "hint+speech")
        return None   # about something else; the question stays open

    # ---------- events ----------

    def _on_run(self, ev: dict) -> list[Out]:
        d = ev.get("detail") or {}
        if d.get("status") != "start":
            return []
        name = d.get("protocol")
        if name and Path(name).name != Path(self.protocol.path).name:
            try:
                self.protocol = protocol_mod.load(name)
            except (FileNotFoundError, OSError, ValueError, KeyError):
                pass   # keep the one we have
        now = self.now
        self.reset()
        self.now = now
        return self.start()

    def _on_angle(self, ev: dict) -> list[Out]:
        d = ev.get("detail") or {}
        deg = d.get("deg")
        if deg is None:
            return []
        step = self.step()
        limit = (step.angle_max_deg if step else None) or d.get("limit")
        if not (d.get("ok") is False or (limit is not None and deg > limit)):
            return []
        keep = f" Keep it under {int(limit)}." if limit else ""
        return self._warn(f"Pipette looks tilted about {int(round(deg))} degrees.{keep}", "angle",
                          step=None if step is None else self.current)

    def _check_tip(self, kind, obj, strong: bool) -> list[Out]:
        outs = []
        if not strong:
            return outs
        if kind == "tip_on":
            self.tip = Tip(on=True)
        elif kind == "tip_off":
            self.tip = Tip(on=False)
        elif kind == "draw" and named(obj):
            o = heard.clean(obj)
            if self.tip.on is False:
                outs += self._warn("Put on a fresh tip before drawing.", "tip")
            elif any(d != o for d in self.tip.drawn):
                outs += self._warn(phrases.short(f"Same tip used twice. Change it before {obj}."), "tip")
            self.tip.drawn.add(o)
        return outs

    def _advance(self, ev: dict, tip_warned=False) -> list[Out]:
        steps, n = self.protocol.steps, len(self.protocol.steps)
        strong = self._strong(ev)
        conf = ev.get("confidence")
        if self.current < n:
            m = self._match(steps[self.current], ev)
            if m:
                return self._finish_current(conf if (strong and m == "exact") else 0.0, "vision", conf)
        prev = self.current - 1
        if prev >= 0 and self._match(steps[prev], ev) == "exact":
            if strong and self.status[prev] == "unsure":
                self.status[prev] = "done"
                self.evidence[prev] = "vision"
                return [self._step_event(prev, "done", conf)]
            return []   # a repeat (second tip, mixing), not a skip
        if not strong:
            return []
        for j in range(self.current + 1, min(n, self.current + 1 + self.lookahead)):
            if self._match(steps[j], ev) == "exact":
                return self._skip_to(j, ev, tip_warned)
        for j in range(self.current - 2, -1, -1):
            if self.status[j] == "unsure" and self._match(steps[j], ev) == "exact":
                self.status[j] = "done"
                self.evidence[j] = "vision"
                return [self._step_event(j, "done", conf)]
        return []

    def _finish_current(self, strength, evidence: str, confidence=None) -> list[Out]:
        i = self.current
        step = self.protocol.steps[i]
        status = "done" if (strength is None or strength >= self.strong) else "unsure"
        self.status[i] = status
        self.evidence[i] = evidence
        if self.question and self.question["step"] == i:
            self.question = None
        outs = [self._step_event(i, status, confidence)]
        if evidence != "vision":
            # what they said happened counts for the tip rules too
            for e in step.expect:
                outs += self._check_tip(e.kind, e.object, True)
        st = self.started_at[i]
        if status == "done" and step.min_seconds and st is not None and self.now - st < step.min_seconds:
            outs += self._warn(f"That was quick. Check step {i + 1}.", f"rush:{i}", i)
        return outs + self._move_to(i + 1)

    def _skip_to(self, j: int, ev: dict, tip_warned: bool) -> list[Out]:
        skipped = list(range(self.current, j))
        self.question = None
        outs = []
        for k in skipped:
            self.status[k] = "skipped"
            outs.append(self._step_event(k, "skipped"))
        if len(skipped) == 1:
            k = skipped[0]
            text = phrases.short(f"You skipped step {k + 1}: {phrases.lower_first(self.protocol.steps[k].text)}")
        else:
            text = f"You skipped steps {phrases.numbers([k + 1 for k in skipped])}."
        warn = self._warn(text, f"skip:{skipped[0]}", skipped[0])
        tip_only = all(e.kind in ("tip_on", "tip_off") for k in skipped for e in self.protocol.steps[k].expect)
        if tip_warned and tip_only:
            warn = [o for o in warn if o.kind == "emit"]   # the tip warning already said it
        self.current = j
        conf = ev.get("confidence")
        return outs + warn + self._finish_current(conf, "vision", conf)

    def _move_to(self, i: int) -> list[Out]:
        self.current = i
        if i >= len(self.protocol.steps):
            return [self._say(phrases.finish_line(self.status), PRI_STEP, "step")]
        self.status[i] = "current"
        self.started_at[i] = self.now
        return [self._step_event(i, "current"),
                self._say(phrases.step_line(self.protocol.steps[i]), PRI_STEP, "step")]
