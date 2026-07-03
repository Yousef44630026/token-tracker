"""Extra — concurrency: the collector under concurrent record() (Phase 8).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_concurrency_collector.py

Many threads recording into one collector (disjoint id ranges) must never crash, never lose
an event, and never inflate. Flushing is done by the main thread after the recorders join
(the realistic 'request threads record, a background worker flushes' split), so assertions
stay deterministic.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.collector.client import CollectorClient, CollectorConfig  # noqa: E402

_failures = 0
T = 16
M = 1000
TOTAL = T * M


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


class Transport:
    def __init__(self):
        self.sent = []
        self._lock = threading.Lock()

    def __call__(self, batch):
        ids = [e["event_id"] for e in batch]
        with self._lock:
            self.sent.extend(ids)
        return ids


transport = Transport()
collector = CollectorClient(transport, CollectorConfig(max_buffer_size=TOTAL + 10, batch_size=500, dedup_history_size=TOTAL + 10))

errors: list = []
accepted = [0] * T
barrier = threading.Barrier(T)


def recorder(tid: int) -> None:
    try:
        barrier.wait()
        cnt = 0
        for j in range(M):
            if collector.record({"event_id": f"e-{tid}-{j}", "trace_id": "t"}):
                cnt += 1
        accepted[tid] = cnt
    except Exception as exc:  # noqa: BLE001
        errors.append((tid, repr(exc)))


threads = [threading.Thread(target=recorder, args=(i,)) for i in range(T)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check(errors == [], f"no recorder thread raised (errors: {errors[:3]})")
check(sum(accepted) == TOTAL, f"all {TOTAL} unique events accepted under concurrent record (got {sum(accepted)})")
check(collector.pending == TOTAL, "every recorded event is in the buffer (nothing lost)")
check(collector.dropped_total == 0, "nothing dropped within capacity")

# drain on the main thread, then verify conservation + uniqueness
while collector.pending:
    collector.flush()
check(collector.sent_total == TOTAL, f"all {TOTAL} events delivered after drain")
check(len(transport.sent) == TOTAL and len(set(transport.sent)) == TOTAL, "every event delivered exactly once (no dup, no loss)")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
