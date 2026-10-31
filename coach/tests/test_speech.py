import pytest

from coach import heard
from coach.tests.helpers import T0, demo_brain, emitted, ev, run, said


def hear(b, text, t):
    return b.hear(text, t=T0 + t)


@pytest.mark.parametrize("text,kind,obj", [
    ("Tip's on.", "tip_on", None),
    ("tips on", "tip_on", None),
    ("Okay, fresh tip.", "tip_on", None),
    ("I drew from tube A.", "draw", "tube a"),
    ("Drew from tube eh", "draw", "tube a"),
    ("Added it to B.", "dispense", "tube b"),
    ("added it to be", "dispense", "tube b"),
    ("I dispensed into tube bee", "dispense", "tube b"),
    ("Took up the dye from tube see", "draw", "tube c"),
    ("Mixed three times", "mix", None),
    ("Ejected the tip", "tip_off", None),
    ("Tip is off", "tip_off", None),
])
def test_statements(text, kind, obj):
    c = heard.statement(text)
    assert c and c["kind"] == kind and c["object"] == obj


@pytest.mark.parametrize("text", [
    "Did I add tube C?", "I haven't added it to B yet", "I'm going to draw from tube A",
    "Should I eject the tip?", "I need to put a tip on", "What's next?", "how much do I add",
])
def test_questions_plans_and_negations_are_not_statements(text):
    assert heard.statement(text) is None


def test_spoken_protocol_end_to_end():
    b = demo_brain()
    lines = []
    for t, text in enumerate(["Tip's on.", "I drew from tube A.", "Added it to B.", "Ejected the tip.",
                              "Fresh tip.", "Drew from tube see.", "Added it to B and mixed three times.",
                              "done"]):
        lines += said(hear(b, text, t * 10))
    assert b.status == ["done"] * 8
    assert all(s["source"] == "said" for s in b.state()["steps"])
    assert lines[-1] == "All steps done. Nice work."


def test_said_steps_carry_the_said_label():
    b = demo_brain()
    outs = hear(b, "tip's on", 3)
    step = emitted(outs, "step")[0]
    assert step["detail"]["evidence"] == "speech" and step["detail"]["source"] == "said"
    assert emitted(outs, "speech")[0]["detail"]["text"] == "tip's on"


def test_long_fresh_tip_sentence_ticks_and_answers():
    b = demo_brain()
    outs = hear(b, "Okay, I just put a fresh tip on the pipette, what is the next step I should do?", 3)
    assert b.status[0] == "done"
    assert said(outs) == ["Next: draw 100 microlitres of water from tube A."]


def test_done_ticks_the_current_step():
    b = demo_brain()
    hear(b, "Done.", 2)
    assert b.status[0] == "done" and b.evidence[0] == "speech"


def test_wrong_tube_does_not_tick():
    b = demo_brain()
    hear(b, "tip's on", 1)
    outs = hear(b, "I drew from tube C", 3)
    assert b.current == 1
    assert said(outs) == ["That doesn't match step 2. Please check."]


def test_speech_cannot_skip_ahead():
    b = demo_brain()
    hear(b, "added it to B", 1)
    assert b.current == 0


def test_contradiction_with_the_camera_asks_gently():
    b = demo_brain()
    run(b, [ev("tip_on", 1), ev("draw", 4, "tube C", conf=0.9)])   # step 2 wants tube A
    assert b.current == 1   # step 6 is too far ahead to count as a skip
    outs = hear(b, "I drew from tube A", 6)
    assert said(outs) == ["The camera saw tube C, you said tube A. Which one?"]
    assert b.current == 1


@pytest.mark.parametrize("q,answer", [
    ("What's next?", "Now: put a fresh tip."),
    ("how much do I add", "100 microlitres of water from tube A."),
    ("Did I add tube C?", "No, nothing with tube C yet."),
    ("Did I add tube see?", "No, nothing with tube C yet."),
    ("which step am I on", "Step 1 of 8, 8 left."),
])
def test_rule_answers(q, answer):
    b = demo_brain()
    assert said(hear(b, q, 2)) == [answer]


def test_did_i_add_after_camera_and_after_speech():
    b = demo_brain()
    run(b, [ev("tip_on", 1), ev("draw", 3, "tube A")])
    assert said(hear(b, "did I add tube A", 4)) == ["Yes, you drew from tube A."]
    hear(b, "added it to B", 5)
    assert said(hear(b, "did I add tube B", 6)) == ["Step 3 is ticked, you said so."]


def test_repeat_says_the_last_line():
    b = demo_brain()
    b.said("Next: dispense it into tube B.")
    assert said(hear(b, "can you repeat that", 1)) == ["Next: dispense it into tube B."]


def test_spoken_tip_counts_for_the_reused_tip_warning():
    b = demo_brain()
    for t, text in enumerate(["tip's on", "drew from tube A", "added it to B"]):
        hear(b, text, t)
    outs = run(b, [ev("draw", 10, "tube C")])
    assert "Same tip used twice. Change it before tube C." in said(outs)


def test_claude_only_words_answers_and_falls_back():
    calls = []

    def answerer(brain, question, rule):
        calls.append((question, rule))
        return "Draw from tube A next."
    b = demo_brain(answerer=answerer)
    hear(b, "tip's on", 1)
    assert calls == []                                    # statements never go to Claude
    assert said(hear(b, "what's next", 2)) == ["Draw from tube A next."]
    assert calls[0][1].startswith("Now: draw")

    def broken(*a):
        raise TimeoutError
    b = demo_brain(answerer=broken)
    assert said(hear(b, "what's next", 2)) == ["Now: put a fresh tip."]
