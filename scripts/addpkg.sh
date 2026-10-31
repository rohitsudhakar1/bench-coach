#!/bin/bash
# addpkg.sh <package...>: install into the shared environment one builder at a time.
# BENCH_PY points at the environment's python (default: python3 on PATH).
set -euo pipefail
LOCK="${TMPDIR:-/tmp}/bench-coach-pip.lock"
until mkdir "$LOCK" 2>/dev/null; do sleep 2; done
trap 'rmdir "$LOCK"' EXIT
uv pip install --python "${BENCH_PY:-python3}" "$@"
