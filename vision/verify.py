"""Optional Claude check of --new-draw hints (only when ANTHROPIC_API_KEY is set).

For each draw or dispense hint, for each camera that saw it (at most 2), three crops around where
the tip went (0.4 s before the entry, its middle, 0.4 s after) go side by side into one picture.
claude-haiku-5-5 answers in a fixed JSON form: is the tip inside a container, is liquid going in
or out, which container. The event gets

    detail.verified  true, false or null
    detail.vlm       the answer, or {"skipped": why}

Rule: tip "no" or liquid "none" -> false; tip "yes" -> true; anything else -> null. The event's
kind and object never change, and the model is not told which kind the tracker guessed. No key,
a failed or slow call (2 s timeout, no retries), or more than `per_minute` checks a minute give null.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import threading
import time
from collections import deque

import cv2
import numpy as np

MODEL = "claude-haiku-5-5"
TILE = 256
KINDS = ("draw", "dispense")

PROMPT = """These pictures come from cameras over a lab bench where someone is using a pipette.
Each picture is one camera: three frames side by side, left to right = just before, during and
just after a moment when a tracker thinks the pipette tip went into a container (tube, bottle,
flask, well). Two pictures means two cameras at the same moment.

Answer about the middle frame, using the outer frames for motion:
- tip: is the end of the pipette tip inside the mouth or neck of a container? "yes", "no", or
  "unclear" if the tip end is hidden.
- liquid: is liquid being taken up ("taking") or pushed out ("releasing")? "none" if the tip is
  not in a container or nothing happens, "unclear" if you cannot tell.
- container: a few words for the container the tip is in, or "" if none.
Be strict: a pipette held near or above a container is not inside it."""

SCHEMA = {
    "type": "object",
    "properties": {
        "tip": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "liquid": {"type": "string", "enum": ["taking", "releasing", "none", "unclear"]},
        "container": {"type": "string"},
    },
    "required": ["tip", "liquid", "container"],
    "additionalProperties": False,
}


def verdict(ans: dict | None):
    if not ans:
        return None
    if ans.get("tip") == "no" or ans.get("liquid") == "none":
        return False
    return True if ans.get("tip") == "yes" else None


class AnthropicClient:
    """ask(jpegs) -> (answer, usage). Raises on any failure."""

    def __init__(self, timeout: float = 2.0):
        import anthropic
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=0)  # key from ANTHROPIC_API_KEY

    def ask(self, jpegs):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.standard_b64encode(j).decode()}} for j in jpegs]
        content.append({"type": "text", "text": PROMPT})
        r = self.client.messages.create(
            model=MODEL, max_tokens=200, thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}])
        if r.stop_reason == "refusal":
            raise RuntimeError("refusal")
        text = next(b.text for b in r.content if b.type == "text")
        return json.loads(text), {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}


def default_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        return AnthropicClient()
    except Exception:
        return None


def crop(frame, xy, size, tile: int = TILE):
    h, w = frame.shape[:2]
    half = min(int(max(1.2 * size, 0.22 * max(w, h))), min(w, h) // 2)
    cx = int(min(max(xy[0], half), w - half))
    cy = int(min(max(xy[1], half), h - half))
    return cv2.resize(frame[cy - half:cy + half, cx - half:cx + half], (tile, tile), interpolation=cv2.INTER_AREA)


def strip(frames, xy, size):
    tiles = [crop(f, xy, size) for f in frames if f is not None]
    if not tiles:
        return None
    tiles += [tiles[-1]] * (3 - len(tiles))
    sep = np.full((TILE, 4, 3), 255, np.uint8)
    ok, buf = cv2.imencode(".jpg", np.hstack([tiles[0], sep, tiles[1], sep, tiles[2]]), [cv2.IMWRITE_JPEG_QUALITY, 75])
    return buf.tobytes() if ok else None


class FrameRing:
    """The last few seconds of one camera's processed frames."""

    def __init__(self, seconds: float = 8.0):
        self.seconds, self.buf = seconds, deque()

    def add(self, t, frame):
        self.buf.append((t, frame))
        while self.buf and t - self.buf[0][0] > self.seconds:
            self.buf.popleft()

    def at(self, t):
        return min(self.buf, key=lambda x: abs(x[0] - t))[1] if self.buf else None


class Verifier:
    def __init__(self, client=None, per_minute: int = 20, clock=time.time):
        self.client, self.per_minute, self.clock = client, per_minute, clock
        self.calls: deque = deque()
        self.entries: deque = deque(maxlen=200)
        self.cache: dict = {}
        self.lock = threading.Lock()

    def note(self, entry):
        """Every Entry pushed into the Sequencer, so we know where the tip went."""
        self.entries.append(entry)

    def pictures(self, ev: dict, frame_at) -> list:
        d = ev["detail"]
        start, end = d.get("start", ev["t"]), d.get("end", ev["t"])
        out = []
        for cam in d.get("cams", [d.get("cam", 0)])[:2]:
            best = None
            for e in self.entries:
                ov = min(e.t1, end) - max(e.t0, start)
                if e.cam == cam and ov >= -0.05 and (best is None or ov > best[0]):
                    best = (ov, e)
            if best is None:
                continue
            e = best[1]
            s = strip([frame_at(cam, tt) for tt in (e.t0 - 0.4, (e.t0 + e.t1) / 2, e.t1 + 0.4)], e.xy, e.size)
            if s:
                out.append(s)
        return out

    def ask(self, jpegs) -> dict:
        if not jpegs:
            return {"verified": None, "vlm": {"skipped": "no pictures"}}
        key = hashlib.sha1(b"".join(jpegs)).hexdigest()
        with self.lock:
            if key in self.cache:
                return {"verified": verdict(self.cache[key]), "vlm": {**self.cache[key], "cached": True}}
            if self.client is None:
                return {"verified": None, "vlm": {"skipped": "no key"}}
            now = self.clock()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.per_minute:
                return {"verified": None, "vlm": {"skipped": "cap"}}
            self.calls.append(now)
        t = time.time()
        try:
            ans, usage = self.client.ask(jpegs)
            ans = {k: ans.get(k) for k in ("tip", "liquid", "container")}
        except Exception as exc:  # timeout, network, refusal, bad JSON
            return {"verified": None, "vlm": {"skipped": f"failed: {type(exc).__name__}"}}
        with self.lock:
            self.cache[key] = ans
        return {"verified": verdict(ans), "vlm": {**ans, "ms": round(1000 * (time.time() - t)), "usage": usage}}


class VerifyWorker:
    """Asks in a background thread and sends each hint on when the answer is in; other events
    are never held up."""

    def __init__(self, verifier: Verifier, send):
        self.v, self.send, self.q = verifier, send, queue.Queue()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def submit(self, ev, jpegs):
        self.q.put((ev, jpegs))

    def _run(self):
        while (item := self.q.get()) is not None:
            ev, jpegs = item
            ev["detail"].update(self.v.ask(jpegs))
            self.send([ev])

    def close(self):
        self.q.put(None)
        self.th.join(timeout=30)
