"""Native allocations must be measured in an isolated process, not pytest's RSS."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import cv2


def test_ten_sample_recordings_memory(tmp_path):
    samples = os.environ.get('BENCH_SAMPLES')
    if not samples or not all((Path(samples) / 'lsv_mv' / f'CP-{side}-Front-414-445.mp4').is_file()
                              for side in ('Left', 'Right')):
        pytest.skip('set BENCH_SAMPLES to the sample video folder')
    result = subprocess.run([sys.executable, '-m', 'capture.leak_probe',
                             '--output', str(tmp_path), '--seconds', '3'],
                            capture_output=True, text=True, timeout=180, check=True)
    rows = [row for line in result.stdout.splitlines() if 'run' in (row := json.loads(line))]
    assert len(rows) == 10
    assert all(row['error'] is None and row['dropped'] == 0 for row in rows)
    # Allow native allocator warmup, but reject sustained per-recording retention.
    late = rows[2:]
    assert max(r['rss_mib'] for r in late) - min(r['rss_mib'] for r in late) < 24, rows
    assert rows[-1]['rss_mib'] - rows[0]['rss_mib'] < 32, rows
    assert max(r['tree_mib'] for r in late) - min(r['tree_mib'] for r in late) < 48, rows
    assert max(r['python_mib'] for r in late) - min(r['python_mib'] for r in late) < 8, rows
    for folder in sorted(tmp_path.glob('probe-*')):
        timing = [json.loads(line) for line in (folder / 'timing.jsonl').read_text().splitlines()]
        assert timing[-1]['kind'] == 'stop' and timing[-1]['samples'] > 0
        for cam, count in enumerate(timing[-1]['frames']):
            video = cv2.VideoCapture(str(folder / f'cam{cam}.mp4'))
            try:
                assert count > 0 and video.get(cv2.CAP_PROP_FRAME_COUNT) == count
                assert video.read()[0]
            finally:
                video.release()
    print(json.dumps(rows))
