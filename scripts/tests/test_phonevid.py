"""Phone-style clips are made with ffmpeg from a small test pattern, then converted."""
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts import phonevid

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")
Q = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]


def hevc():
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "libx265" in enc:
        return ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error", "-tag:v", "hvc1"]
    if "hevc_videotoolbox" in enc:
        return ["-c:v", "hevc_videotoolbox", "-q:v", "60", "-tag:v", "hvc1"]
    pytest.skip("no HEVC encoder")


def pattern(w=320, h=180, seconds=2, rate=30):
    # Asymmetric picture so a wrong rotation or mirror is caught.
    return ["-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={rate}:duration={seconds}"]


def frame(path, index=0):
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, image = cap.read()
    cap.release()
    assert ok
    return image.astype(float)


def info(path):
    out = phonevid.probe(path)
    out["frames"] = int(cv2.VideoCapture(str(path)).get(cv2.CAP_PROP_FRAME_COUNT))
    return out


@pytest.fixture
def ref(tmp_path):
    path = tmp_path / "ref.mp4"
    subprocess.run([*Q, *pattern(), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_plain_h264_is_left_alone(ref):
    assert phonevid.reasons(phonevid.probe(ref)) == []
    assert phonevid.prepare(ref, quiet=True) == ref
    assert not ref.with_name("ref.bench.mp4").exists()


def test_hevc_mov_becomes_h264_and_is_cached(tmp_path, ref):
    src = tmp_path / "phone.mov"
    subprocess.run([*Q, *pattern(), *hevc(), str(src)], check=True)
    out = phonevid.prepare(src, quiet=True)
    assert out == tmp_path / "phone.bench.mp4"
    got = info(out)
    assert (got["codec"], got["width"], got["height"], got["frames"]) == ("h264", 320, 180, 60)
    assert np.abs(frame(out, 30) - frame(ref, 30)).mean() < 6
    mtime = out.stat().st_mtime
    assert phonevid.prepare(src, quiet=True) == out and out.stat().st_mtime == mtime


@pytest.mark.parametrize("stored,tag", [("transpose=1", 90), ("transpose=2", -90), ("hflip,vflip", 180)])
def test_rotation_tag_is_applied(tmp_path, ref, stored, tag):
    raw, src = tmp_path / "raw.mov", tmp_path / "rot.mov"
    subprocess.run([*Q, *pattern(), "-vf", stored, *hevc(), str(raw)], check=True)
    subprocess.run([*Q, "-display_rotation", str(tag), "-i", str(raw), "-c", "copy", str(src)], check=True)
    assert phonevid.probe(src)["rotation"]
    out = phonevid.prepare(src, quiet=True)
    got = info(out)
    assert (got["width"], got["height"], got["rotation"]) == (320, 180, 0)
    good = np.abs(frame(out, 10) - frame(ref, 10)).mean()
    flipped = np.abs(frame(out, 10) - frame(ref, 10)[::-1, ::-1]).mean()
    assert good < 6 and good * 3 < flipped


def test_60fps_becomes_30_with_same_timing(tmp_path, ref):
    src = tmp_path / "p60.mov"
    subprocess.run([*Q, *pattern(rate=60), *hevc(), str(src)], check=True)
    out = phonevid.prepare(src, quiet=True)
    got = info(out)
    assert got["fps"] == 30 and got["frames"] == 60
    for i in (15, 45):
        assert np.abs(frame(out, i) - frame(ref, i)).mean() < 6


def test_variable_frame_rate_keeps_timestamps(tmp_path, ref):
    # Drop roughly every third frame but keep the original timestamps (as a phone does when it
    # stalls); frame N of the output must still show time N/30.
    src = tmp_path / "vfr.mov"
    subprocess.run([*Q, *pattern(), "-vf", "select='not(eq(mod(n\\,3)\\,1))'", "-fps_mode", "vfr",
                    *hevc(), str(src)], check=True)
    assert "variable frame rate" in phonevid.reasons(phonevid.probe(src))
    out = phonevid.prepare(src, quiet=True)
    got = info(out)
    assert got["fps"] == got["avg_fps"] == 30 and abs(got["frames"] - 60) <= 1
    for i in (30, 51):  # frames that survived the drop
        d = [np.abs(frame(out, i) - frame(ref, j)).mean() for j in (i - 1, i, i + 1)]
        assert int(np.argmin(d)) == 1


def test_4k_is_downscaled(tmp_path):
    src = tmp_path / "uhd.mov"
    subprocess.run([*Q, *pattern(3840, 2160, seconds=0.3), *hevc(), str(src)], check=True)
    got = info(phonevid.prepare(src, quiet=True))
    assert (got["width"], got["height"]) == (1920, 1080)


def test_portrait_4k_stays_portrait(tmp_path):
    src = tmp_path / "tall.mov"
    subprocess.run([*Q, *pattern(2160, 3840, seconds=0.2), *hevc(), str(src)], check=True)
    got = info(phonevid.prepare(src, quiet=True))
    assert (got["width"], got["height"]) == (1080, 1920)


def test_hlg_hdr_is_tone_mapped_not_washed_out(tmp_path, ref):
    lut = tmp_path / "to_hlg.cube"
    phonevid.write_cube(lut, phonevid.sdr_to_hlg, size=17)
    src = tmp_path / "hlg.mov"
    subprocess.run([*Q, "-i", str(ref), "-vf",
                    f"scale=in_color_matrix=bt709,format=gbrp16le,lut3d=file={lut},"
                    "scale=out_color_matrix=bt2020:out_range=tv,format=yuv420p10le,"
                    "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
                    *hevc(), str(src)], check=True)
    probed = phonevid.probe(src)
    assert probed["hdr"] == "hlg" and probed["ten_bit"]
    out = phonevid.prepare(src, quiet=True)
    assert phonevid.probe(out)["hdr"] is None
    mapped = np.abs(frame(out, 20) - frame(ref, 20)).mean()
    naive = np.abs(frame(src, 20) - frame(ref, 20)).mean()
    # The test pattern is brighter and more saturated than bench footage, so its top end is
    # rolled off (about 18 levels off); reading the HLG file as if it were SDR is about 60 off.
    assert mapped < 25 and mapped * 3 < naive


def test_hdr_curve_white_and_black():
    rgb = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.5, 0.5, 0.5]])
    out = phonevid.hdr_to_sdr(rgb, "hlg")
    assert out[0].max() < 0.01 and out[1].min() > 0.98
    assert np.allclose(phonevid.hdr_to_sdr(phonevid.sdr_to_hlg(np.array([[0.3, 0.4, 0.2]])), "hlg"),
                       [[0.3, 0.4, 0.2]], atol=0.02)
    pq = phonevid.hdr_to_sdr(np.array([[0.58, 0.58, 0.58]]), "pq")  # about 203 nits
    assert 0.6 < pq[0, 0] < 0.95


def test_unreadable_file_is_passed_through(tmp_path):
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"not a video")
    assert phonevid.prepare(bad, quiet=True) == bad


def test_samples_phone_style(tmp_path):
    import os
    root = os.environ.get("BENCH_SAMPLES")
    sample = Path(root or "") / "lsv_mv" / "CP-Left-Front-414-445.mp4"
    if not root or not sample.is_file():
        pytest.skip("BENCH_SAMPLES not set")
    src = tmp_path / "sample.mov"
    subprocess.run([*Q, "-t", "2", "-i", str(sample), "-vf", "scale=1920:1080", "-r", "60", *hevc(), "-an",
                    str(src)], check=True)
    got = info(phonevid.prepare(src, quiet=True))
    assert (got["codec"], got["fps"], got["frames"]) == ("h264", 30, 60)
