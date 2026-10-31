"""One build or PDF render per process; exit releases native decoder/font caches."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading

# Bound native work even when different runs/export requests arrive together.
_slots = threading.BoundedSemaphore(1)


def run_worker(operation: str, payload: bytes, timeout: float = 180) -> bytes:
    with _slots:
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), operation],
                input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=True,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run kills and waits for its child before raising.
            raise RuntimeError(f"Note {operation} timed out") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Note {operation} worker failed") from exc
    return result.stdout


def main():
    # Absolute script entry works from any working directory without changing how
    # relative run/source/output paths are interpreted.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    operation = sys.argv[1]
    payload = sys.stdin.buffer.read()
    if operation == 'build':
        from note.service import _make_note
        result = _make_note(**json.loads(payload))
        sys.stdout.buffer.write(json.dumps(result).encode())
    elif operation == 'pdf':
        from note.export import _render_pdf
        sys.stdout.buffer.write(_render_pdf(payload.decode()))
    else:
        raise ValueError('unknown note operation')


if __name__ == '__main__':
    main()
