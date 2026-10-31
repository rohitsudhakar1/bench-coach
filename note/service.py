"""Build, save and serve lab notes (port 8705, docs/EVENTS.md)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import weakref
from pathlib import Path

import httpx
from starlette.requests import Request

from .build import build_note
from .frames import FRAME_DIR, attach_offsets, save_key_frames
from .render import to_html, to_markdown
from .runfolder import RUNS_DIR, load_run, read_jsonl
from .wording import polish

HUB = os.environ.get("BENCH_HUB", "http://127.0.0.1:8700")
# Keep a shared lock while a builder or waiter owns it, then release the run key.
_locks = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def _build_lock(out: Path):
    key = str(out.resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def make_note(run_dir: str | Path, use_claude: bool | None = None, frames: bool = True,
              sources: list[str] | None = None, events_file: str | None = None,
              out_dir: str | Path | None = None) -> dict:
    """Build in a fresh process, then release all native resources on worker exit."""
    from .worker import run_worker

    out = Path(out_dir) if out_dir else Path(run_dir)
    lock = _build_lock(out)
    with lock:
        payload = dict(run_dir=str(run_dir), use_claude=use_claude, frames=frames,
                       sources=[str(p) for p in sources] if sources else None,
                       events_file=str(events_file) if events_file else None,
                       out_dir=str(out_dir) if out_dir else None)
        return json.loads(run_worker("build", json.dumps(payload).encode()))


def _make_note(run_dir: str | Path, use_claude: bool | None = None, frames: bool = True,
               sources: list[str] | None = None, events_file: str | None = None,
               out_dir: str | Path | None = None) -> dict:
    """Build the note for one run folder; write note.md, note.html, note.json and key frames.
    use_claude None means: only if ANTHROPIC_API_KEY is set."""
    run_dir = Path(run_dir)
    out = Path(out_dir) if out_dir else run_dir
    out.mkdir(parents=True, exist_ok=True)
    lock = _build_lock(out)
    with lock:
        run = load_run(run_dir, sources=sources, events_file=events_file)
        note = build_note(run)
        attach_offsets(note, run)
        if use_claude is not False:
            polish(note)
        if frames:
            save_key_frames(note, run, out / FRAME_DIR)
        else:
            for p in note["problems"]:
                p["frames"] = []
        note["built_at"] = time.time()
        (out / "note.md").write_text(to_markdown(note), encoding="utf-8")
        (out / "note.html").write_text(to_html(note), encoding="utf-8")
        (out / "note.json").write_text(json.dumps(note, indent=1, default=str), encoding="utf-8")
    return {"run": note["run"], "folder": str(out), "note": note}


def note_event(run_id: str, base_url: str) -> dict:
    path = f"/note/{run_id}"
    return {"t": time.time(), "source": "note", "kind": "note", "object": None, "hand": None,
            "step": None, "confidence": None,
            "detail": {"run": run_id, "path": path, "url": base_url.rstrip("/") + path}}


def post_event(event: dict, hub: str | None) -> bool:
    """To the hub; with no hub, one JSON line on stdout."""
    if hub:
        try:
            httpx.post(f"{hub.rstrip('/')}/events", json=event, timeout=2.0).raise_for_status()
            return True
        except Exception:
            pass
    print(json.dumps(event), flush=True)
    return False


def wait_for_files(folder: Path, run_id: str, timeout: float = 10.0) -> None:
    """After a stop, give the hub time to log the stop and capture time to close its files."""
    end = time.time() + timeout
    while time.time() < end:
        ev = folder / "events.jsonl"
        logged = ev.is_file() and any(r.get("kind") == "run" and (r.get("detail") or {}).get("status") == "stop"
                                      for r in read_jsonl(ev))
        tp = folder / "timing.jsonl"
        closed = (not tp.is_file()) or any(r.get("kind") == "stop" for r in read_jsonl(tp))
        if logged and closed:
            return
        time.sleep(0.25)


def latest_run(runs_dir: Path) -> Path | None:
    folders = [p for p in runs_dir.glob("*") if (p / "events.jsonl").is_file()]
    return max(folders, key=lambda p: (p / "events.jsonl").stat().st_mtime) if folders else None


def create_app(runs_dir: Path = RUNS_DIR, hub: str | None = HUB, watch: bool = True,
               base_url: str = "http://127.0.0.1:8705"):
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
    from pydantic import BaseModel

    from .export import print_html, render_pdf, run_zip

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(watch_hub()) if watch and hub else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Bench Coach note", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    runs_dir = Path(runs_dir)

    class NoteRequest(BaseModel):
        run: str | None = None
        polish: bool | None = None
        frames: bool = True

    def folder_for(run_id: str) -> Path:
        root = runs_dir.resolve()
        p = (runs_dir / run_id).resolve()
        if p.parent != root or not p.is_dir():
            raise HTTPException(404, "no such run")
        return p

    def build(run_id: str | None, use_claude=None, frames=True, announce=True) -> dict:
        folder = folder_for(run_id) if run_id else latest_run(runs_dir)
        if folder is None:
            raise HTTPException(404, "no runs yet")
        res = make_note(folder, use_claude=use_claude, frames=frames)
        event = note_event(folder.name, base_url)
        if announce:
            post_event(event, hub)
        return {"run": folder.name, "path": event["detail"]["path"], "url": event["detail"]["url"],
                "summary": res["note"]["summary"], "wording": res["note"]["wording"]}

    def saved(run_id: str) -> tuple[Path, dict]:
        folder = folder_for(run_id)
        if not (folder / "note.json").is_file():
            build(run_id, use_claude=False, announce=False)
        return folder, json.loads((folder / "note.json").read_text(encoding="utf-8"))

    @app.get("/health")
    def health():
        return {"ok": True, "part": "note", "watching_hub": bool(watch and hub)}

    @app.post("/note")
    def post_note(req: NoteRequest | None = None):
        req = req or NoteRequest()
        return build(req.run, req.polish, req.frames)

    def export(run_id: str, archive: bool):
        folder, note = saved(run_id)
        document = print_html(note, folder)
        try:
            pdf = render_pdf(document)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        if archive:
            try:
                content = run_zip(note, folder, document, pdf)
            except FileNotFoundError as exc:
                raise HTTPException(409, str(exc)) from exc
        ext = "zip" if archive else "pdf"
        # Fixed names: no event text goes into a header.
        return Response(content if archive else pdf, media_type=f"application/{ext}",
                        headers={"Content-Disposition": f'attachment; filename="lab-note.{ext}"',
                                 "Cache-Control": "no-store"})

    @app.get("/note/{run_id}.pdf")
    def get_pdf(run_id: str):
        return export(run_id, archive=False)

    @app.get("/note/{run_id}.zip")
    def get_zip(run_id: str):
        return export(run_id, archive=True)

    @app.get("/note/{run_id}.benchling")
    @app.get("/note/{run_id}.eln")
    @app.get("/note/{run_id}.csv")
    def get_notebook_export(run_id: str, request: Request):
        from .elnexport import entry_html, eln_archive, steps_csv

        folder, note = saved(run_id)
        format = request.url.path.rsplit(".", 1)[-1]
        if format == "eln":
            content, mime, name = eln_archive(note, folder), "application/vnd.eln+zip", "lab-note.eln"
        elif format == "csv":
            content, mime, name = steps_csv(note), "text/csv", "steps.csv"
        else:
            content, mime, name = entry_html(note, folder), "text/html", "benchling-entry.html"
        return Response(content, media_type=mime, headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get("/note/{run_id}")
    def get_note(run_id: str, format: str = "html"):
        _, note = saved(run_id)
        prefix = f"/runs/{run_id}/"
        if format == "md":
            return PlainTextResponse(to_markdown(note, prefix), media_type="text/markdown")
        if format == "json":
            return JSONResponse(note)
        return HTMLResponse(to_html(note, prefix))

    @app.get("/runs/{run_id}/{file_path:path}")
    def run_file(run_id: str, file_path: str):
        folder = folder_for(run_id)
        parts = Path(file_path).parts
        # Judge the name asked for, not a symlink's target: capture may link recordings in.
        if not parts or any(x in ("..", "", "/") for x in parts) or file_path.startswith("/"):
            raise HTTPException(404)
        p = folder.joinpath(*parts)
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    async def on_stop(run_id: str | None):
        if not run_id:
            return
        try:
            folder = folder_for(run_id)
        except HTTPException:
            await asyncio.sleep(2.0)  # the hub may create the folder a moment later
            try:
                folder = folder_for(run_id)
            except HTTPException:
                print(f"note: no run folder for {run_id}", file=sys.stderr)
                return
        await asyncio.to_thread(wait_for_files, folder, run_id)
        try:
            await asyncio.to_thread(build, run_id)
        except Exception as exc:
            print(f"note: could not build the note for {run_id}: {exc}", file=sys.stderr)

    async def watch_hub():
        import websockets
        url = hub.replace("http", "ws", 1).rstrip("/") + "/ws"
        while True:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        try:
                            data = json.loads(msg)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        for ev in data if isinstance(data, list) else [data]:
                            if isinstance(ev, dict) and ev.get("kind") == "run" and \
                                    (ev.get("detail") or {}).get("status") == "stop":
                                asyncio.create_task(on_stop(ev["detail"].get("run")))
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(2.0)

    app.state.on_stop = on_stop
    return app
