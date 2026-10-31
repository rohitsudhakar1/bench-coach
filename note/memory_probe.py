"""Ten distinct fake runs, current RSS after each build and optional PDF export."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil


def measure(root, count=10, pdf=False):
    from note.service import make_note, _locks
    from note.export import print_html, render_pdf

    process = psutil.Process()
    owned = []
    original = subprocess.Popen
    def tracked(*args, **kwargs):
        child = original(*args, **kwargs)
        owned.append(child)
        return child

    # Track owned PIDs directly: process enumeration is unavailable in some sandboxes.
    subprocess.Popen = tracked
    rows = []
    try:
        for i in range(count):
            folder = root / f'probe-{i:02d}'
            peak = [0]
            done = threading.Event()
            def sample():
                while not done.is_set():
                    rss = process.memory_info().rss
                    for child in list(owned):
                        try:
                            rss += psutil.Process(child.pid).memory_info().rss
                        except psutil.NoSuchProcess:
                            pass
                    peak[0] = max(peak[0], rss)
                    done.wait(.01)
            thread = threading.Thread(target=sample)
            thread.start()
            started = time.monotonic()
            try:
                result = make_note(folder, use_claude=False)
                frames = sum(len(p['frames']) for p in result['note']['problems'])
                assert frames == 6
                if pdf:
                    document = print_html(result['note'], folder)
                    content = render_pdf(document)
                    assert content.startswith(b'%PDF')
                    del document, content
                del result
            finally:
                done.set()
                thread.join()
            assert all(child.poll() is not None for child in owned)
            owned.clear()
            row = dict(run=i + 1, rss_mib=round(process.memory_info().rss / 2**20, 2),
                       peak_total_mib=round(peak[0] / 2**20, 2),
                       seconds=round(time.monotonic() - started, 2), frames=frames, locks=len(_locks))
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        subprocess.Popen = original
    return rows


def prepare(root, count, samples=False):
    # Fixture creation exits before measurement, so its encoder cannot warm the measured process.
    from note.fakerun import make
    for i in range(count):
        make(root / f'probe-{i:02d}', samples=samples)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--pdf', action='store_true')
    parser.add_argument('--samples', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare(args.root, args.count, args.samples)
    else:
        measure(args.root, args.count, args.pdf)
