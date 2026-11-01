"""Make phone videos safe for the rest of the pipeline.

iPhone footage is usually HEVC in a .mov, often 4K or 60 fps, sometimes variable frame rate,
stored sideways with a rotation tag, and often HDR (HLG or Dolby Vision 8.4, 10-bit). The
readers in vision, capture and render_demo use OpenCV and count frames at a fixed rate, so
any of that can give sideways boxes, drifting timestamps, slow 4K decoding or washed-out
colour.

prepare(path) checks the file with ffprobe. A plain H.264 file of at most 1920x1080 and
30 fps with no rotation and no HDR is returned unchanged. Anything else is converted once
with ffmpeg to H.264, upright, constant 30 fps, at most 1920x1080, SDR BT.709, and saved next
to the input as <name>.bench.mp4 (or in a cache folder if that folder is read-only). Later
runs reuse it while it is newer than the input.

HDR is tone-mapped with a 3D lookup table this module writes itself (HLG or PQ, BT.2020 to
BT.709), so it only needs ffmpeg's built-in lut3d filter, not zscale or libplacebo.

    python scripts/phonevid.py phone.mov        # convert and print the new path
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

import numpy as np

MAX_W, MAX_H, FPS = 1920, 1080, 30
SUFFIX = ".bench.mp4"
HDR_TRANSFERS = {"arib-std-b67": "hlg", "smpte2084": "pq"}
LUT_SIZE = 33
SDR_WHITE = 300.0  # nits shown as SDR white. BT.2408 says 203, but iPhone HLG sits brighter: at 203
                   # paper and sticky notes clip to white; 300 kept the writing on a real iPhone clip.
HLG_PEAK = 1000.0


def _rate(text):
    try:
        r = Fraction(text)
        return float(r) if r > 0 else 0.0
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


def probe(path):
    """What the first video stream looks like, in the terms prepare() cares about."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                          "-of", "json", str(path)], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path}: {out.stderr.strip()[:200]}")
    streams = json.loads(out.stdout).get("streams") or []
    if not streams:
        raise RuntimeError(f"no video stream in {path}")
    s = streams[0]
    rotation = 0
    for side in s.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(round(float(side["rotation"])))
    if not rotation and "rotate" in (s.get("tags") or {}):
        rotation = int(s["tags"]["rotate"])
    rotation %= 360
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    if rotation in (90, 270):
        w, h = h, w
    pix = s.get("pix_fmt") or ""
    return {
        "codec": s.get("codec_name") or "?",
        "width": w, "height": h,  # as displayed, after rotation
        "rotation": rotation,
        "fps": _rate(s.get("r_frame_rate")),
        "avg_fps": _rate(s.get("avg_frame_rate")),
        "pix_fmt": pix,
        "ten_bit": "10" in pix or "12" in pix,
        "hdr": HDR_TRANSFERS.get(s.get("color_transfer") or ""),
        "dolby_vision": any("DOVI" in (d.get("side_data_type") or "") for d in s.get("side_data_list") or []),
    }


def reasons(info):
    """Why a file needs converting; an empty list means it is fine as it is."""
    why = []
    if info["codec"] != "h264":
        why.append(info["codec"].upper())
    long_side, short_side = max(info["width"], info["height"]), min(info["width"], info["height"])
    if long_side > MAX_W or short_side > MAX_H:
        why.append(f"{info['width']}x{info['height']}")
    if info["rotation"]:
        why.append(f"rotated {info['rotation']}")
    if info["fps"] > FPS + 1 or info["avg_fps"] > FPS + 1:
        why.append(f"{max(info['fps'], info['avg_fps']):.0f} fps")
    if info["fps"] and info["avg_fps"] and abs(info["fps"] - info["avg_fps"]) > 0.01 * info["fps"]:
        why.append("variable frame rate")
    if info["hdr"]:
        why.append("Dolby Vision HDR" if info["dolby_vision"] else f"HDR ({info['hdr'].upper()})")
    elif info["ten_bit"]:
        why.append("10-bit")
    return why


# HDR -> SDR lookup table -----------------------------------------------------------------

_A, _B, _C = 0.17883277, 0.28466892, 0.55991073
BT2020_TO_709 = np.array([[1.6605, -0.5876, -0.0728],
                          [-0.1246, 1.1329, -0.0083],
                          [-0.0182, -0.1006, 1.1187]])
Y2020 = np.array([0.2627, 0.6780, 0.0593])


def hlg_inverse_oetf(v):
    v = np.clip(v, 0.0, 1.0)
    return np.where(v <= 0.5, v * v / 3.0, (np.exp((v - _C) / _A) + _B) / 12.0)


def hlg_oetf(e):
    e = np.clip(e, 0.0, 1.0)
    return np.where(e <= 1 / 12, np.sqrt(3 * e), _A * np.log(np.maximum(12 * e - _B, 1e-12)) + _C)


def pq_eotf(v):
    m1, m2, c1, c2, c3 = 0.1593017578125, 78.84375, 0.8359375, 18.8515625, 18.6875
    p = np.power(np.clip(v, 0.0, 1.0), 1 / m2)
    return 10000.0 * np.power(np.maximum(p - c1, 0) / (c2 - c3 * p), 1 / m1)


def _gamma(peak):
    return 1.2 + 0.42 * np.log10(peak / 1000.0)


def hdr_to_sdr(rgb, kind):
    """Nonlinear BT.2020 HLG/PQ RGB in 0..1 -> nonlinear BT.709 SDR RGB in 0..1 (gamma 2.4)."""
    if kind == "hlg":
        scene = hlg_inverse_oetf(rgb)
        ys = np.maximum(scene @ Y2020, 1e-9)[..., None]
        nits = HLG_PEAK * np.power(ys, _gamma(HLG_PEAK) - 1) * scene
        peak = HLG_PEAK
    else:
        nits = pq_eotf(rgb)
        peak = 1000.0
    lin = nits / SDR_WHITE  # 1.0 = SDR white
    # Highlights above half of white roll off smoothly towards the peak (extended Reinhard on luminance,
    # which keeps the hue); everything below that is left alone.
    y = np.maximum(lin @ Y2020, 1e-9)
    knee, top = 0.5, peak / SDR_WHITE
    room, w = 1.0 - knee, (top - knee) / (1.0 - knee)
    u = np.maximum(y - knee, 0) / room
    mapped = np.where(y > knee, knee + room * u * (1 + u / (w * w)) / (1 + u), y)
    mapped = np.minimum(mapped, 1.0)
    lin = lin * (mapped / y)[..., None]
    lin = np.clip(lin @ BT2020_TO_709.T, 0.0, 1.0)
    return np.power(lin, 1 / 2.4)


def sdr_to_hlg(rgb):
    """Nonlinear BT.709 SDR RGB -> nonlinear BT.2020 HLG RGB (used to make test clips)."""
    lin = np.power(np.clip(rgb, 0, 1), 2.4) @ np.linalg.inv(BT2020_TO_709).T
    lin = np.clip(lin, 0, None)
    nits = lin * SDR_WHITE
    g = _gamma(HLG_PEAK)
    yd = np.maximum(nits @ Y2020, 1e-9)[..., None]
    scene = (nits / HLG_PEAK) * np.power(yd / HLG_PEAK, (1 - g) / g)
    return hlg_oetf(scene)


def write_cube(path, fn, size=LUT_SIZE):
    """A .cube 3D LUT for ffmpeg's lut3d filter; red varies fastest."""
    axis = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    out = fn(np.stack([r, g, b], axis=-1).reshape(-1, 3))
    lines = [f"LUT_3D_SIZE {size}"] + [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out]
    Path(path).write_text("\n".join(lines) + "\n")
    return path


# Conversion --------------------------------------------------------------------------------

def _cache_dir():
    base = os.environ.get("BENCH_PHONEVID_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "bench-coach", "phonevid")
    Path(base).mkdir(parents=True, exist_ok=True)
    return Path(base)


def target_for(path):
    path = Path(path)
    if os.access(path.parent, os.W_OK):
        return path.with_name(path.stem + SUFFIX)
    return _cache_dir() / (path.stem + f"-{abs(hash(str(path.resolve()))) % 10**8}" + SUFFIX)


def _encoder():
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "libx264" if "libx264" in enc else "h264_videotoolbox"


def command(src, dst, info, lut=None):
    chain = []
    if info["hdr"] and lut:
        # swscale only does the YUV->RGB matrix; the LUT does transfer, tone map and gamut.
        chain += ["scale=in_color_matrix=bt2020:in_range=tv", "format=gbrp16le",
                  f"lut3d=file='{lut}':interp=tetrahedral",
                  "scale=out_color_matrix=bt709:out_range=tv"]
    chain += [f"fps={FPS}",
              f"scale=w='if(gt(iw,ih),min(iw,{MAX_W}),min(iw,{MAX_H}))':h='if(gt(iw,ih),min(ih,{MAX_H}),min(ih,{MAX_W}))'"
              ":force_original_aspect_ratio=decrease:force_divisible_by=2",
              "setsar=1", "format=yuv420p",
              "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"]
    enc = _encoder()
    venc = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"] if enc == "libx264"
            else ["-c:v", "h264_videotoolbox", "-b:v", "10M"])
    # ffmpeg applies the rotation tag itself (autorotate is on by default) and drops the tag.
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(chain), "-fps_mode", "cfr",
            *venc, "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]


def prepare(path, quiet=False, force=False):
    """Return a path the pipeline can read: the input itself, or its converted copy."""
    path = Path(path)
    if path.name.endswith(SUFFIX) or "://" in str(path) or not path.is_file():
        return path
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        if not quiet:
            print(f"phone video: ffmpeg not found, using {path.name} as it is", file=sys.stderr)
        return path
    try:
        info = probe(path)
    except (RuntimeError, ValueError) as exc:
        if not quiet:
            print(f"phone video: cannot check {path.name} ({str(exc)[:80]}), using it as it is", file=sys.stderr)
        return path
    why = reasons(info)
    if not why and not force:
        return path
    dst = target_for(path)
    say = (lambda *a: None) if quiet else (lambda m: print(m, file=sys.stderr, flush=True))
    if dst.is_file() and dst.stat().st_mtime >= path.stat().st_mtime and dst.stat().st_size > 0:
        say(f"phone video: reusing {dst.name} ({', '.join(why)} -> 1080p30 H.264 upright SDR)")
        return dst
    say(f"phone video: {path.name} is {', '.join(why)}; converting to 1080p30 H.264 upright SDR ...")
    t = time.time()
    tmp = dst.with_name(dst.name[:-len(SUFFIX)] + ".part" + SUFFIX)
    lut = None
    try:
        if info["hdr"]:
            lut = tmp.with_suffix(".cube")
            write_cube(lut, lambda rgb: hdr_to_sdr(rgb, info["hdr"]))
        run = subprocess.run(command(path, tmp, info, lut), capture_output=True, text=True)
        if run.returncode != 0:
            raise RuntimeError(f"ffmpeg could not convert {path.name}: {run.stderr.strip()[-400:]}")
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
        if lut:
            Path(lut).unlink(missing_ok=True)
    say(f"phone video: wrote {dst.name} in {time.time() - t:.1f}s")
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: phonevid.py VIDEO [VIDEO ...]")
    for arg in sys.argv[1:]:
        print(prepare(arg))
