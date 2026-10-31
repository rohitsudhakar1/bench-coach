import asyncio
import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from note import fakerun, service


def free_port():
    for port in range(8840, 8899):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    pytest.skip("no free port in 8840-8899")


@pytest.fixture
def runs(tmp_path):
    fakerun.make(tmp_path / "run-1")
    return tmp_path


def test_routes(runs, capsys):
    app = service.create_app(runs, hub=None, watch=False, base_url="http://127.0.0.1:8805")
    c = TestClient(app)
    assert c.get("/health").json()["ok"]
    r = c.post("/note", json={"run": "run-1", "polish": False})
    assert r.status_code == 200 and r.json()["url"] == "http://127.0.0.1:8805/note/run-1"
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert out[-1]["kind"] == "note" and out[-1]["detail"] == {
        "run": "run-1", "path": "/note/run-1", "url": "http://127.0.0.1:8805/note/run-1"}
    html = c.get("/note/run-1").text
    assert "/runs/run-1/cam0.mp4#t=" in html and "/note/run-1.pdf" in html
    assert "## Steps" in c.get("/note/run-1?format=md").text
    assert c.get("/note/run-1?format=json").json()["counts"]["done"] == 5
    pdf = c.get("/note/run-1.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    z = c.get("/note/run-1.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    v = c.get("/runs/run-1/cam0.mp4", headers={"Range": "bytes=0-99"})
    assert v.status_code == 206 and len(v.content) == 100
    assert c.get("/runs/run-1/note_frames/problem-1-cam0.jpg").status_code == 200
    assert c.get("/runs/run-1/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.get("/note/nope").status_code == 404
    assert c.get("/note/..").status_code == 404


def test_post_without_run_uses_the_newest(runs):
    c = TestClient(service.create_app(runs, hub=None, watch=False))
    assert c.post("/note").json()["run"] == "run-1"


def test_builds_on_the_hubs_run_stop(runs):
    """Real flow: a hub websocket sends the run stop; the note posts its note event back."""
    import uvicorn
    from fastapi import FastAPI, WebSocket

    got = []
    hub = FastAPI()

    @hub.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        await sock.send_text(json.dumps({"t": time.time(), "source": "app", "kind": "run",
                                         "detail": {"status": "stop", "run": "run-1"}}))
        await asyncio.sleep(30)

    @hub.post("/events")
    async def events(ev: dict):
        got.append(ev)
        return {"ok": True}

    hub_port = free_port()
    hub_srv = uvicorn.Server(uvicorn.Config(hub, host="127.0.0.1", port=hub_port, log_level="error"))
    threading.Thread(target=hub_srv.run, daemon=True).start()
    while not hub_srv.started:
        time.sleep(0.05)
    note_port = free_port()
    app = service.create_app(runs, hub=f"http://127.0.0.1:{hub_port}", watch=True,
                             base_url=f"http://127.0.0.1:{note_port}")
    note_srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=note_port, log_level="error"))
    threading.Thread(target=note_srv.run, daemon=True).start()
    try:
        end = time.time() + 20
        while not got and time.time() < end:
            time.sleep(0.1)
        assert got and got[0]["kind"] == "note" and got[0]["detail"]["run"] == "run-1"
        assert got[0]["detail"]["url"] == f"http://127.0.0.1:{note_port}/note/run-1"
        assert (runs / "run-1" / "note.html").is_file()
    finally:
        note_srv.should_exit = True
        hub_srv.should_exit = True
        time.sleep(0.5)
