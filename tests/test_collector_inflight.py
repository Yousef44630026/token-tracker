"""Extra — collector in-flight resolution: a timed-out send is NOT resent.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_collector_inflight.py

When a transport exceeds collector_timeout_ms, the collector records the send as in-flight and
moves on (without blocking). A later flush resolves the in-flight result and applies the acks
WITHOUT retransmitting the batch — so the transport sees each batch exactly once.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.collector.client import CollectorClient, CollectorConfig  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


class GatedTransport:
    """Blocks on a gate until released, recording every batch it is handed."""

    def __init__(self):
        self.gate = threading.Event()
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, batch):
        ids = [e["event_id"] for e in batch]
        with self._lock:
            self.calls.append(ids)
        self.gate.wait(3.0)
        return ids


transport = GatedTransport()
collector = CollectorClient(transport, CollectorConfig(collector_timeout_ms=100, batch_size=10, max_inflight_ms=5000))
for i in range(5):
    collector.record({"event_id": f"e{i}"})

# 1) first flush: transport blocks past the 100ms timeout -> in-flight, nothing acked yet
r1 = collector.flush()
check(r1.ok is False, "timed-out flush is not ok")
check(collector.pending == 5 and collector.sent_total == 0, "batch kept, nothing delivered while in-flight")
check(len(transport.calls) == 1, "transport was called once")

# 2) a flush while still in-flight does NOT resend
r2 = collector.flush()
check(len(transport.calls) == 1, "no resend while the send is still in-flight")

# 3) release the transport, then resolve: acks applied, no retransmission
transport.gate.set()
for _ in range(100):
    collector.flush()
    if collector.pending == 0:
        break
    time.sleep(0.01)

check(collector.pending == 0, "in-flight result resolves and drains the buffer")
check(collector.sent_total == 5, "all 5 events delivered after resolution")
check(len(transport.calls) == 1, "the batch was sent EXACTLY once (no duplicate on timeout)")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
