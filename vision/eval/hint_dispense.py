"""Hint mode with the coach's own evaluation (scripts/scorecard.py), for a draw-rule setting.

    $BENCH_PY -m vision.eval.hint_dispense --set '{"disp_min_dwell": 0.4}'

Runs the scorecard's hint replay (simulated person who only answers, 2 s later) with the given
DrawRules settings and prints questions, how many were about a real action, steps ticked, the
chance share, and the questions split by kind. Needs the coach part in the repo.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def measure(settings: dict) -> dict:
    sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
    import vision.draw as vd
    orig = vd.DrawRules
    vd.DrawRules = lambda **kw: orig(**{**settings, **kw})
    try:
        import scorecard as sc
        coach = sc.find_coach(None)
        totals = sc.hint_scores(coach)
        labels = sc.load_actions()
        out = {}
        for split in ("hold", "tune"):
            by_kind = {}
            for _, names in sc.action_streams(labels, split):
                evs, dur = sc.vision_events(labels, names, True)
                made = []
                base = sc.Session

                class Rec(base):
                    def __init__(self, *a, **k):
                        super().__init__(*a, **k)
                        made.append(self)
                sc.Session = Rec
                try:
                    sc.hint_replay(coach, labels, names, evs, dur)
                finally:
                    sc.Session = base
                acts = [a for a in labels["actions"] if a["clip"] == names[0]]
                for q in made[0].questions:
                    k = by_kind.setdefault(q.get("kind"), [0, 0])
                    k[0] += 1
                    k[1] += sc.near_action(q["t"], acts, q.get("kind"))
            x = totals[split]
            out[split] = {"questions": x["questions"], "real": x["real"], "ticked": x["ticked"], "steps": x["steps"],
                          "chance_real": round(x["chance_real"], 2),
                          "by_kind": {k: {"asked": v[0], "real": v[1]} for k, v in by_kind.items()}}
        return out
    finally:
        vd.DrawRules = orig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="{}")
    a = ap.parse_args()
    print(json.dumps(measure(json.loads(a.set)), indent=1))


if __name__ == "__main__":
    main()
