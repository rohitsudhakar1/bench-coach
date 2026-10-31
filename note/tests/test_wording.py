from note.service import make_note
from note.wording import lines_of, polish, safe_rewrite


def test_safe_rewrite_rules():
    line = "Pipette tilted up to 27° from vertical during step 2, limit 20°."
    assert safe_rewrite(line, "During step 2 the pipette tilted up to 27° from vertical (limit 20°).")
    assert not safe_rewrite(line, "Pipette tilted 30° during step 2, limit 20°.")          # changed number
    assert not safe_rewrite(line, "Maya tilted the pipette 27° during step 2, limit 20°.")  # new name
    assert not safe_rewrite(line, "")
    assert not safe_rewrite(line, None)


def test_polish_keeps_good_and_drops_bad(fake_run):
    note = make_note(fake_run, use_claude=False, frames=False)["note"]
    lines = lines_of(note)
    assert not any("Maya" in v for v in lines.values())  # quotes are never sent

    def stand_in(sent):
        out = {k: v for k, v in sent.items()}
        out["problems.0"] = "The pipette leaned up to 27° from vertical in step 2 (limit 20°)."
        out["problems.1"] = "Same tip reused for tube D."  # invented tube: must be dropped
        return out
    before = note["problems"][1]["text"]
    polish(note, call=stand_in)
    assert note["problems"][0]["text"].startswith("The pipette leaned")
    assert note["problems"][1]["text"] == before
    assert "Claude" in note["wording"]


def test_polish_failure_leaves_rule_based(fake_run, monkeypatch):
    note = make_note(fake_run, use_claude=False, frames=False)["note"]

    def boom(_):
        raise RuntimeError("no network")
    polish(note, call=boom)
    assert note["wording"] == "rule-based"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert polish(note) is note and note["wording"] == "rule-based"


def test_tube_letters_must_match():
    line = "Same tip reused: drew from tube C after tube A with no tip change."
    assert not safe_rewrite(line, "Same tip reused: drew from tube D after tube A with no tip change.")
    assert safe_rewrite(line, "The same tip went from tube A into tube C with no tip change.")
