"""The coach as a program: replay, a stand-in hub, the health port, a file as the mic.

Servers only on ports 8800-8899, and every process is stopped by the test.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "coach" / "samples" / "demo-events.jsonl"


def coach(*args, timeout=60, **kw):
    return subprocess.run([sys.executable, "-m", "coach", "--no-llm", *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout, **kw)


def free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_replay_prints_events_and_lines_without_a_hub(tmp_path):
    state = tmp_path / "state.json"
    r = coach("--events", str(SAMPLE), "--no-hub", "--port", "0", "--state-out", str(state))
    assert r.returncode == 0, r.stderr
    events = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
    kinds = {e["kind"] for e in events}
    assert {"step", "warning", "say"} <= kinds
    assert "[coach says] Run finished. Check steps 4, 5 and 7." in r.stderr
    st = json.loads(state.read_text())
    assert [s["status"] for s in st["steps"]] == ["done", "done", "done", "skipped", "skipped",
                                                  "done", "skipped", "done"]


def test_hint_mode_replay(tmp_path):
    f = tmp_path / "h.jsonl"
    rows = [{"t": 10.0, "source": "vision", "kind": "tip_on", "confidence": 0.9, "detail": {}},
            {"t": 12.0, "source": "vision", "kind": "draw", "object": "tube", "confidence": 0.5,
             "detail": {"cam": 0, "hint": True}},
            {"t": 14.0, "source": "speech", "kind": "speech", "detail": {"text": "yes"}}]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    r = coach("--events", str(f), "--no-hub", "--port", "0", "--hints")
    assert "[coach says] Did you just draw from tube A?" in r.stderr
    steps = [json.loads(l) for l in r.stdout.splitlines() if '"kind": "step"' in l]
    assert any(s["detail"].get("source") == "seen+said" and s["detail"]["status"] == "done" for s in steps)


class Hub(BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        Hub.got.append(json.loads(self.rfile.read(n)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def test_posts_to_the_hub_and_serves_health():
    hub_port, health_port = 8841, 8842
    if not (free(hub_port) and free(health_port)):
        pytest.skip("test ports busy")
    Hub.got = []
    srv = HTTPServer(("127.0.0.1", hub_port), Hub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    p = subprocess.Popen([sys.executable, "-m", "coach", "--no-llm", "--mic", "off",
                          "--hub", f"http://127.0.0.1:{hub_port}", "--port", str(health_port)],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        import httpx
        body = None
        for _ in range(60):
            try:
                body = httpx.get(f"http://127.0.0.1:{health_port}/health", timeout=0.5).json()
                break
            except Exception:
                time.sleep(0.2)
        assert body and body["part"] == "coach" and body["current"] == 0
        state = httpx.get(f"http://127.0.0.1:{health_port}/state", timeout=1).json()
        assert len(state["steps"]) == 8
        for _ in range(40):
            if any(e.get("kind") == "say" for e in Hub.got):
                break
            time.sleep(0.1)
        assert any(e.get("kind") == "step" for e in Hub.got)
        assert any(e.get("kind") == "say" and e["detail"]["text"] == "First: put a fresh tip." for e in Hub.got)
    finally:
        p.terminate()
        p.wait(10)
        srv.shutdown()
        srv.server_close()
    assert free(health_port) and free(hub_port)


def whisper_cached():
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return (hub / "models--Systran--faster-whisper-small.en").is_dir()


@pytest.mark.models
@pytest.mark.skipif(not whisper_cached(), reason="Whisper small.en not on disk")
def test_a_file_as_the_mic(tmp_path):
    from coach.tests.voice_session import session, write_wav
    pcm, _ = session()
    wav = tmp_path / "session.wav"
    write_wav(wav, pcm)
    r = coach("--source", str(wav), "--fast-source", "--no-hub", "--port", "0", "--gap", "0", timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    heard = [l for l in r.stderr.splitlines() if l.startswith("coach: heard:")]
    assert len(heard) == 4, r.stderr
    speech = [json.loads(l) for l in r.stdout.splitlines() if '"kind": "speech"' in l]
    assert len(speech) == 4
    assert "[coach says] Next: draw 100 microlitres of water from tube A." in r.stderr


@pytest.mark.models
@pytest.mark.skipif(not os.environ.get("BENCH_SAMPLES"), reason="BENCH_SAMPLES not set")
def test_a_sample_video_as_the_mic_runs_alone():
    clip = Path(os.environ["BENCH_SAMPLES"]) / "lsv_mv" / "CP-Left-Front-414-445.mp4"
    if not clip.is_file():
        pytest.skip("sample clip missing")
    r = coach("--source", str(clip), "--fast-source", "--no-hub", "--port", "0", timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]


def test_follows_hub_events_over_websocket():
    """A stand-in hub sends events on /ws; it takes no POSTs, so the coach prints its events."""
    import asyncio
    import websockets
    port = 8843
    if not free(port):
        pytest.skip("test port busy")
    rows = [json.loads(l) for l in SAMPLE.read_text().splitlines() if l.startswith("{")]
    rows = [r for r in rows if r["source"] != "coach"]
    out = {}

    async def main():
        async def serve(ws):
            await asyncio.sleep(1.0)
            await ws.send(json.dumps(rows[:3]))       # a list is accepted too
            for r in rows[3:]:
                await ws.send(json.dumps(r))
            await asyncio.sleep(2.0)

        async with websockets.serve(serve, "127.0.0.1", port):
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "coach", "--no-llm", "--mic", "off", "--port", "0",
                "--gap", "0", "--hub", f"http://127.0.0.1:{port}", cwd=ROOT,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.sleep(8)
            p.terminate()
            o, e = await p.communicate()
            out["stdout"], out["stderr"] = o.decode(), e.decode()

    asyncio.run(main())
    steps = [json.loads(l) for l in out["stdout"].splitlines() if '"kind": "step"' in l]
    final = {}
    for s in steps:
        final[s["detail"]["index"]] = s["detail"]["status"]
    assert final == {0: "done", 1: "done", 2: "done", 3: "skipped", 4: "skipped", 5: "done",
                     6: "skipped", 7: "done"}, out["stderr"][-1500:]
    assert free(port)
