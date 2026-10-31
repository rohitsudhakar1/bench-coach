#!/bin/sh
# Make .venv in the repo and install every part's dependencies.
# Uses uv when it is installed, otherwise python3 -m venv and pip.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

if command -v uv >/dev/null 2>&1; then
  echo "Creating .venv with uv (Python 3.12)"
  [ -x "$PY" ] || uv venv --python 3.12 "$VENV"
  uv pip install --python "$PY" -r requirements.txt
else
  BASE=${PYTHON:-python3}
  if ! "$BASE" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 13) else 1)'; then
    echo "Need Python 3.10 to 3.12 (found $("$BASE" --version 2>&1)). Install uv or set PYTHON=/path/to/python3.12." >&2
    exit 1
  fi
  echo "Creating .venv with $("$BASE" --version 2>&1)"
  [ -x "$PY" ] || "$BASE" -m venv "$VENV"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements.txt
fi

if command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg found"
else
  echo "WARNING: ffmpeg is missing. The replay demo works without it, but recording and" >&2
  echo "speech input need it. Install it (macOS: brew install ffmpeg; Debian/Ubuntu: apt install ffmpeg)." >&2
fi

"$PY" -c 'import fastapi, uvicorn, httpx, websockets, yaml, numpy, cv2' \
  || { echo "Install finished but the core imports failed" >&2; exit 1; }

cat <<NEXT

Setup done. Next, from $ROOT:

  export BENCH_PY="\$PWD/.venv/bin/python"
  ./scripts/demo.sh --replay app/samples/demo.jsonl --without capture vision angle coach note

Then open http://127.0.0.1:8700 once it prints "Demo ready". Ctrl-C stops it.

Tests: .venv/bin/python -m pytest -q
Browser checks in app/tests also need: .venv/bin/python -m playwright install chromium
NEXT
