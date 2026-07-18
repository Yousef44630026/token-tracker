"""Extra — load: the collector under high volume (Phase 8).

Run: python tests/test_load_collector.py

Drains 20k events through batched flushes (nothing lost), then checks dedup-at-scale and the
bounded-buffer drop policy under a flood.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.collector.client import CollectorClient, CollectorConfig  # noqa: E402

_failures = 0
N = 20000


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


class Transport:
    def __call__(self, batch):
        return [e["event_id"] for e in batch]  # ack all


# --- drain 20k through batched flushes, nothing lost ---
c = CollectorClient(Transport(), CollectorConfig(max_buffer_size=N, batch_size=500, dedup_history_size=N))
t0 = time.perf_counter()
accepted = sum(1 for i in range(N) if c.record({"event_id": f"e{i}", "trace_id": "t"}))
check(accepted == N, f"all {N} events accepted")
check(c.pending == N, "all buffered before flushing")

flushes = 0
while c.pending and flushes < N:  # guard against an infinite loop
    c.flush()
    flushes += 1
drain_s = time.perf_counter() - t0
check(c.pending == 0, "buffer fully drained")
check(c.sent_total == N, f"sent_total == {N} (nothing lost)")
check(c.dropped_total == 0, "nothing dropped when within capacity")

# --- dedup at scale: re-recording delivered ids (within history) is rejected ---
redup = sum(1 for i in range(N) if c.record({"event_id": f"e{i}"}))
check(redup == 0, "re-recording delivered ids (within dedup history) all rejected")

# --- bounded buffer under a flood: drops counted, never crashes ---
small = CollectorClient(Transport(), CollectorConfig(max_buffer_size=100, drop_policy="drop_oldest"))
for i in range(1000):
    small.record({"event_id": f"x{i}"})
check(small.pending == 100, "bounded buffer caps at max_buffer_size")
check(small.dropped_total == 900, "the 900 overflow events are counted as dropped")

print(f"  timings: drain {N} in {drain_s:.2f}s ({flushes} flushes)")
print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
