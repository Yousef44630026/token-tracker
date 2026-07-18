"""Extra — CollectorConfig validation (fail fast on bad tunables).

Run: python tests/test_collector_config.py

The config validates at construction so a misconfigured collector is caught immediately rather
than misbehaving silently under load.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.collector.client import CollectorConfig  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def rejects(**kw):
    try:
        CollectorConfig(**kw)
        return False
    except ValueError:
        return True


# --- valid configs ---
check(CollectorConfig().drop_policy == "drop_oldest", "default config is valid")
check(CollectorConfig(drop_policy="drop_newest").drop_policy == "drop_newest", "drop_newest accepted")
check(CollectorConfig(dedup_history_size=0).dedup_history_size == 0, "dedup_history_size 0 accepted (no history)")
check(
    CollectorConfig(collector_timeout_ms=10, max_inflight_ms=10).max_inflight_ms == 10,
    "max_inflight_ms may equal the send timeout",
)
check(
    rejects(max_inflight_ms=1, collector_timeout_ms=2),
    "max_inflight_ms below collector timeout -> ValueError",
)

# --- invalid configs rejected at construction ---
check(rejects(max_buffer_size=0), "max_buffer_size 0 -> ValueError")
check(rejects(max_buffer_size=-1), "max_buffer_size negative -> ValueError")
check(rejects(batch_size=0), "batch_size 0 -> ValueError")
check(rejects(collector_timeout_ms=0), "collector_timeout_ms 0 -> ValueError")
check(rejects(collector_timeout_ms=-5), "collector_timeout_ms negative -> ValueError")
check(rejects(dedup_history_size=-1), "dedup_history_size negative -> ValueError")
check(rejects(drop_policy="drop_random"), "unknown drop_policy -> ValueError")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
