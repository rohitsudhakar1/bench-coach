"""The loudness gate on the four questions over bench noise. No speech model needed."""
import numpy as np
import pytest

from coach.listen import Gate
from coach.tests.voice_session import RATE, read, session


def cut(pcm, block):
    g = Gate()
    found = []
    for i in range(0, len(pcm), block):
        found += [t for _, t in g.feed(pcm[i:i + block], i / RATE)]
    return found + [t for _, t in g.flush()]


@pytest.mark.parametrize("block", [160, 320, 480, 512, 1600, 4096])
def test_every_question_found_once_and_never_early(block):
    pcm, marks = session()
    starts = cut(pcm, block)
    assert len(starts) == len(marks), starts
    for t, (_name, _text, s, _e) in zip(starts, marks):
        assert s - 0.35 <= t <= s + 0.2, (t, s)


def test_block_size_does_not_change_the_result():
    pcm, _ = session()
    ref = cut(pcm, 320)
    for block in (100, 1000, 1600, 4096):
        assert np.allclose(cut(pcm, block), ref, atol=1e-6)


def test_bench_noise_alone_opens_nothing_even_twice_as_loud():
    noise = read("bench_noise")
    for gain in (1.0, 2.0):
        pcm = (np.clip(noise * gain, -1, 1) * 32767).astype(np.int16)
        assert cut(pcm, 320) == []


def test_room_level_is_set_after_two_seconds_and_a_loud_room_raises_the_bar():
    quiet, loud = Gate(), Gate()
    rng = np.random.default_rng(1)
    for i in range(0, 3 * RATE, 320):
        quiet.feed((rng.normal(0, 0.002, 320) * 32767).astype(np.int16), i / RATE)
        loud.feed((rng.normal(0, 0.02, 320) * 32767).astype(np.int16), i / RATE)
    assert quiet.room is not None and loud.room is not None
    assert loud.room_bar() > 5 * quiet.room_bar()


def test_chatter_that_pauses_does_not_open_the_gate():
    # a steady murmur that stops for 0.4 s every 3 s; the background estimate drops in the gap
    rng = np.random.default_rng(2)
    n = 40 * RATE
    t = np.arange(n) / RATE
    murmur = rng.normal(0, 0.05, n) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    murmur[(t % 3.0) > 2.6] *= 0.05
    pcm = (np.clip(murmur, -1, 1) * 32767).astype(np.int16)
    assert cut(pcm, 320) == []
    old = Gate(room_margin=0.0)   # the gate without the room level does open on it
    found = [x for i in range(0, n, 320) for x in old.feed(pcm[i:i + 320], i / RATE)]
    assert found
