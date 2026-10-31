"""Build the lab note (a plain dict) from a loaded run. Rules only, no network."""
from __future__ import annotations

import re
from datetime import datetime

from .runfolder import Run

VOLUME = re.compile(r"\b\d+(?:\.\d+)?\s*(?:µl|μl|ul|microlit(?:re|er)s?|micro\s?lit(?:re|er)s?|ml|millilit(?:re|er)s?|nl|l)\b", re.I)
AMOUNT = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|µg|ug|ng|g|mm|µm|um|nm|mol|molar|%|percent|x|rpm|minutes?|mins?|seconds?|secs?|hours?|degrees?|°c?)(?!\w)", re.I)
LOT = re.compile(r"\b(?:lot|batch|catalog(?:ue)?|cat\.?|ref)\s*(?:number|no\.?|#)?\s*[:#]?\s*[A-Za-z0-9-]*\d[A-Za-z0-9-]*", re.I)
OBSERVE = re.compile(r"\b(looks?|looked|colou?r(?:ed)?|cloudy|clear|bubbles?|bubbly|spill(?:ed)?|splash(?:ed)?|leak(?:ed|ing)?|precipitat\w*|turned|noticed?|seems?|smells?|dropped|forgot|mistake|oops|wrong|blue|red|green|yellow|pink|purple)\b", re.I)
WHO = re.compile(r"\b(?i:my name is|this is|i'm|i am)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)")
NOT_NAMES = {"Going", "Done", "Ready", "Here", "Now", "Just", "Adding", "Drawing", "Tube", "The", "On", "Doing"}

FINAL = ("done", "skipped", "unsure")
LABEL = {"vision": "seen", "speech": "said", "hint+speech": "seen+said"}
NEAR = 4.0  # seconds: a coach warning this close to one of our own lines repeats it


def speech_reason(text: str) -> str | None:
    if LOT.search(text):
        return "lot"
    if VOLUME.search(text):
        return "volume"
    if AMOUNT.search(text):
        return "amount"
    if OBSERVE.search(text):
        return "observation"
    return None


def is_hint(ev: dict) -> bool:
    return bool(ev.get("detail", {}).get("hint"))


def matches(ev: dict, expect: list[dict] | None) -> bool:
    if is_hint(ev):
        return False  # a hint is never proof (docs/EVENTS.md decisions)
    for e in expect or []:
        if ev.get("kind") != e.get("kind"):
            continue
        want = e.get("object")
        if want is None or str(ev.get("object") or "").strip().lower() == str(want).strip().lower():
            return True
    return False


def steps_from_coach(events: list[dict]) -> tuple[dict[int, dict], list[tuple[float, int]]]:
    """Final status per step from the coach's step events, plus when each step became current."""
    final: dict[int, dict] = {}
    current: list[tuple[float, int]] = []
    for ev in events:
        if ev.get("kind") != "step":
            continue
        d = ev["detail"]
        idx = d.get("index", ev.get("step"))
        status = d.get("status")
        if not isinstance(idx, int):
            continue
        if status == "current":
            current.append((ev["t"], idx))
            final.setdefault(idx, {"status": "current", "t": ev["t"], "detail": d, "confidence": ev.get("confidence")})
        elif status in FINAL:
            final[idx] = {"status": status, "t": ev["t"], "detail": d, "confidence": ev.get("confidence")}
    return final, current


def steps_from_evidence(proto_steps: list[dict], events: list[dict]) -> dict[int, dict]:
    """No coach events: walk the protocol's evidence ourselves. A step whose evidence never shows
    up while a later step's does counts as skipped."""
    out: dict[int, dict] = {}
    pos = 0
    for ev in events:
        conf = ev.get("confidence")
        if isinstance(conf, (int, float)) and conf < 0.5:
            continue
        for j in range(pos, len(proto_steps)):
            if matches(ev, proto_steps[j].get("expect")):
                for k in range(pos, j):
                    out[k] = {"status": "skipped", "t": ev["t"], "detail": {}, "confidence": None}
                out[j] = {"status": "done", "t": ev["t"], "detail": {"evidence": "vision"},
                          "confidence": conf}
                pos = j + 1
                break
    return out


def _proof_time(step: dict, events: list[dict], lo: float, hi: float) -> float | None:
    hits = [e["t"] for e in events if lo <= e["t"] <= hi + 0.01 and matches(e, step.get("expect"))]
    return hits[-1] if hits else None


def build_note(run: Run) -> dict:
    events = run.events
    proto = run.protocol or {}
    proto_steps = [s for s in (proto.get("steps") or []) if isinstance(s, dict)]

    final, current = steps_from_coach(events)
    status_source = "coach"
    if not final:
        final, current = steps_from_evidence(proto_steps, events), []
        status_source = "evidence" if final else "none"

    # ---- steps ---------------------------------------------------------------------------
    steps = []
    prev_t = run.start if run.start is not None else float("-inf")
    for i, sp in enumerate(proto_steps):
        info = final.get(i)
        status = info["status"] if info else "not reached"
        if status == "current":
            status = "not finished"
        d = info["detail"] if info else {}
        t = info["t"] if info else None
        label = None
        if status == "done":
            evidence = d.get("evidence")
            label = d.get("source") if d.get("source") in ("seen", "said", "seen+said") else LABEL.get(evidence, "seen")
            if label == "seen":
                # Time the step at the camera event that proved it, not when the coach caught up.
                proof = _proof_time(sp, events, prev_t, t)
                if proof is not None:
                    t = proof
            prev_t = t
        steps.append({"index": i, "number": i + 1, "text": str(sp.get("text") or f"Step {i + 1}"),
                      "status": status, "t": t, "label": label,
                      "confidence": info.get("confidence") if info else None,
                      "limit": (sp.get("technique") or {}).get("angle_max_deg")})

    def step_during(t: float) -> int | None:
        """The step in progress at time t."""
        if current:
            best = None
            for ct, idx in current:
                if ct <= t + 0.01:
                    best = idx
            if best is not None:
                return best
        for s in steps:  # no coach timeline: the first step finished at or after t
            if s["t"] is not None and s["t"] >= t - 0.01:
                return s["index"]
        return None

    def step_of(ev: dict) -> int | None:
        return ev["step"] if isinstance(ev.get("step"), int) else step_during(ev["t"])

    technique: list[dict] = []
    problems: list[dict] = []

    # ---- pipette angle per step, against the limit -------------------------------------------
    by_step: dict = {}
    for ev in events:
        if ev.get("kind") == "angle" and isinstance(ev["detail"].get("deg"), (int, float)):
            by_step.setdefault(step_of(ev), []).append(ev)
    for idx, evs in sorted(by_step.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        limit = next((e["detail"]["limit"] for e in evs if isinstance(e["detail"].get("limit"), (int, float))), None)
        if limit is None and idx is not None and 0 <= idx < len(steps):
            limit = steps[idx]["limit"]
        over = [e for e in evs if e["detail"].get("ok") is False
                or (limit is not None and e["detail"]["deg"] > limit)]
        worst = max(evs, key=lambda e: e["detail"]["deg"])
        where = f"step {idx + 1}" if idx is not None and idx < len(steps) else "the run"
        lim = f", limit {limit:g}°" if isinstance(limit, (int, float)) else ", no limit set"
        n = len(evs)
        reading = "reading" if n == 1 else "readings"
        if over:
            text = (f"Pipette angle during {where}: highest {worst['detail']['deg']:.0f}° from vertical{lim}; "
                    f"{len(over)} of {n} {reading} over.")
        else:
            text = f"Pipette angle during {where}: highest {worst['detail']['deg']:.0f}° from vertical{lim}; {n} {reading}, none over."
        technique.append({"t": worst["t"], "kind": "angle", "step": idx, "text": text,
                          "deg": round(float(worst["detail"]["deg"]), 1), "limit": limit,
                          "readings": n, "over": len(over)})
        if over:
            first = over[0]
            problems.append({"t": first["t"], "kind": "angle", "step": idx,
                             "text": f"Pipette tilted up to {worst['detail']['deg']:.0f}° from vertical during {where}{lim}."})

    # ---- fresh or reused tip for each draw ------------------------------------------------------
    tip_on_seen = False
    since_change: list[str] = []
    hints = 0
    for ev in events:
        kind = ev.get("kind")
        if kind in ("draw", "dispense") and is_hint(ev):
            hints += 1
            continue
        if kind == "tip_on":
            tip_on_seen, since_change = True, []
        elif kind == "tip_off":
            tip_on_seen, since_change = False, []
        elif kind == "draw":
            obj = str(ev.get("object") or "a tube")
            idx = step_of(ev)
            if since_change and obj not in since_change:
                text = f"Same tip reused: drew from {obj} after {since_change[-1]} with no tip change."
                technique.append({"t": ev["t"], "kind": "tip", "step": idx, "text": text, "fresh": False})
                problems.append({"t": ev["t"], "kind": "tip", "step": idx, "text": text})
            elif not since_change:
                text = (f"Fresh tip for the draw from {obj}." if tip_on_seen
                        else f"Drew from {obj}; no tip was seen going on first.")
                technique.append({"t": ev["t"], "kind": "tip", "step": idx, "text": text, "fresh": tip_on_seen})
            since_change.append(obj)

    # ---- what the person said -------------------------------------------------------------------
    said = []
    who = None
    for ev in events:
        if ev.get("kind") != "speech":
            continue
        text = str(ev["detail"].get("text") or "").strip()
        if not text:
            continue
        if who is None:
            speaker = ev["detail"].get("speaker")
            m = WHO.search(text)
            if isinstance(speaker, str) and speaker.strip():
                who = speaker.strip()
            elif m and m.group(1).split()[0] not in NOT_NAMES:
                who = m.group(1)
        reason = speech_reason(text)
        if reason:
            said.append({"t": ev["t"], "kind": reason, "step": step_during(ev["t"]), "text": text})

    # ---- what went wrong ----------------------------------------------------------------------
    for s in steps:
        if s["status"] == "skipped":
            problems.append({"t": s["t"], "kind": "skipped", "step": s["index"],
                             "text": f"Step {s['number']} skipped: {s['text']}"})
        elif s["status"] == "unsure":
            problems.append({"t": s["t"], "kind": "unsure", "step": s["index"],
                             "text": f"Step {s['number']} could not be confirmed: {s['text']}"})
    missing = [s for s in steps if s["status"] in ("not reached", "not finished")]
    if missing and len(missing) < len(steps):
        first, last = missing[0], missing[-1]
        span = f"step {first['number']}" if first is last else f"steps {first['number']} to {last['number']}"
        problems.append({"t": run.stop, "kind": "stopped", "step": first["index"],
                         "text": f"The run ended before {span} {'was' if first is last else 'were'} done."})

    seen_warn: list[tuple[float, str]] = []
    for ev in events:
        if ev.get("kind") != "warning":
            continue
        text = str(ev["detail"].get("text") or "").strip()
        low = text.lower()
        if not text or any(abs(ev["t"] - t0) < 5 and low == x for t0, x in seen_warn):
            continue
        seen_warn.append((ev["t"], low))

        def repeats(p: dict) -> bool:
            if p.get("t") is None or abs(p["t"] - ev["t"]) > NEAR:
                return False
            return ((p["kind"] == "angle" and ("angle" in low or "tilt" in low)) or
                    (p["kind"] == "tip" and "tip" in low) or
                    (p["kind"] == "skipped" and "skip" in low))
        if any(repeats(p) for p in problems if p["kind"] != "warning"):
            continue
        problems.append({"t": ev["t"], "kind": "warning", "step": step_of(ev),
                         "text": f"{ev.get('source', 'coach').capitalize()} warned: \"{text}\""})
    problems.sort(key=lambda p: (p["t"] is None, p["t"] or 0))
    technique.sort(key=lambda x: x["t"])

    # ---- header and summary ---------------------------------------------------------------------
    order = ["done", "skipped", "unsure", "not finished", "not reached"]
    counts = {k: sum(1 for s in steps if s["status"] == k) for k in order}
    parts = [f"{n} {k}" for k, n in counts.items() if n]
    if steps:
        summary = f"{len(steps)} protocol steps: " + ", ".join(parts) + ". "
    else:
        summary = "No protocol was loaded for this run. "
    summary += (f"{len(problems)} thing{'s' if len(problems) != 1 else ''} to check." if problems
                else "Nothing went wrong that the system could see.")

    started = datetime.fromtimestamp(run.start).astimezone() if run.start is not None else None
    return {
        "run": run.run_id,
        "title": str(proto.get("name") or "Lab run"),
        "protocol_file": run.protocol_file,
        "date": started.strftime("%A %-d %B %Y") if started else None,
        "time": started.strftime("%H:%M %Z") if started else None,
        "start": run.start,
        "stop": run.stop,
        "duration": (run.stop - run.start) if run.start is not None and run.stop is not None else None,
        "who": who,
        "status_source": status_source,
        "summary": summary,
        "counts": counts,
        "steps": steps,
        "technique": technique,
        "said": said,
        "problems": problems,
        "hints_not_used": hints,
        "videos": [{"name": v.name, "cam": v.cam, "timed": bool(v.frame_times)} for v in run.videos],
        "wording": "rule-based",
    }
