"""python -m note build <run folder>  |  python -m note serve"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .runfolder import RUNS_DIR
from .service import HUB, create_app, make_note, note_event, post_event


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m note")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write note.md, note.html and note.json for one run folder")
    b.add_argument("run_dir")
    b.add_argument("--events", help="events file (default <run_dir>/events.jsonl)")
    b.add_argument("--source", action="append", default=[], help="a recorded video; repeat for each camera")
    b.add_argument("--out", help="where to write the note (default: the run folder)")
    b.add_argument("--no-claude", action="store_true", help="skip the wording pass even with a key")
    b.add_argument("--no-frames", action="store_true", help="do not save key frames")
    b.add_argument("--post", action="store_true", help="also send a note event (to the hub, or stdout)")
    b.add_argument("--hub", default=HUB)
    s = sub.add_parser("serve", help="the note service")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8705)
    s.add_argument("--runs", default=str(RUNS_DIR), help="folder holding runs/<run>/ (default repo-root runs/)")
    s.add_argument("--hub", default=HUB)
    s.add_argument("--public-url", help="address used in the note event (default http://<host>:<port>)")
    s.add_argument("--no-watch", action="store_true", help="do not build notes on the hub's run stop")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        res = make_note(a.run_dir, use_claude=False if a.no_claude else None, frames=not a.no_frames,
                        sources=a.source, events_file=a.events, out_dir=a.out)
        print(res["note"]["summary"])
        print(f"wrote note.md, note.html, note.json in {res['folder']} (wording: {res['note']['wording']})")
        if a.post:
            post_event(note_event(res["run"], os.environ.get("BENCH_NOTE_URL", "http://127.0.0.1:8705")), a.hub)
    else:
        import uvicorn
        base = a.public_url or os.environ.get("BENCH_NOTE_URL") or f"http://{a.host}:{a.port}"
        uvicorn.run(create_app(Path(a.runs), a.hub, watch=not a.no_watch, base_url=base),
                    host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
