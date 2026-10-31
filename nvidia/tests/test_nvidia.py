"""Offline tests for the nvidia part. No network, no API keys."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nvidia import runner, synth
from nvidia.export_tasks import export_run
from nvidia.runlog import load_run
from nvidia.tasks import build_tasks
from nvidia.verify import check

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
REAL = SAMPLES / "run-real"
FAKE = SAMPLES / "run-fake"


def test_real_run_facts():
    f = load_run(REAL)
    assert f.skipped == [3, 4, 6]               # steps 4, 5 and 7 as a person counts them
    draws = f.draws()
    assert [d["object"] for d in draws] == ["tube A", "tube C"]
    assert draws[1]["reused"] is True           # no fresh tip before tube C


def test_fake_run_protocol_comes_from_protocols_folder():
    f = load_run(FAKE)
    assert len(f.steps) == 8 and f.skipped == [3, 4]
    assert f.status[6] == "unsure"


def test_verifier_parsing():
    t = {"kind": "skipped_step", "skipped_steps": [4, 5, 7]}
    assert check("skipped_step", "Steps 4, 5 and 7", t)[0] == 1
    assert check("skipped_step", "4, 5", t)[0] == 0
    assert check("skipped_step", "none", {"kind": "skipped_step", "skipped_steps": []})[0] == 1
    assert check("tip_reuse", "Yes, same tip.", {"kind": "tip_reuse", "reused": True})[0] == 1
    assert check("tip_reuse", "no", {"kind": "tip_reuse", "reused": True})[0] == 0
    want = {"kind": "missing_step_text", "text": "Put a fresh tip on the pipette."}
    assert check("missing_step_text", "put fresh tips on pipette", want)[0] == 1
    assert check("missing_step_text", "draw from tube A", want)[0] == 0


def test_tasks_hide_answers_and_speech():
    for folder in (REAL, FAKE):
        for t in build_tasks(load_run(folder)):
            kinds = {e["kind"] for e in t["context"]["events"]}
            assert not kinds & {"step", "warning", "say", "speech"}
            blob = json.dumps(t["context"])
            assert "Maya" not in blob and "W-2231" not in blob
            if t["kind"] == "missing_step_text":
                n = t["truth"]["step"]
                assert t["context"]["protocol"][n - 1]["text"] == "[missing]"


def test_export_harbor_layout_and_test_sh(tmp_path):
    dirs = export_run(REAL, tmp_path)
    assert len(dirs) == 5
    for d in dirs:
        for f in ("instruction.md", "task.toml", "environment/Dockerfile", "environment/context/task.json",
                  "solution/solve.sh", "tests/test.sh", "tests/truth.json", "tests/verify.py"):
            assert (d / f).is_file(), f
        assert "truth" not in (d / "environment/context/task.json").read_text()
    # run one task's own test.sh with the container paths pointed at temp folders
    d = next(x for x in dirs if x.name.endswith("skipped-step"))
    ws, logs = tmp_path / "ws", tmp_path / "logs"
    ws.mkdir()
    sh = (d / "tests/test.sh").read_text().replace("/workspace", str(ws)).replace("/tests", str(d / "tests")) \
        .replace("/logs", str(logs)).replace("python3", sys.executable)
    solve = (d / "solution/solve.sh").read_text().replace("/workspace", str(ws))
    subprocess.run(["bash", "-c", solve], check=True)
    subprocess.run(["bash", "-c", sh], check=True, capture_output=True)
    assert (logs / "verifier/reward.txt").read_text() == "1"
    (ws / "answer.txt").write_text("2\n")
    subprocess.run(["bash", "-c", sh], check=True, capture_output=True)
    assert (logs / "verifier/reward.txt").read_text() == "0"


def test_runner_baselines(tmp_path):
    export_run(REAL, tmp_path / "real")
    export_run(FAKE, tmp_path / "fake")
    assert runner.score(tmp_path, "reference")["mean"] == 1.0
    assert runner.score(tmp_path, "blank")["mean"] == 0.0
    rules = runner.score(tmp_path, "rules")
    assert rules["by_kind"]["skipped_step"]["mean"] == 1.0
    assert rules["by_kind"]["tip_reuse"]["mean"] == 1.0
    assert 0 < rules["by_kind"]["missing_step_text"]["mean"] < 1   # step 7 has no twin to copy
    assert runner.score(tmp_path, lambda task: "yes")["by_kind"]["tip_reuse"]["mean"] == 1.0


def test_claude_answer_needs_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        runner.claude_answer({"question": "q", "context": {}})


def test_local_synth_is_seeded_and_anonymised(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    a = synth.synthesize([SAMPLES], 30, tmp_path / "a", engine="local", seed=3)
    b = synth.synthesize([SAMPLES], 30, tmp_path / "b", engine="local", seed=3)
    assert a["manifest"]["engine"] == "local" and "not used" in a["manifest"]["engine_detail"]
    assert (tmp_path / "a/rows.jsonl").read_text() == (tmp_path / "b/rows.jsonl").read_text()
    runs = sorted((tmp_path / "a/runs").iterdir())
    assert len(runs) == 30
    text = "".join((r / "events.jsonl").read_text() + (r / "note.txt").read_text() for r in runs)
    for secret in ("Maya", "W-2231", "aa480e71", "/Us" "ers/", "demo-fake", "speech"):
        assert secret not in text
    # tips steps are skipped far more often in the recorded runs, and the corpus keeps that
    rec, syn = a["recorded"]["Pipetting demo"], a["synthetic"]["Pipetting demo"]
    assert syn["skip_rate"][3] > syn["skip_rate"][0]
    assert abs(syn["skip_rate"][3] - rec["skip_rate"][3]) < 0.3
    # synthetic runs export to tasks like recorded ones
    assert len(export_run(runs[0], tmp_path / "t")) >= 2


def test_prior_zero_gives_raw_rates():
    from nvidia.stats import run_stats
    st = run_stats([load_run(REAL), load_run(FAKE)], prior=0.0)["Pipetting demo"]
    assert st["skip_rate"] == [0, 0, 0, 1, 1, 0, 0.5, 0]
    assert st["tip_reuse_rate"] == 1.0
    assert st["out_of_order_rate"] == 0.0


@pytest.mark.skipif(not synth.has_datadesigner(), reason="data-designer not installed in this Python")
def test_datadesigner_engine_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("NEMO_TELEMETRY_ENABLED", "false")
    res = synth.synthesize([SAMPLES], 12, tmp_path / "dd", engine="datadesigner")
    assert res["manifest"]["engine"] == "datadesigner"
    assert "no language model" in res["manifest"]["notes"]
    assert len(list((tmp_path / "dd/runs").iterdir())) == 12
