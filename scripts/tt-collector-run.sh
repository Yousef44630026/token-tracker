#!/usr/bin/env sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${AI_TOKEN_TRACKER_PYTHON:-python3}

: "${TRACKER_STORE:=$ROOT/collector_events.jsonl}"
: "${TRACKER_HOST:=127.0.0.1}"
: "${TRACKER_PORT:=8787}"
: "${TRACKER_DURABLE:=true}"
: "${TRACKER_RESTART_DELAY_SECONDS:=10}"
export TRACKER_STORE TRACKER_HOST TRACKER_PORT TRACKER_DURABLE PYTHONUNBUFFERED=1

child_pid=""
stop_collector() {
    if [ -n "$child_pid" ]; then
        kill "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
}
trap stop_collector INT TERM HUP

cd "$ROOT"
while :; do
    "$PYTHON_BIN" -m api.main "$@" &
    child_pid=$!
    wait "$child_pid"
    code=$?
    child_pid=""
    printf '%s\n' "collector exited with code $code; restarting in ${TRACKER_RESTART_DELAY_SECONDS}s" >&2
    sleep "$TRACKER_RESTART_DELAY_SECONDS"
done
