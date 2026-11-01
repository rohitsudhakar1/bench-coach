"""Silent, local video step labelling: python -m scripts.label_steps --help."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from scripts.render_demo import load_protocol

STATIC = Path(__file__).with_name("label_steps.html")


def probe(video: Path) -> dict:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
            "-show_format", "-show_frames", "-show_entries",
            "stream=codec_name,avg_frame_rate,duration:format=duration:frame=best_effort_timestamp_time",
            "-of", "json", str(video),
        ], capture_output=True, text=True, check=True, timeout=120)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        duration = float(stream.get("duration") or data["format"]["duration"])
        numerator, denominator = stream["avg_frame_rate"].split("/")
        fps = float(numerator) / float(denominator)
        if not math.isfinite(duration) or duration <= 0 or not 0 < fps <= 1000:
            raise ValueError("Invalid video duration or frame rate")
        frames = sorted({float(f["best_effort_timestamp_time"]) for f in data.get("frames", [])
                         if "best_effort_timestamp_time" in f})
        if frames:
            origin = frames[0]
            frames = [round(t - origin, 6) for t in frames]
        return {"duration": duration, "fps": fps, "codec": stream["codec_name"], "frames": frames}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        raise ValueError("Cannot read video. Check the file and install ffmpeg/ffprobe.") from exc


@contextmanager
def prepared_video(video: Path, force: bool = False):
    """MOV/HEVC is converted up front so unsupported browsers never get a black player."""
    info = probe(video)
    if not force and video.suffix.lower() == ".mp4" and info["codec"] == "h264":
        yield video, info, False
        return
    with tempfile.TemporaryDirectory(prefix="bench-label-") as directory:
        converted = Path(directory) / "preview.mp4"
        print("Preparing a silent H.264 preview; the original video is untouched.", flush=True)
        process = subprocess.Popen([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video), "-map", "0:v:0",
            "-an", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-fps_mode", "vfr", "-movflags", "+faststart", str(converted),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, stderr = process.communicate(timeout=600)
            if process.returncode:
                raise ValueError("ffmpeg could not prepare the preview. Check the video and free disk space.")
            yield converted, probe(converted), True
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()


def validate_labels(rows, steps: list, duration: float) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Labels must be a list of at most 10,000 entries.")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each label must be an object.")
        s = row.get("s")
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) or not 0 <= s <= duration:
            raise ValueError("Label time must be finite seconds within the video.")
        kind, index = row.get("kind"), row.get("step")
        if kind == "step":
            if type(index) is not int or not 0 <= index < len(steps) or index in seen:
                raise ValueError("Each step needs one valid zero-based index.")
            if row.get("status") not in ("done", "skipped"):
                raise ValueError("Step status must be done or skipped.")
            if "text" in row and row["text"] != steps[index]["text"]:
                raise ValueError("Saved step text differs from this protocol. Use its original protocol or a new output.")
            seen.add(index)
            result.append({"s": round(s, 6), "kind": kind, "step": index,
                           "status": row["status"], "text": steps[index]["text"]})
        elif kind == "warning":
            if row.get("code") not in ("reused_tip", "skipped_step"):
                raise ValueError("Unknown warning type.")
            if index is not None and (type(index) is not int or not 0 <= index < len(steps)):
                raise ValueError("Invalid warning step.")
            message = "Reused tip (hand-labelled)." if row["code"] == "reused_tip" else "Skipped step (hand-labelled)."
            if index is not None:
                message += f" Step {index + 1}: {steps[index]['text']}"
            result.append({"s": round(s, 6), "kind": kind, "step": index, "code": row["code"], "text": message})
        else:
            raise ValueError("This editor reopens step/warning labels only; use a new output for legacy action labels.")
    return sorted(result, key=lambda row: row["s"])


def atomic_save(out: Path, rows: list[dict]):
    out.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out.parent,
                                         prefix=f".{out.name}.", suffix=".tmp", delete=False) as handle:
            name = handle.name
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, out)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def create_app(video: Path, protocol: Path, out: Path, info: dict, *, converted=False, video_name=None):
    for source in (video, protocol):
        if out.resolve() == source.resolve():
            raise ValueError("Output must differ from the video and protocol.")
    data = load_protocol(protocol)
    steps = data.get("steps") or []
    if not steps or any(not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip() for s in steps):
        raise ValueError("Protocol must contain nonempty step text.")
    rows = []
    if out.exists():
        rows = validate_labels([json.loads(line) for line in out.read_text().splitlines()
                                if line.strip() and not line.lstrip().startswith("#")], steps, info["duration"])
    token = secrets.token_urlsafe(24)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.get("/")
    def page():
        return HTMLResponse(STATIC.read_text(), headers={"Cache-Control": "no-store"})

    @app.get("/api/session")
    def session():
        return {"name": data.get("name") or protocol.stem, "video": video_name or video.name,
                "out": out.name, "steps": steps, "labels": rows, "token": token,
                "converted": converted, **info}

    @app.get("/video")
    def media():
        return FileResponse(video, media_type="video/mp4")

    @app.post("/api/save")
    async def save(request: Request):
        nonlocal rows
        if request.headers.get("x-label-token") != token:
            raise HTTPException(403, "Open the labelling page before saving.")
        body = await request.body()
        if len(body) > 2_000_000:
            raise HTTPException(413, "Label file too large.")
        try:
            checked = validate_labels(json.loads(body), steps, info["duration"])
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            atomic_save(out, checked)
        except OSError as exc:
            raise HTTPException(500, "Could not save. Check output permissions and free disk space.") from exc
        rows = checked
        return {"saved": len(rows), "out": out.name}

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8810)
    parser.add_argument("--transcode", action="store_true", help="also convert H.264 MP4 if your browser cannot play it")
    args = parser.parse_args(argv)
    if not 8800 <= args.port <= 8899:
        parser.error("Use a port from 8800 through 8899.")
    if not args.video.is_file() or not args.protocol.is_file():
        parser.error("Video and protocol must be existing files.")
    if args.out.resolve() in (args.video.resolve(), args.protocol.resolve()):
        parser.error("Output must differ from the video and protocol.")
    import uvicorn
    def stop(_signum, _frame):
        raise KeyboardInterrupt
    previous_term = signal.signal(signal.SIGTERM, stop)
    try:
        with prepared_video(args.video, args.transcode) as (video, info, converted):
            app = create_app(video, args.protocol, args.out, info, converted=converted, video_name=args.video.name)
            print(f"Label steps at http://127.0.0.1:{args.port} (Ctrl-C to stop)", flush=True)
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"label_steps: {exc}\n")
    finally:
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    main()
