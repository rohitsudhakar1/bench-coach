"""Read one recorded run folder and pull out the facts the tasks and statistics need.

Only events between the run's start and stop are used; the hub log can hold later smoke-test
events after a stop. Standard library plus PyYAML.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Events a solver may see. Coach output (step, warning, say) gives the answers away, and speech
# is free text that can hold names, so neither goes into a task.
VISIBLE_KINDS = {"tip_on", "tip_off", "draw", "dispense", "hand_touch", "angle"}


@dataclass
class RunFacts:
    run_id: str
    protocol_name: str
    steps: list[dict]                 # [{"text", "expect"}] from the protocol
    events: list[dict]                # every event inside the run window
    start: float
    stop: float | None
    status: dict[int, str] = field(default_factory=dict)   # final status per step index
    done_order: list[int] = field(default_factory=list)    # step indices in the order they finished

    @property
    def skipped(self) -> list[int]:
        return sorted(i for i, s in self.status.items() if s == "skipped")

    def visible_events(self) -> list[dict]:
        """Vision and angle events with times relative to the run start."""
        out = []
        for e in self.events:
            if e.get("kind") not in VISIBLE_KINDS:
                continue
            row = {"t": round(float(e["t"]) - self.start, 2), "kind": e["kind"]}
            if e.get("object") and e["kind"] != "angle":
                row["object"] = e["object"]
            if e["kind"] == "angle":
                d = e.get("detail") or {}
                row["deg"] = d.get("deg")
                row["ok"] = d.get("ok")
            out.append(row)
        return out

    def draws(self) -> list[dict]:
        """Every draw with whether a fresh tip went on since the previous draw.

        reused is True when the pipette drew before with no tip_on in between. The first draw
        of a run has reused None (nothing to compare to).
        """
        out, seen_draw, fresh = [], False, False
        for e in self.events:
            k = e.get("kind")
            if k == "tip_on":
                fresh = True
            elif k == "draw":
                reused = (not fresh) if seen_draw else None
                prev = out[-1]["object"] if out else None
                out.append({"t": float(e["t"]), "object": e.get("object"), "reused": reused,
                            "previous_object": prev})
                seen_draw, fresh = True, False
        return out


def read_events(path: Path) -> list[dict]:
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    events.sort(key=lambda e: float(e.get("t", 0)))
    return events


def _load_protocol(start_detail: dict, folder: Path) -> dict:
    if isinstance(start_detail.get("protocol_data"), dict):
        return start_detail["protocol_data"]
    import yaml
    for cand in [folder / "protocol.yaml", REPO_ROOT / str(start_detail.get("protocol") or "")]:
        if cand.is_file():
            return yaml.safe_load(cand.read_text())
    raise FileNotFoundError(f"no protocol found for run in {folder}")


def load_run(folder: str | Path) -> RunFacts:
    folder = Path(folder)
    events = read_events(folder / "events.jsonl")
    start_ev = next((e for e in events if e.get("kind") == "run"
                     and (e.get("detail") or {}).get("status") == "start"), None)
    if start_ev is None:
        raise ValueError(f"{folder}: events.jsonl has no run start event")
    sd = start_ev.get("detail") or {}
    run_id = sd.get("run") or folder.name
    stop_ev = next((e for e in events if e.get("kind") == "run" and e["t"] >= start_ev["t"]
                    and (e.get("detail") or {}).get("status") == "stop"
                    and (e.get("detail") or {}).get("run", run_id) == run_id), None)
    t0 = float(start_ev["t"])
    t1 = float(stop_ev["t"]) if stop_ev else None
    window = [e for e in events if float(e["t"]) >= t0 and (t1 is None or float(e["t"]) <= t1)]
    proto = _load_protocol(sd, folder)
    steps = [{"text": s.get("text", ""), "expect": s.get("expect") or [],
              **({"technique": s["technique"]} if s.get("technique") else {})} for s in proto.get("steps", [])]
    facts = RunFacts(run_id=run_id, protocol_name=proto.get("name", "protocol"), steps=steps,
                     events=window, start=t0, stop=t1)
    for e in window:
        if e.get("kind") != "step":
            continue
        d = e.get("detail") or {}
        i, s = d.get("index"), d.get("status")
        if not isinstance(i, int) or s in (None, "current"):
            continue
        facts.status[i] = s
        if s == "done" and i not in facts.done_order:
            facts.done_order.append(i)
    return facts


def find_runs(paths: list[str | Path]) -> list[Path]:
    """Expand each path to run folders: a folder with events.jsonl, or a parent of such folders."""
    out = []
    for p in map(Path, paths):
        if (p / "events.jsonl").is_file():
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(c for c in p.iterdir() if (c / "events.jsonl").is_file()))
    return out
