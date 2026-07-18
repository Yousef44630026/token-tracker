#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${AI_TOKEN_TRACKER_PYTHON:-python3}
STORE=${1:-codex_live.jsonl}
if [ "$#" -gt 0 ]; then
    shift
fi

cd "$ROOT"
exec "$PYTHON_BIN" -m tracker.proxy.cli report --store "$STORE" "$@"
