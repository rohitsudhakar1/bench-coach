import json
from pathlib import Path

from coach import protocol
from coach.brain import Brain

HERE = Path(__file__).resolve().parent
T0 = 1793400000.0


def demo_brain(**kw) -> Brain:
    b = Brain(protocol.load("pipetting-demo.yaml"), clock=lambda: T0, **kw)
    b.start()
    return b


def ev(kind, t, obj=None, conf=0.9, source="vision", **detail):
    return {"t": T0 + t, "source": source, "kind": kind, "object": obj, "hand": "right",
            "step": None, "confidence": conf, "detail": detail}


def said(outs):
    return [o.text for o in outs if o.kind == "say"]


def emitted(outs, kind=None):
    return [o.event for o in outs if o.kind == "emit" and (kind is None or o.event["kind"] == kind)]


def run(brain, events):
    outs = []
    for e in events:
        outs += brain.handle(e)
    return outs


def sample_events():
    lines = (HERE.parent / "samples" / "demo-events.jsonl").read_text().splitlines()
    return [json.loads(l) for l in lines if l.strip() and not l.startswith("#")]
