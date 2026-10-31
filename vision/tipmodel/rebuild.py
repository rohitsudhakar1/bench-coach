"""Rebuild the tip model weights from the sample videos (needs BENCH_SAMPLES), in short pieces.

    $BENCH_PY -m vision.tipmodel.rebuild

Auto-labels the extra clips in 150 s pieces, builds the image set, then trains in 8 minute pieces,
waiting while the machine is short of memory. About 10 minutes for auto-labels and 15 for training
on an M-series Mac. Ends with vision/models/tipmodel/tipmodel.pt.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time

from .dataset import OUT


def memory_free() -> int | None:
    try:
        out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return None
    m = re.search(r"(\d+)%", out)
    return int(m.group(1)) if m else None


def piece(args: list) -> str:
    while (free := memory_free()) is not None and free < 40:
        print(f"memory {free}% free; waiting", flush=True)
        time.sleep(30)
    r = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True)
    lines = [x for x in r.stdout.splitlines() if x.strip()]
    print("\n".join(lines[-3:]), flush=True)
    if r.returncode:
        raise SystemExit(r.stderr[-2000:])
    return r.stdout


def main():
    while "budget used" in piece(["vision.tipmodel.dataset", "autolabel", "--budget", "150"]):
        pass
    piece(["vision.tipmodel.dataset", "build"])
    first = True
    while "time used" in piece(["vision.tipmodel.train", "--minutes", "8"] + (["--fresh"] if first else [])):
        first = False
    print(f"done: {OUT / 'tipmodel.pt'}")


if __name__ == "__main__":
    main()
