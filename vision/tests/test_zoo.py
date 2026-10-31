"""The other detectors' glue, with no model loaded."""
import pytest

from vision.__main__ import parse
from vision.core import Det
from vision.detector import CONTAINER_PROMPTS, PROMPTS
from vision.zoo import OWL_PHRASES, Owl2, make, merge


def test_merge_joins_same_name_overlaps_only():
    a = [Det("pipette", (0, 0, 10, 10), 0.4), Det("hand", (50, 50, 60, 60), 0.9)]
    b = [Det("pipette", (1, 0, 11, 10), 0.6), Det("tube", (0, 0, 10, 10), 0.5)]
    out = merge(a, b)
    assert len(out) == 3
    p = next(d for d in out if d.label == "pipette")
    assert p.score == 0.6
    assert p.box[0] == pytest.approx(0.6)  # score-weighted: 0 * 0.4 + 1 * 0.6
    assert any(d.label == "tube" for d in out)


def test_merge_keeps_far_boxes_apart():
    out = merge([Det("tube", (0, 0, 10, 10), 0.5)], [Det("tube", (100, 100, 110, 110), 0.5)])
    assert len(out) == 2


def test_owl_phrases_map_back_to_event_names():
    o = Owl2.__new__(Owl2)
    o.set_prompts(PROMPTS, OWL_PHRASES)
    assert len(o.queries) == len(o.names) > len(PROMPTS)
    assert set(o.names) == set(PROMPTS.values())
    c = o.with_prompts(CONTAINER_PROMPTS)
    assert set(c.names) == set(CONTAINER_PROMPTS.values())
    assert set(o.names) == set(PROMPTS.values())  # the copy did not change the original


def test_unknown_detector_name():
    with pytest.raises(ValueError):
        make("yolo-nothing")


def test_detector_option_defaults_to_tiny():
    assert parse(["--source", "a.mp4"]).detector == "gdino-tiny"
    assert parse(["--source", "a.mp4", "--detector", "owlv2"]).detector == "owlv2"
