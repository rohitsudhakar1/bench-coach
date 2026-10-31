#!/bin/bash
# addpkg.sh <package...>: install into the shared environment one builder at a time.
set -euo pipefail
LOCK=/tmp/bench-coach-pip.lock
until mkdir "$LOCK" 2>/dev/null; do sleep 2; done
trap 'rmdir "$LOCK"' EXIT
uv pip install --python /Users/tyler/scienceclaw/.venv/bin/python "$@"
