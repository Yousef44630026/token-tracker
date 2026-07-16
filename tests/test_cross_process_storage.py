"""Deterministic cross-process locking and idempotency checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage._locking import lock_for  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402


def event(event_id: str) -> TokenEvent:
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=f"request-{event_id}",
        trace_id="trace-cross-process",
        span_id="span",
        quantities=[
            TokenQuantity(
                TokenType.OUTPUT,
                1,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        provider_total_tokens=1,
        observation={"authoritative": True},
    )


def wait_for(path: str, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.01)
    return False


def worker() -> int:
    mode = sys.argv[1]
    if mode == "hold_then_append":
        store, ready, release, result = sys.argv[2:]
        repository = FileRepository(store)
        with lock_for(store):
            Path(ready).write_text("ready", encoding="utf-8")
            if not wait_for(release, timeout_seconds=60.0):
                return 2
            appended = repository.append_unique([event("shared")])
            Path(result).write_text(json.dumps(appended), encoding="utf-8")
        return 0
    if mode == "append":
        store, started, result = sys.argv[2:]
        Path(started).write_text("started", encoding="utf-8")
        appended = FileRepository(store).append_unique([event("shared")])
        Path(result).write_text(json.dumps(appended), encoding="utf-8")
        return 0
    if mode == "timeout":
        store, started, result = sys.argv[2:]
        Path(started).write_text("started", encoding="utf-8")
        try:
            lock_for(store).acquire(timeout_seconds=0.2)
        except TimeoutError as exc:
            Path(result).write_text(str(exc), encoding="utf-8")
            return 0
        return 3
    if mode == "crash_with_lock":
        store, ready, release = sys.argv[2:]
        lock_for(store).acquire()
        Path(ready).write_text("ready", encoding="utf-8")
        if not wait_for(release, timeout_seconds=60.0):
            return 4
        os._exit(0)
    return 5


if len(sys.argv) > 1:
    raise SystemExit(worker())


_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


work = os.path.abspath(f".test_cross_process_storage_{uuid.uuid4().hex}")
os.makedirs(work, exist_ok=True)
store = os.path.join(work, "events.jsonl")
holder_ready = os.path.join(work, "holder.ready")
holder_release = os.path.join(work, "holder.release")
holder_result = os.path.join(work, "holder.result")
contender_started = os.path.join(work, "contender.started")
contender_result = os.path.join(work, "contender.result")

holder = subprocess.Popen(
    [sys.executable, __file__, "hold_then_append", store, holder_ready, holder_release, holder_result]
)
check(wait_for(holder_ready), "first process acquires the repository lock")

contender = subprocess.Popen(
    [sys.executable, __file__, "append", store, contender_started, contender_result]
)
check(wait_for(contender_started), "second process starts its append attempt")
time.sleep(0.3)
check(contender.poll() is None, "second process blocks while the first process owns the lock")

timeout_started = os.path.join(work, "timeout.started")
timeout_result = os.path.join(work, "timeout.result")
timed = subprocess.run(
    [sys.executable, __file__, "timeout", store, timeout_started, timeout_result],
    check=False,
    timeout=30,
)
check(timed.returncode == 0, "contended lock times out with a controlled result")
timeout_message = Path(timeout_result).read_text(encoding="utf-8") if os.path.exists(timeout_result) else ""
check(
    "timed out after 0.200s" in timeout_message
    and os.path.normcase(os.path.abspath(store)) in os.path.normcase(timeout_message),
    "timeout error identifies the lock and wait",
)

Path(holder_release).write_text("release", encoding="utf-8")
holder_exit = holder.wait(timeout=30)
contender_exit = contender.wait(timeout=30)
check(holder_exit == 0 and contender_exit == 0, "both repository processes finish cleanly")
holder_ids = json.loads(Path(holder_result).read_text(encoding="utf-8"))
contender_ids = json.loads(Path(contender_result).read_text(encoding="utf-8"))
check(holder_ids == ["shared"] and contender_ids == [], "cross-process append_unique has one deterministic winner")
check([item.event_id for item in FileRepository(store).read_all()] == ["shared"], "cross-process dedup persists exactly one event")

# Kernel ownership is released when a process dies; the persistent sidecar is not a stale lock.
crash_ready = os.path.join(work, "crash.ready")
crash_release = os.path.join(work, "crash.release")
crasher = subprocess.Popen([sys.executable, __file__, "crash_with_lock", store, crash_ready, crash_release])
check(wait_for(crash_ready), "crash worker acquires the repository lock")
Path(crash_release).write_text("exit", encoding="utf-8")
check(crasher.wait(timeout=30) == 0, "crash worker exits without an explicit unlock")
lock_for(store).acquire(timeout_seconds=1.0)
lock_for(store).release()
check(os.path.exists(f"{os.path.abspath(store)}.lock"), "persistent sidecar does not create stale ownership after process exit")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if _failures else 0)
