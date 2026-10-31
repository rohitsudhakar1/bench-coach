import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import scorecard  # noqa: E402


def test_coach_rows_wait_until_coach_is_merged():
    rows = scorecard.coach_rows(None)
    assert rows and all(r["number"] == "waiting for coach/" for r in rows)


def test_find_coach_none_without_folder(tmp_path):
    assert scorecard.find_coach(str(tmp_path)) is None


def test_render_has_every_column_and_no_local_paths():
    rows = [scorecard.row("Part", "held-out", "1 clip", "1 of 2", "means | this", "nothing", "0.5")]
    text = scorecard.render(rows, 1.0, None)
    assert "| Part | Data | Measured on | Number | Chance |" in text
    assert "means / this" in text
    assert "waiting for `coach/`" in text
    assert "/" + "Users/" not in text  # no local home folders in a public repo


def test_table_plan_is_valid():
    rng = random.Random(1)
    for _ in range(20):
        plan, visits = scorecard.table_plan(rng)
        assert len(plan) == len(visits)
        assert plan[0] == ("tip_on", "tip box") and plan[-1] == ("tip_off", "waste")


def test_table_mode_runs_and_beats_chance():
    t = scorecard.table_mode(n=4)
    assert t["fired"] and t["right"] <= min(t["fired"], t["planned"])
    assert t["right"] / t["fired"] > t["chance_precision"]


def test_convert_key_matches_known_protocol_lengths():
    assert all(len(v) >= 1 for v in scorecard.CONVERT_KEY.values())
    assert len(scorecard.CONVERT_KEY["lsv_mv/CP-414-445-protocol.txt"]) == len(scorecard.CP414["steps"])


def test_near_action_tolerance():
    acts = [{"kind": "draw", "start": 10.0, "end": 12.0}]
    assert scorecard.near_action(13.4, acts, "draw")
    assert not scorecard.near_action(13.6, acts, "draw")
    assert not scorecard.near_action(11.0, acts, "dispense")
