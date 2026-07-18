#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${AI_TOKEN_TRACKER_PYTHON:-python3}

cd "$ROOT"
exec "$PYTHON_BIN" -m tracker.ops.doctor "$@"
