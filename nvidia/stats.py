"""Statistics of recorded runs, the only thing the synthetic corpus learns from.

Per protocol: how often each step was skipped, how often steps finished out of order, how often
a tip was reused before a new tube, pipette tilt and seconds per step. No text from the run
(speech, warnings, notes) is read here.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .runlog import RunFacts


def _mean_std(xs: list[float], default_mean: float, default_std: float) -> tuple[float, float]:
    if not xs:
        return default_mean, default_std
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) if len(xs) > 1 else default_std
    return round(m, 3), round(max(sd, 0.5), 3)


def run_stats(runs: list[RunFacts], prior: float = 1.0) -> dict:
    """Group runs by protocol and measure them.

    prior pulls each step's skip rate toward the rate over all steps of that protocol, weighted
    like `prior` extra runs. With only a few recorded runs a raw rate is 0 or 1; prior=0 gives
    the raw rates.
    """
    groups: dict[str, list[RunFacts]] = defaultdict(list)
    for r in runs:
        groups[r.protocol_name].append(r)
    out = {}
    for name, rs in groups.items():
        steps = rs[0].steps
        n = len(rs)
        skip_k = [sum(1 for r in rs if r.status.get(i) == "skipped") for i in range(len(steps))]
        pooled = sum(skip_k) / (n * len(steps)) if steps else 0.0
        skip = [round((k + prior * pooled) / (n + prior), 4) for k in skip_k]
        unsure = [round(sum(1 for r in rs if r.status.get(i) == "unsure") / n, 4) for i in range(len(steps))]
        out_of_order = sum(1 for r in rs if r.done_order != sorted(r.done_order)) / n
        compared = [d for r in rs for d in r.draws()
                    if d["reused"] is not None and d["object"] != d["previous_object"]]
        reuse = (sum(1 for d in compared if d["reused"]) / len(compared)) if compared else None
        angles = [float(e["detail"]["deg"]) for r in rs for e in r.events
                  if e.get("kind") == "angle" and isinstance((e.get("detail") or {}).get("deg"), (int, float))]
        bad = [e for r in rs for e in r.events if e.get("kind") == "angle" and (e.get("detail") or {}).get("ok") is False]
        # peak tilt per run is what the synthetic runs sample
        peaks = [max(a) for a in ([float(e["detail"]["deg"]) for e in r.events if e.get("kind") == "angle"
                                   and isinstance((e.get("detail") or {}).get("deg"), (int, float))] for r in rs) if a]
        gaps = []
        for r in rs:
            done_t = [float(e["t"]) for e in r.events if e.get("kind") == "step"
                      and (e.get("detail") or {}).get("status") == "done"]
            ts = [r.start] + done_t
            gaps += [b - a for a, b in zip(ts, ts[1:]) if b > a]
        m_gap, s_gap = _mean_std(gaps, 4.0, 1.5)
        m_peak, s_peak = _mean_std(peaks, 12.0, 4.0)
        out[name] = {
            "runs": n,
            "steps": [{"text": s["text"], "expect": s["expect"], **({"technique": s["technique"]} if s.get("technique") else {})}
                      for s in steps],
            "skip_rate": skip,
            "skip_count": skip_k,
            "unsure_rate": unsure,
            "out_of_order_rate": round(out_of_order, 4),
            "tip_reuse_rate": round(reuse, 4) if reuse is not None else None,
            "tip_reuse_compared": len(compared),
            "angle_readings": len(angles),
            "angle_bad_rate": round(len(bad) / len(angles), 4) if angles else 0.0,
            "run_bad_tilt_rate": round(sum(1 for r in rs if any(e.get("kind") == "angle" and (e.get("detail") or {}).get("ok") is False for e in r.events)) / n, 4),
            "peak_tilt_deg": {"mean": m_peak, "std": s_peak},
            "step_seconds": {"mean": m_gap, "std": s_gap},
        }
    return out
