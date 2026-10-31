import numpy as np

from vision.draw import Entry
from vision.verify import FrameRing, Verifier, VerifyWorker, strip, verdict


class Fake:
    def __init__(self, ans=None, fail=False):
        self.ans, self.fail, self.calls = ans or {"tip": "yes", "liquid": "taking", "container": "tube"}, fail, 0

    def ask(self, jpegs):
        self.calls += 1
        if self.fail:
            raise TimeoutError
        return dict(self.ans), {"input_tokens": 600, "output_tokens": 30}


def pic():
    return strip([np.zeros((480, 640, 3), np.uint8)] * 3, (100, 100), 40)


def test_verdict_rule():
    assert verdict({"tip": "yes", "liquid": "taking"}) is True
    assert verdict({"tip": "no", "liquid": "taking"}) is False
    assert verdict({"tip": "yes", "liquid": "none"}) is False
    assert verdict({"tip": "unclear", "liquid": "unclear"}) is None
    assert verdict(None) is None


def test_no_key_gives_null():
    assert Verifier(None).ask([pic()])["verified"] is None


def test_failure_gives_null_and_cache_reuses():
    assert Verifier(Fake(fail=True)).ask([pic()])["verified"] is None
    f = Fake()
    v = Verifier(f)
    assert v.ask([pic()])["verified"] is True
    assert v.ask([pic()])["vlm"]["cached"] is True and f.calls == 1


def test_per_minute_cap():
    now = [0.0]
    v = Verifier(Fake(), per_minute=2, clock=lambda: now[0])
    imgs = [strip([np.full((480, 640, 3), i, np.uint8)] * 3, (100, 100), 40) for i in range(4)]
    res = [v.ask([j])["vlm"] for j in imgs[:3]]
    assert res[2] == {"skipped": "cap"}
    now[0] = 61.0
    assert v.ask([imgs[3]])["verified"] is True


def test_pictures_one_per_camera_and_worker_keeps_kind():
    v = Verifier(Fake())
    for cam in (0, 1):
        v.note(Entry(cam, 1.0, 1.5, "tube", (100, 100), 40))
    rings = [FrameRing(), FrameRing()]
    for i in range(30):
        for r in rings:
            r.add(i * 0.1, np.zeros((480, 640, 3), np.uint8))
    ev = {"t": 1.5, "kind": "draw", "object": "tube", "detail": {"cam": 0, "cams": [0, 1], "start": 1.0, "end": 1.5, "hint": True}}
    jpegs = v.pictures(ev, lambda c, t: rings[c].at(t))
    assert len(jpegs) == 2
    sent = []
    w = VerifyWorker(v, sent.extend)
    w.submit(ev, jpegs)
    w.close()
    assert sent[0]["kind"] == "draw" and sent[0]["detail"]["verified"] is True and sent[0]["detail"]["hint"] is True
