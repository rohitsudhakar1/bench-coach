"""Builds the test session: the four spoken questions over real bench noise.

The three short questions are laid over 20 s of bench audio at 3, 8 and 13 s. The long one,
which already has bench noise in it, follows at 20 s. Nothing is ever played.
"""
import json
import wave
from pathlib import Path

import numpy as np

VOICE = Path(__file__).resolve().parent / "voice"
RATE = 16000
PLACES = [("whats_next", 3.0), ("how_much", 8.0), ("did_i_add_tube_c", 13.0)]


def read(name) -> np.ndarray:
    with wave.open(str(VOICE / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


def session():
    """Returns (int16 audio, [(name, text, speech_start_s, speech_end_s)])."""
    info = json.loads((VOICE / "questions.json").read_text())
    x = read("bench_noise").copy()
    marks = []
    for name, at in PLACES:
        q = read(name)
        i = int(at * RATE)
        x[i:i + len(q)] += q
        marks.append((name, info[name]["text"], at + info[name]["speech_start"], at + info[name]["speech_end"]))
    long = read("long_noisy")
    marks.append(("long_noisy", info["long_noisy"]["text"], len(x) / RATE + info["long_noisy"]["speech_start"],
                  len(x) / RATE + info["long_noisy"]["speech_end"]))
    x = np.concatenate([x, long, np.zeros(RATE, np.float32)])
    return (np.clip(x, -1, 1) * 32767).astype(np.int16), marks


def write_wav(path, pcm: np.ndarray):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
