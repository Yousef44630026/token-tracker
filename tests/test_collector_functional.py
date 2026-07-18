"""Extra — collector functional behaviour (Phase 8, beyond fault injection).

Run: python tests/test_collector_functional.py

Exercises the happy paths and policies: record/flush, dedup by event_id, drop_oldest vs
drop_newest, offline_mode / no transport, partial-ack requeue, and the never-raise guarantee
on a non-serializable input.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.collector.client import CollectorClient, CollectorConfig  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


class Transport:
    """A fake transport that records what it was asked to send and how it acks."""

    def __init__(self, mode="all"):
        self.mode = mode
        self.batches = []

    def __call__(self, batch):
        ids = [e["event_id"] for e in batch]
        self.batches.append(ids)
        if self.mode == "raise":
            raise RuntimeError("transport down")
        if self.mode == "none":
            return []
        if self.mode == "first":
            return ids[:1]
        return ids  # all


def ev(i):
    return {"event_id": f"e{i}", "trace_id": "t", "payload": i}


# --- happy path: record then flush all ---
c = CollectorClient(Transport("all"))
check(all(c.record(ev(i)) for i in range(3)), "record accepts 3 events")
check(c.pending == 3, "pending == 3 before flush")
res = c.flush()
check(res.ok and res.sent == 3, "flush sends the whole batch (ok)")
check(c.pending == 0 and c.sent_total == 3, "buffer drained, sent_total == 3")

# --- dedup by event_id ---
c2 = CollectorClient(Transport("all"))
check(c2.record(ev(1)) is True, "first record accepted")
check(c2.record(ev(1)) is False, "duplicate event_id rejected")
check(c2.pending == 1, "duplicate not buffered twice")

# --- drop_oldest when full ---
c3 = CollectorClient(Transport("all"), CollectorConfig(max_buffer_size=2, drop_policy="drop_oldest"))
c3.record(ev(1))
c3.record(ev(2))
c3.record(ev(3))
c3.flush()
check(c3.dropped_total == 1, "drop_oldest dropped exactly one")
check("e1" not in c3._transport.batches[-1] and "e3" in c3._transport.batches[-1], "drop_oldest evicted e1, kept e3")

# --- drop_newest when full ---
c4 = CollectorClient(Transport("all"), CollectorConfig(max_buffer_size=2, drop_policy="drop_newest"))
c4.record(ev(1))
c4.record(ev(2))
c4.record(ev(3))
c4.flush()
check(c4.dropped_total == 1 and "e2" not in c4._transport.batches[-1], "drop_newest evicted the newest buffered (e2)")

# --- offline_mode: buffer only, never sent ---
c5 = CollectorClient(Transport("all"), CollectorConfig(offline_mode=True))
c5.record(ev(1))
r5 = c5.flush()
check(r5.ok is False and r5.reason == "offline", "offline_mode flush reports offline")
check(c5.pending == 1, "offline_mode keeps the buffer")

# --- no transport configured ---
c6 = CollectorClient(transport=None)
c6.record(ev(1))
check(c6.flush().reason == "offline", "no transport -> offline")

# --- partial ack: only un-acked are retried ---
c7 = CollectorClient(Transport("first"))
c7.record(ev(1))
c7.record(ev(2))
c7.record(ev(3))
r7 = c7.flush()
check(r7.ok is False and r7.sent == 1 and r7.retried == 2, "partial ack: 1 sent, 2 retried")
check(c7.pending == 2, "un-acked events remain buffered")

# --- transport raises: whole batch requeued, never raises ---
c8 = CollectorClient(Transport("raise"))
c8.record(ev(1))
r8 = c8.flush()
check(r8.ok is False and r8.reason == "error", "transport error surfaced as a failed flush")
check(c8.pending == 1, "failed flush keeps the batch for retry")

# --- never raises on a non-serializable input ---
c9 = CollectorClient(Transport("all"))
check(c9.record(42) is False, "non-serializable record rejected, not raised")
check(c9.dropped_total == 1, "non-serializable counted as dropped")

# --- dropped ids leave pending dedup and can be recorded again ------------------------
c10 = CollectorClient(Transport("all"), CollectorConfig(max_buffer_size=1))
c10.record(ev(1))
c10.record(ev(2))  # drops e1
check(c10.record(ev(1)) is True, "a dropped event_id can be recorded again")

# --- delivered dedup history is bounded -----------------------------------------------
c11 = CollectorClient(Transport("all"), CollectorConfig(dedup_history_size=2))
for i in range(3):
    c11.record(ev(i))
    c11.flush()
check(c11.record(ev(0)) is True, "expired delivered ids leave bounded dedup history")


# --- a transport cannot acknowledge ids outside its submitted batch ------------------
def bogus_ack(batch):
    return ["not-in-this-batch"]


c12 = CollectorClient(bogus_ack)
c12.record(ev(1))
r12 = c12.flush()
check(r12.sent == 0 and c12.pending == 1, "out-of-batch acknowledgements are ignored")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
