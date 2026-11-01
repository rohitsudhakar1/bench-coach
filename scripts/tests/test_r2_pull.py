"""r2_pull against a fake S3 client: listing, grouping, picking, downloading, size checks and the
renderer glue. No network and no real credentials."""
import io
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import r2_pull as P

DAY = "2026/10/31"
START_MS = 1793478302766


def key(stream, hms, sess, ext, suffix=True):
    return f"lab-cam/campi/{stream}/{DAY}/{hms}_{sess}{'_' + stream if suffix else ''}.{ext}"


def sync_doc(sess, start_ms, length=30.2, streams=("usb", "ribbon", "audio"), hms="162502.766"):
    ext = {"usb": "mp4", "ribbon": "mp4", "audio": "m4a"}
    return {"device": "campi", "session": sess, "start_utc": "x", "start_epoch_ms": start_ms, "length_s": length,
            "fps": 30, "clock_synced_from_internet": True,
            "files": {s: key(s, hms, sess, ext[s]) for s in streams}}


class FakeS3:
    """Just the three calls r2_pull makes. Records every call so tests can check it never writes."""

    def __init__(self, objects: dict[str, bytes], page=2, short=()):
        self.objects, self.page, self.short, self.calls = objects, page, set(short), []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        self.calls.append("list")
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        i = int(ContinuationToken or 0)
        chunk = keys[i:i + self.page]
        out = {"Contents": [{"Key": k, "Size": len(self.objects[k]),
                             "LastModified": datetime(2026, 10, 31, 15, 0, n % 60, tzinfo=timezone.utc)}
                            for n, k in enumerate(chunk)],
               "IsTruncated": i + self.page < len(keys)}
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(i + self.page)
        return out

    def get_object(self, Bucket, Key):
        self.calls.append("get")
        return {"Body": io.BytesIO(self.objects[Key])}

    def download_file(self, Bucket, Key, Filename):
        self.calls.append("download")
        data = self.objects[Key]
        Path(Filename).write_bytes(data[:-1] if Key in self.short else data)


def bucket():
    new, old = "e259bf", "f442b4"
    objs = {
        key("usb", "162502.766", new, "mp4"): b"u" * 600,
        key("ribbon", "162502.766", new, "mp4"): b"r" * 1200,
        key("audio", "162502.766", new, "m4a"): b"a" * 30,
        key("sync", "162502.766", new, "json"): json.dumps(sync_doc(new, START_MS)).encode(),
        key("usb", "123414.235", old, "mp4"): b"u" * 900,
        key("ribbon", "123414.235", old, "mp4"): b"r" * 1100,
        key("sync", "123414.235", old, "json"): json.dumps(
            sync_doc(old, START_MS - 3 * 3600 * 1000, streams=("usb", "ribbon"), hms="123414.235")).encode(),
        # an early upload: no stream suffix, no sync file
        key("usb", "112409", "0d3abd", "mp4", suffix=False): b"u" * 100,
        key("ribbon", "112409", "0d3abd", "mp4", suffix=False): b"r" * 100,
        # outside the stream folders: ignored
        "lab-cam/campi/2026/10/26/201951_0a62a1.mp4": b"x" * 10,
    }
    return objs


def test_list_groups_sessions_newest_first_across_pages():
    s3 = FakeS3(bucket(), page=2)
    found = P.sessions(s3, "b")
    assert [s["session"] for s in found] == ["e259bf", "f442b4", "0d3abd"]
    new = found[0]
    assert set(new["streams"]) == {"usb", "ribbon", "audio"}
    assert new["start_epoch"] == START_MS / 1000 and new["length_s"] == 30.2
    assert found[2]["sync_key"] is None and found[2]["start_epoch"] is None
    text = P.format_list(found)
    assert "e259bf" in text and "audio:" in text and "(upload)" in text
    assert "0a62a1" not in text
    assert s3.calls.count("list") > 1  # paged


def test_device_filter():
    objs = bucket()
    objs["lab-cam/otherpi/usb/2026/10/31/170000.000_aaaaaa_usb.mp4"] = b"u"
    assert {s["device"] for s in P.sessions(FakeS3(objs), "b")} == {"campi", "otherpi"}
    assert {s["device"] for s in P.sessions(FakeS3(objs), "b", device="otherpi")} == {"otherpi"}


def test_pick():
    found = P.sessions(FakeS3(bucket()), "b")
    assert P.pick(found, latest=True)["session"] == "e259bf"
    assert P.pick(found, session="f442b4")["session"] == "f442b4"
    with pytest.raises(P.PullError, match="no session"):
        P.pick(found, session="nope")
    with pytest.raises(P.PullError, match="--latest"):
        P.pick(found)


def test_pull_downloads_checks_sizes_and_writes_sync(tmp_path):
    s3 = FakeS3(bucket())
    s = P.pick(P.sessions(s3, "b"), latest=True)
    got = P.pull(s3, "b", s, tmp_path, log=lambda *_: None)
    d = tmp_path / "campi-e259bf"
    assert got["dir"] == d
    assert (d / "usb.mp4").stat().st_size == 600 and (d / "ribbon.mp4").stat().st_size == 1200
    assert (d / "audio.m4a").stat().st_size == 30
    sync = json.loads((d / "sync.json").read_text())
    assert sync["session"] == "e259bf" and sync["local_files"]["usb"] == "usb.mp4"
    # mtime = real end of recording, so "mtime - length" gives the real start
    assert abs((d / "usb.mp4").stat().st_mtime - (START_MS / 1000 + 30.2)) < 1
    assert not list(d.glob("*.part"))
    assert (tmp_path / "current").resolve() == d.resolve()
    assert (tmp_path / "current" / "usb.mp4").stat().st_size == 600
    # second pull downloads nothing
    n = s3.calls.count("download")
    again = P.pull(s3, "b", s, tmp_path, log=lambda *_: None)
    assert s3.calls.count("download") == n and again["fetched_bytes"] == 0
    assert set(s3.calls) <= {"list", "get", "download"}


def test_short_download_is_refused(tmp_path):
    objs = bucket()
    s3 = FakeS3(objs, short={key("ribbon", "162502.766", "e259bf", "mp4")})
    s = P.pick(P.sessions(s3, "b"), latest=True)
    with pytest.raises(P.PullError, match="R2 says 1200"):
        P.pull(s3, "b", s, tmp_path, log=lambda *_: None)
    assert not (tmp_path / "campi-e259bf" / "ribbon.mp4").exists()
    assert not list((tmp_path / "campi-e259bf").glob("*.part"))


def test_size_limit(tmp_path):
    s3 = FakeS3(bucket())
    s = P.pick(P.sessions(s3, "b"), latest=True)
    with pytest.raises(P.PullError, match="over the"):
        P.pull(s3, "b", s, tmp_path, max_mb=0.001, log=lambda *_: None)
    assert "download" not in s3.calls


def test_session_without_sync_gets_a_written_one(tmp_path):
    s3 = FakeS3(bucket())
    s = P.pick(P.sessions(s3, "b"), session="0d3abd")
    P.pull(s3, "b", P.pick(P.sessions(s3, "b"), latest=True), tmp_path, log=lambda *_: None)
    P.pull(s3, "b", s, tmp_path, log=lambda *_: None)
    assert (tmp_path / "current").resolve().name == "campi-0d3abd"  # moves to the last pull
    sync = json.loads((tmp_path / "campi-0d3abd" / "sync.json").read_text())
    assert sync["start_epoch_ms"] is None and "No sync.json" in sync["note"]


def test_missing_env_is_a_clear_message(monkeypatch):
    for n in P.ENV:
        monkeypatch.delenv(n, raising=False)
    with pytest.raises(P.PullError, match="R2_ENDPOINT, R2_BUCKET"):
        P.make_client()


def test_missing_boto3_is_a_clear_message(monkeypatch):
    for n in P.ENV:
        monkeypatch.setenv(n, "x")
    import builtins
    real = builtins.__import__

    def no_boto(name, *a, **k):
        if name.startswith("boto3") or name.startswith("botocore"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_boto)
    with pytest.raises(P.PullError, match="addpkg.sh boto3"):
        P.make_client()


def test_cli_exit_code_without_env(monkeypatch, capsys):
    for n in P.ENV:
        monkeypatch.delenv(n, raising=False)
    assert P.main(["--list"]) == 2
    assert "missing environment variables" in capsys.readouterr().err


def test_render_demo_r2_glue_fills_views_and_audio(monkeypatch, tmp_path):
    from scripts import render_demo as R
    d = tmp_path / "campi-e259bf"
    d.mkdir()
    got = {n: d / f for n, f in P.LOCAL_NAMES.items()}
    monkeypatch.setattr(P, "fetch", lambda *a, **k: got)
    a = R.parse(["--r2-latest", "--protocol", "p.txt", "--out", "o.mp4"])
    R.from_r2(a)
    assert a.video == got["usb"] and a.video2 == got["ribbon"] and a.audio == got["audio"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_mux_audio_starts_after_title_card(tmp_path):
    from scripts import render_demo as R
    vid, aud = tmp_path / "v.mp4", tmp_path / "a.m4a"
    run = lambda *c: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *c], check=True)
    run("-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid))
    run("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2", "-c:a", "aac", str(aud))
    R.mux_audio(vid, aud)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                          str(vid)], capture_output=True, text=True, check=True).stdout
    streams = {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}
    assert abs(streams["video"] - 6.0) < 0.1
    assert abs(streams["audio"] - (R.TITLE_SECONDS + 2.0)) < 0.2
