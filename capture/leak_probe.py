"""Isolated recording memory probe; sample paths come from BENCH_SAMPLES."""
import gc
import json
import os
from pathlib import Path
import time
import tracemalloc

import psutil
from capture.service import Capture
from capture import recording


def probe(root, seconds=3, count=10):
    samples = Path(os.environ['BENCH_SAMPLES']) / 'lsv_mv'
    sources = [str(samples / f'CP-{side}-Front-414-445.mp4') for side in ('Left', 'Right')]
    encoders = []
    original = recording.H264Writer
    def tracked_writer(*args):
        writer = original(*args)
        encoders.append(writer.process)
        return writer
    recording.H264Writer = tracked_writer
    capture = Capture(sources, root, fake_mic=True, loop=True)
    process = psutil.Process()
    tracemalloc.start()
    capture.start()
    rows = []
    try:
        deadline = time.monotonic() + 15
        while any(c.latest is None for c in capture.cameras):
            if time.monotonic() > deadline:
                raise RuntimeError('sample cameras did not start')
            time.sleep(.05)
        before = tracemalloc.take_snapshot()
        for index in range(count):
            capture.recorder.start(f'probe-{index}')
            time.sleep(seconds)
            active = process.memory_info().rss / 2**20
            tree = active + sum(psutil.Process(p.pid).memory_info().rss / 2**20
                                for p in encoders if p.poll() is None)
            result = capture.recorder.stop()
            assert all(p.poll() is not None for p in encoders), 'encoder process survived stop'
            encoders.clear()
            gc.collect()
            row = dict(run=index, active_mib=round(active, 2), tree_mib=round(tree, 2), rss_mib=round(process.memory_info().rss / 2**20, 2),
                       python_mib=round(tracemalloc.get_traced_memory()[0] / 2**20, 2),
                       dropped=result['dropped'], error=result['error'])
            rows.append(row)
            print(json.dumps(row), flush=True)
        after = tracemalloc.take_snapshot()
        # Basenames only: reports must not expose local paths.
        for stat in after.compare_to(before, 'lineno')[:10]:
            frame = stat.traceback[0]
            print(json.dumps(dict(file=Path(frame.filename).name, line=frame.lineno,
                                  bytes=stat.size_diff, count=stat.count_diff)), flush=True)
        return rows
    finally:
        capture.close()
        tracemalloc.stop()
        recording.H264Writer = original


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=3)
    args = parser.parse_args()
    probe(args.output, args.seconds)
