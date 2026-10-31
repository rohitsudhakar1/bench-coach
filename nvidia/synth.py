"""Make a synthetic corpus of bench runs that follows the statistics of recorded runs.

    python -m nvidia.synth [--from runs/ ...] [--n 50] [--out nvidia/out/synth] [--engine auto]

Each synthetic run starts as one row of sampled settings: which steps were skipped, whether two
steps swapped order, peak tilt, seconds per step. NeMo Data Designer samples those rows when it
is installed (engine "datadesigner"); otherwise a seeded NumPy generator samples the same columns
(engine "local"). Each row is then expanded into runs/<id>/events.jsonl in the docs/EVENTS.md
format, so `python -m nvidia.export_tasks` works on synthetic runs too.

What leaves the recorded runs: only the numbers in stats.json and the protocol steps. Speech,
warnings, note text, run ids, times and file paths are never copied. With NVIDIA_API_KEY set and
the Data Designer engine, each run also gets a two-sentence practice note written by a Nemotron
model on build.nvidia.com from the sampled numbers alone; otherwise the note is a fixed template.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .runlog import REPO_ROOT, find_runs, load_run
from .stats import run_stats

T0 = 1793400000.0    # synthetic clock base; runs are spaced 600 s apart
NOTE_MODEL_ALIAS = "nvidia-text"   # Data Designer's default alias for the NVIDIA text model


def _columns(name: str, st: dict) -> dict:
    """Column specs shared by both engines: name -> (type, params)."""
    n = len(st["steps"])
    cols = {f"skip_{i}": ("bernoulli", {"p": st["skip_rate"][i]}) for i in range(n)}
    cols["swap"] = ("bernoulli", {"p": st["out_of_order_rate"]})
    cols["swap_at"] = ("category", {"values": list(range(max(n - 1, 1)))})
    cols["tilt_peak_deg"] = ("gaussian", {"mean": st["peak_tilt_deg"]["mean"], "std": st["peak_tilt_deg"]["std"]})
    cols["tilt_bad"] = ("bernoulli", {"p": st["run_bad_tilt_rate"]})
    cols["step_seconds"] = ("gaussian", {"mean": st["step_seconds"]["mean"], "std": st["step_seconds"]["std"]})
    return cols


def sample_local(cols: dict, n: int, seed: int) -> list[dict]:
    import numpy as np
    rng = np.random.default_rng(seed)
    rows = [{} for _ in range(n)]
    for name, (kind, p) in cols.items():
        if kind == "bernoulli":
            vals = (rng.random(n) < p["p"]).astype(int)
        elif kind == "category":
            vals = rng.choice(p["values"], size=n)
        else:
            vals = rng.normal(p["mean"], p["std"], size=n)
        for r, v in zip(rows, vals):
            r[name] = v.item() if hasattr(v, "item") else v
    return rows


def sample_datadesigner(cols: dict, n: int, protocol: str, workdir: Path, with_note: bool) -> list[dict]:
    os.environ.setdefault("NEMO_TELEMETRY_ENABLED", "false")
    import data_designer.config as dd
    from data_designer.config.run_config import RunConfig
    from data_designer.interface import DataDesigner

    b = dd.DataDesignerConfigBuilder()
    for name, (kind, p) in cols.items():
        if kind == "bernoulli":
            b.add_column(dd.SamplerColumnConfig(name=name, sampler_type=dd.SamplerType.BERNOULLI,
                                                params=dd.BernoulliSamplerParams(p=p["p"])))
        elif kind == "category":
            b.add_column(dd.SamplerColumnConfig(name=name, sampler_type=dd.SamplerType.CATEGORY,
                                                params=dd.CategorySamplerParams(values=p["values"])))
        else:
            b.add_column(dd.SamplerColumnConfig(name=name, sampler_type=dd.SamplerType.GAUSSIAN,
                                                params=dd.GaussianSamplerParams(mean=p["mean"], stddev=p["std"])))
    if with_note:
        skips = "".join(f"{{% if {c} == 1 %}}{int(c[5:]) + 1} {{% endif %}}"
                        for c in cols if c.startswith("skip_"))
        b.add_column(dd.ExpressionColumnConfig(name="skipped_list", expr=skips))
        b.add_column(dd.LLMTextColumnConfig(
            name="note", model_alias=NOTE_MODEL_ALIAS,
            prompt=("Write two plain sentences for a lab notebook about one practice pipetting run of "
                    f"the protocol '{protocol}'. Step numbers skipped: {{{{ skipped_list }}}}. "
                    "Peak pipette tilt {{ tilt_peak_deg | round(1) }} degrees. Do not invent names, "
                    "dates, places or results.")))
    designer = DataDesigner(artifact_path=str(workdir))
    designer.set_run_config(RunConfig(otel_metrics_port=None))   # no metrics server on any port
    res = designer.create(b, num_records=n, dataset_name="bench_runs")
    df = res.load_dataset()
    rows = []
    for rec in df.to_dict(orient="records"):
        rows.append({k: (v.item() if hasattr(v, "item") else v) for k, v in rec.items()})
    return rows


def template_note(row: dict, steps: list[dict]) -> str:
    skipped = [str(i + 1) for i in range(len(steps)) if row.get(f"skip_{i}")]
    word = "step" if len(skipped) == 1 else "steps"
    s1 = f"Practice run with {word} {', '.join(skipped)} skipped." if skipped else "Practice run with no skipped steps."
    return f"{s1} Peak pipette tilt about {max(row['tilt_peak_deg'], 0):.0f} degrees."


def expand(row: dict, steps: list[dict], protocol: str, run_id: str, t0: float) -> list[dict]:
    """One sampled row -> the run's event list, in the hub's event format."""
    def ev(t, source, kind, obj=None, step=None, conf=None, **detail):
        return {"t": round(t, 3), "source": source, "kind": kind, "object": obj, "hand": None,
                "step": step, "confidence": conf, "detail": detail}
    n = len(steps)
    order = list(range(n))
    if row.get("swap") and 0 <= int(row.get("swap_at", 0)) < n - 1:
        k = int(row["swap_at"])
        order[k], order[k + 1] = order[k + 1], order[k]
    gap = max(float(row["step_seconds"]), 1.0)
    peak = min(max(float(row["tilt_peak_deg"]), 0.0), 60.0)
    E = [ev(t0, "synth", "run", status="start", run=run_id, protocol=f"synthetic:{protocol}",
            protocol_data={"name": protocol, "steps": steps})]
    t, pending = t0 + 0.5, []
    for i in order:
        if row.get(f"skip_{i}"):
            pending.append(i)
            continue
        x = (steps[i]["expect"] or [{"kind": "hand_touch"}])[0]
        limit = (steps[i].get("technique") or {}).get("angle_max_deg")
        if x.get("kind") == "draw" and limit:
            deg = peak if row.get("tilt_bad") else min(peak, limit - 2.0)
            E.append(ev(t - 0.3, "synth", "angle", "pipette", step=i, conf=0.8,
                        deg=round(deg, 1), ok=deg <= limit, limit=limit))
        E.append(ev(t, "synth", x.get("kind", "hand_touch"), x.get("object"), step=i, conf=0.85))
        for j in pending:
            E.append(ev(t, "synth", "step", step=j, index=j, status="skipped", text=steps[j]["text"]))
        pending = []
        E.append(ev(t, "synth", "step", step=i, conf=0.85, index=i, status="done", text=steps[i]["text"]))
        t += gap
    for j in pending:
        E.append(ev(t, "synth", "step", step=j, index=j, status="skipped", text=steps[j]["text"]))
    E.append(ev(t + 0.5, "synth", "run", status="stop", run=run_id))
    return E


def has_datadesigner() -> bool:
    try:
        import data_designer.interface  # noqa: F401
        return True
    except Exception:
        return False


def synthesize(sources: list[str | Path], n: int, out: Path, engine: str = "auto", seed: int = 0,
               prior: float = 1.0) -> dict:
    folders = find_runs(sources)
    runs = []
    for f in folders:
        try:
            runs.append(load_run(f))
        except (ValueError, FileNotFoundError):
            continue
    if not runs:
        raise SystemExit("no recorded runs found; pass --from with run folders")
    stats = run_stats(runs, prior=prior)
    if engine == "auto":
        engine = "datadesigner" if has_datadesigner() else "local"
    with_note = engine == "datadesigner" and bool(os.environ.get("NVIDIA_API_KEY"))

    if out.exists():
        shutil.rmtree(out)
    (out / "runs").mkdir(parents=True)
    names = sorted(stats)
    total_runs = sum(stats[k]["runs"] for k in names)
    rows_all, idx = [], 0
    for pi, name in enumerate(names):
        st = stats[name]
        share = round(n * st["runs"] / total_runs) if pi < len(names) - 1 else n - idx
        if share <= 0:
            continue
        cols = _columns(name, st)
        if engine == "datadesigner":
            rows = sample_datadesigner(cols, share, name, out / "datadesigner", with_note)
        else:
            rows = sample_local(cols, share, seed + pi)
        for row in rows:
            run_id = f"synth-{idx:04d}"
            events = expand(row, st["steps"], name, run_id, T0 + 600 * idx)
            d = out / "runs" / run_id
            d.mkdir()
            with open(d / "events.jsonl", "w") as fh:
                for e in events:
                    fh.write(json.dumps(e) + "\n")
            note = row.get("note") if with_note and row.get("note") else template_note(row, st["steps"])
            (d / "note.txt").write_text(str(note).strip() + "\n")
            rows_all.append({"run": run_id, "protocol": name, **{k: v for k, v in row.items() if k != "note"}})
            idx += 1

    synth_runs = [load_run(p) for p in sorted((out / "runs").iterdir())]
    check = run_stats(synth_runs, prior=0.0)
    manifest = {
        "engine": engine,
        "engine_detail": ("NeMo Data Designer samplers (not seeded; a rerun gives new rows)" if engine == "datadesigner"
                          else f"local seeded NumPy generator, seed {seed}; NeMo Data Designer was not used"),
        "notes": (f"Data Designer LLM column, model alias {NOTE_MODEL_ALIAS} on build.nvidia.com" if with_note
                  else "fixed template; no language model was called"),
        "anonymised": "no speech, warning or note text, names, run ids, times or paths copied from recorded runs",
        "recorded_runs": len(runs),
        "synthetic_runs": idx,
        "prior": prior,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (out / "stats.json").write_text(json.dumps({"recorded": stats, "synthetic": check}, indent=1))
    with open(out / "rows.jsonl", "w") as fh:
        for r in rows_all:
            fh.write(json.dumps(r, default=str) + "\n")
    return {"manifest": manifest, "recorded": stats, "synthetic": check}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make synthetic bench runs from recorded run statistics.")
    ap.add_argument("--from", dest="sources", nargs="+", default=None,
                    help="run folders or folders of runs (default: runs/ at the repo root, else nvidia/samples)")
    ap.add_argument("--n", type=int, default=50, help="number of synthetic runs")
    ap.add_argument("--out", default=str(REPO_ROOT / "nvidia" / "out" / "synth"))
    ap.add_argument("--engine", default="auto", choices=["auto", "datadesigner", "local"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prior", type=float, default=1.0, help="weight pulling each skip rate toward the protocol average")
    a = ap.parse_args(argv)
    sources = a.sources
    if sources is None:
        sources = [REPO_ROOT / "runs"] if find_runs([REPO_ROOT / "runs"]) else [REPO_ROOT / "nvidia" / "samples"]
    if a.engine == "datadesigner" and not has_datadesigner():
        ap.error("data-designer is not installed in this Python; see nvidia/README.md")
    res = synthesize(sources, a.n, Path(a.out), a.engine, a.seed, a.prior)
    m = res["manifest"]
    print(f"engine: {m['engine_detail']}")
    print(f"notes: {m['notes']}")
    print(f"{m['synthetic_runs']} synthetic runs from {m['recorded_runs']} recorded runs in {a.out}/runs")
    for name, st in res["recorded"].items():
        sy = res["synthetic"].get(name, {})
        print(f"  {name}: skip rate per step recorded {st['skip_rate']}")
        print(f"  {' ' * len(name)}  synthetic           {sy.get('skip_rate')}")
        print(f"  {' ' * len(name)}  tip reuse recorded {st['tip_reuse_rate']} synthetic {sy.get('tip_reuse_rate')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
