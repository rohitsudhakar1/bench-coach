"""Fetch a recording session the Raspberry Pi uploaded to Cloudflare R2.

    python -m scripts.r2_pull --list
    python -m scripts.r2_pull --latest --out DIR
    python -m scripts.r2_pull --session e259bf [--device campi] --out DIR

Each session in the bucket is up to four objects:
    lab-cam/<device>/<stream>/YYYY/MM/DD/<HHMMSS.mmm>_<session>_<stream>.<ext>
with stream usb, ribbon (videos), audio (.m4a) and sync (.json). Older uploads have no
"_<stream>" suffix and no sync file; they are listed too.

A pull writes DIR/<device>-<session>/usb.mp4, ribbon.mp4, audio.m4a and sync.json, and points
DIR/current at that folder. Files already
there with the right size are not downloaded again. Every file is checked against the size R2
reports. Each video's modification time is set to the session's real end, so tools that read
"start = mtime - length" (the renderer's lab note date) get the real recording time.

Read only: this lists and gets objects, it never writes or deletes anything in the bucket.
Credentials come from R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ENV = ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
PREFIX = "lab-cam/"
STREAMS = ("usb", "ribbon", "audio")
LOCAL_NAMES = {"usb": "usb.mp4", "ribbon": "ribbon.mp4", "audio": "audio.m4a"}
KEY_RE = re.compile(
    r"^lab-cam/(?P<device>[^/]+)/(?P<stream>usb|ribbon|audio|sync)/(?P<y>\d{4})/(?P<mo>\d{2})/(?P<d>\d{2})/"
    r"(?P<hms>\d{6}(?:\.\d{1,3})?)_(?P<session>[0-9A-Za-z]+)(?:_[a-z]+)?\.(?:mp4|m4a|json)$")
DEFAULT_MAX_MB = 400


class PullError(RuntimeError):
    """Something the person running the demo has to fix (shown as one line, no traceback)."""


# ------------------------------------------------------------------ connection ----------------

def bucket_name() -> str:
    return os.environ.get("R2_BUCKET", "")


def make_client():
    """An S3 client for R2, or a PullError saying exactly what is missing."""
    missing = [n for n in ENV if not os.environ.get(n)]
    if missing:
        raise PullError("missing environment variables: " + ", ".join(missing) +
                        ". Load the R2 settings first, for example: set -a; . path/to/r2.env; set +a")
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise PullError("boto3 is not installed in this Python. Install it with: "
                        "BENCH_PY=\"$BENCH_PY\" scripts/addpkg.sh boto3") from None
    return boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                        region_name="auto", config=Config(retries={"max_attempts": 4}))


# ------------------------------------------------------------------ listing -------------------

def list_objects(s3, bucket: str, prefix: str = PREFIX) -> list[dict]:
    out, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        out += page.get("Contents", [])
        if not page.get("IsTruncated"):
            return out
        token = page["NextContinuationToken"]


def _epoch(obj) -> float:
    lm = obj.get("LastModified")
    if isinstance(lm, datetime):
        return (lm if lm.tzinfo else lm.replace(tzinfo=timezone.utc)).timestamp()
    return 0.0


def group_sessions(objects: list[dict], device: str | None = None) -> list[dict]:
    """Group bucket objects into sessions (one per device + session id). Objects outside the
    stream folders are ignored."""
    sessions: dict[tuple, dict] = {}
    for o in objects:
        m = KEY_RE.match(o["Key"])
        if not m or (device and m["device"] != device):
            continue
        k = (m["device"], m["session"])
        s = sessions.setdefault(k, {"device": m["device"], "session": m["session"], "streams": {},
                                    "sync_key": None, "start_epoch": None, "length_s": None,
                                    "start_from": "upload time", "uploaded": 0.0})
        s["uploaded"] = max(s["uploaded"], _epoch(o))
        if m["stream"] == "sync":
            s["sync_key"] = o["Key"]
        else:
            s["streams"][m["stream"]] = {"key": o["Key"], "size": int(o["Size"])}
    return list(sessions.values())


def read_sync(s3, bucket: str, key: str) -> dict:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def sessions(s3, bucket: str, device: str | None = None) -> list[dict]:
    """All sessions, newest first. Start time and length come from sync.json when there is one;
    otherwise the start is unknown and the newest upload time is used for ordering."""
    out = group_sessions(list_objects(s3, bucket), device)
    for s in out:
        if s["sync_key"]:
            try:
                sync = read_sync(s3, bucket, s["sync_key"])
                s["sync"] = sync
                if sync.get("start_epoch_ms") is not None:
                    s["start_epoch"] = sync["start_epoch_ms"] / 1000.0
                    s["start_from"] = "sync.json"
                s["length_s"] = sync.get("length_s")
            except Exception as e:  # a broken sync file should not hide the session
                s["sync_error"] = str(e)
    out.sort(key=lambda s: s["start_epoch"] if s["start_epoch"] is not None else s["uploaded"], reverse=True)
    return out


def pick(found: list[dict], session: str | None = None, latest: bool = False) -> dict:
    if session:
        hits = [s for s in found if s["session"] == session]
        if not hits:
            raise PullError(f"no session {session} in the bucket (try --list)")
        if len(hits) > 1:
            raise PullError(f"session {session} exists on several devices; add --device")
        return hits[0]
    if latest:
        usable = [s for s in found if "usb" in s["streams"] or "ribbon" in s["streams"]]
        if not usable:
            raise PullError("no sessions with a video in the bucket")
        return usable[0]
    raise PullError("say which session: --latest or --session ID")


def when(s: dict) -> str:
    t = s["start_epoch"] if s["start_epoch"] is not None else s["uploaded"]
    text = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    return text if s["start_epoch"] is not None else text + " (upload)"


def format_list(found: list[dict]) -> str:
    rows = [f"{'device':<10} {'session':<8} {'start (local)':<26} {'length':>7}  streams"]
    for s in found:
        length = f"{s['length_s']:.1f}s" if s.get("length_s") else "?"
        streams = " ".join(f"{n}:{s['streams'][n]['size'] / 1e6:.1f}MB" for n in STREAMS if n in s["streams"])
        sync = " sync" if s["sync_key"] else ""
        rows.append(f"{s['device']:<10} {s['session']:<8} {when(s):<26} {length:>7}  {streams}{sync}")
    return "\n".join(rows)


# ------------------------------------------------------------------ download ------------------

def free_bytes(path: Path) -> int:
    p = path
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free


def pull(s3, bucket: str, s: dict, out: Path, max_mb: float = DEFAULT_MAX_MB, log=print) -> dict:
    """Download one session into out/<device>-<session>/ and return the local paths."""
    dest = out / f"{s['device']}-{s['session']}"
    todo = {n: s["streams"][n] for n in STREAMS if n in s["streams"]}
    if not todo:
        raise PullError(f"session {s['session']} has no video or audio files")
    need = sum(v["size"] for n, v in todo.items()
               if not ((dest / LOCAL_NAMES[n]).is_file() and (dest / LOCAL_NAMES[n]).stat().st_size == v["size"]))
    total = sum(v["size"] for v in todo.values())
    if total > max_mb * 1e6:
        raise PullError(f"session {s['session']} is {total / 1e6:.0f} MB, over the {max_mb:g} MB limit "
                        f"(raise it with --max-mb)")
    if need and free_bytes(out) < need + 1e9:
        raise PullError(f"not enough disk: need {need / 1e6:.0f} MB plus 1 GB spare")
    dest.mkdir(parents=True, exist_ok=True)
    paths, fetched = {}, 0
    for name, obj in todo.items():
        path = dest / LOCAL_NAMES[name]
        paths[name] = path
        if path.is_file() and path.stat().st_size == obj["size"]:
            log(f"  {name}: already here ({obj['size'] / 1e6:.1f} MB)")
            continue
        part = path.with_suffix(path.suffix + ".part")
        s3.download_file(bucket, obj["key"], str(part))
        got = part.stat().st_size
        if got != obj["size"]:
            part.unlink()
            raise PullError(f"{name}: got {got} bytes, R2 says {obj['size']}; try again")
        part.replace(path)
        fetched += got
        log(f"  {name}: {got / 1e6:.1f} MB")
    sync = dict(s.get("sync") or {})
    if not sync:
        sync = {"device": s["device"], "session": s["session"], "start_utc": None, "start_epoch_ms": None,
                "length_s": None, "files": {n: v["key"] for n, v in todo.items()},
                "note": "No sync.json was uploaded for this session; written by r2_pull. Start time unknown."}
    sync["local_files"] = {n: p.name for n, p in paths.items()}
    (dest / "sync.json").write_text(json.dumps(sync, indent=2) + "\n")
    paths["sync"] = dest / "sync.json"
    if s["start_epoch"] is not None:
        end = s["start_epoch"] + float(s.get("length_s") or 0)
        for n in ("usb", "ribbon", "audio"):
            if n in paths:
                os.utime(paths[n], (end, end))
    link = out / "current"  # always the last session pulled, so the live demo command never changes
    if link.is_symlink() or not link.exists():
        tmp = out / ".current-tmp"
        if tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(dest.name)
        tmp.replace(link)
    return {"dir": dest, "fetched_bytes": fetched, **paths}


def fetch(session: str | None = None, latest: bool = False, device: str | None = None,
          out: Path | None = None, max_mb: float = DEFAULT_MAX_MB, log=print) -> dict:
    """List the bucket, pick a session and download it. Used by render_demo --r2-latest."""
    s3 = make_client()
    bucket = bucket_name()
    found = sessions(s3, bucket, device)
    s = pick(found, session, latest)
    log(f"r2: session {s['session']} from {s['device']}, started {when(s)}")
    got = pull(s3, bucket, s, out or default_out(), max_mb, log)
    got["session"] = s
    return got


def default_out() -> Path:
    return Path(os.environ.get("BENCH_R2_CACHE") or Path.home() / ".cache" / "bench-coach" / "r2")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m scripts.r2_pull", description=__doc__.splitlines()[0])
    p.add_argument("--list", action="store_true", help="list sessions, newest first")
    p.add_argument("--latest", action="store_true", help="download the newest session with a video")
    p.add_argument("--session", help="download this session id (for example e259bf)")
    p.add_argument("--device", help="only this device (folder name under lab-cam/)")
    p.add_argument("--out", type=Path, help="download folder (default $BENCH_R2_CACHE or ~/.cache/bench-coach/r2)")
    p.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="refuse sessions bigger than this")
    a = p.parse_args(argv)
    try:
        if a.list:
            print(format_list(sessions(make_client(), bucket_name(), a.device)))
            return 0
        got = fetch(a.session, a.latest, a.device, a.out, a.max_mb)
        print(json.dumps({k: str(v) for k, v in got.items() if k not in ("session", "fetched_bytes")}))
        return 0
    except PullError as e:
        print(f"r2_pull: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
