"""Phase 8 — safe-failure collector under fault injection.

Run: python tests/test_collector_fault_injection.py

The collector is non-blocking and fail-safe: a tracker/transport failure must NEVER raise
into the caller. Covers: collector down, slow (timeout), buffer full (drop policy), network
failure, process killed mid-flush, duplicate send (dedup), partial batch failure, recovery.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.collector.client import CollectorClient, CollectorConfig  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def ev(i):
    return {"event_id": f"evt-{i}", "payload": i}


# --- collector totally down: record + flush never raise --------------------------------
def bomb(batch):
    raise ConnectionError("collector down")


c = CollectorClient(transport=bomb, config=CollectorConfig(batch_size=10))
raised = False
try:
    for i in range(5):
        c.record(ev(i))
    res = c.flush()
except Exception:  # noqa: BLE001
    raised = True
check(not raised, "collector down: record + flush never raise into the caller")
check(res.ok is False, "collector down: flush reports failure (not success)")
check(c.pending == 5, "collector down: the 5 events are kept (requeued), none lost")
check(c.sent_total == 0, "collector down: nothing was acked")

# --- recovery: a working transport drains the buffer -----------------------------------
sent_box = []


def good(batch):
    ids = [e["event_id"] for e in batch]
    sent_box.extend(ids)
    return ids  # ack everything


c._transport = good
res = c.flush()
check(res.ok is True, "recovery: flush succeeds once transport is back")
check(c.pending == 0, "recovery: buffer drained after a good flush")
check(c.sent_total == 5 and len(sent_box) == 5, "recovery: all 5 events delivered")

# --- buffer full: drop policy, bounded memory, no raise --------------------------------
c2 = CollectorClient(transport=bomb, config=CollectorConfig(max_buffer_size=3, batch_size=10))
for i in range(10):
    c2.record(ev(i))
check(c2.pending <= 3, f"buffer full: pending bounded by max_buffer_size (got {c2.pending})")
check(c2.dropped_total >= 7, f"buffer full: overflow events counted as dropped (got {c2.dropped_total})")


# --- slow transport: timeout is enforced, caller not blocked indefinitely --------------
def slow(batch):
    time.sleep(0.5)
    return [e["event_id"] for e in batch]


c3 = CollectorClient(transport=slow, config=CollectorConfig(collector_timeout_ms=100, batch_size=10))
for i in range(3):
    c3.record(ev(i))
start = time.time()
res = c3.flush()
elapsed = time.time() - start
check(elapsed < 0.45, f"slow: flush returns near the timeout, not after the full delay (elapsed={elapsed:.2f}s)")
check(res.ok is False, "slow: a timed-out flush is reported as failure")
check(c3.pending == 3, "slow: timed-out events are kept for retry, not lost")
time.sleep(0.45)
res = c3.flush()
check(res.ok is True and c3.pending == 0, "slow: late acknowledgement resolves without resend")


# --- permanently stuck first send expires: NEVER resend while the zombie worker might
# still be delivering it, but still apply its late ack once it truly finishes -----------
class FirstCallSticks:
    def __init__(self):
        self.calls = 0

    def __call__(self, batch):
        self.calls += 1
        if self.calls == 1:
            time.sleep(0.15)
        return [e["event_id"] for e in batch]


sticking = FirstCallSticks()
c3b = CollectorClient(
    transport=sticking,
    config=CollectorConfig(
        collector_timeout_ms=10,
        max_inflight_ms=30,
        batch_size=10,
    ),
)
c3b.record(ev(99))
check(c3b.flush().reason == "timeout", "stuck: first send times out")

time.sleep(0.04)  # max_inflight_ms (30ms) elapses, but the transport is still sleeping
res = c3b.flush()
check(res.reason == "empty" and c3b.pending == 1, "stuck: abandoned send is NOT resent while the zombie worker may still be delivering it")
check(sticking.calls == 1, "stuck: transport was called exactly once so far (no duplicate send)")

time.sleep(0.15)  # let the zombie worker's transport call actually finish
res = c3b.flush()
check(res.ok is True and c3b.pending == 0, "stuck: the zombie's late (successful) ack is applied once it finishes")
check(sticking.calls == 1, "stuck: transport was STILL only called once overall (the late ack was reaped, not re-sent)")

# --- duplicate send: dedup by event_id -------------------------------------------------
c4 = CollectorClient(transport=good, config=CollectorConfig(batch_size=10))
c4.record(ev(1))
c4.record(ev(1))  # same event_id twice
c4.record(ev(2))
check(c4.pending == 2, "duplicate send: same event_id is buffered once (dedup)")


# --- partial batch failure: only the un-acked events are retried -----------------------
def partial(batch):
    # ack only even-indexed events; the rest "fail"
    return [e["event_id"] for e in batch if e["payload"] % 2 == 0]


c5 = CollectorClient(transport=partial, config=CollectorConfig(batch_size=10))
for i in range(4):  # evt-0..evt-3, payload 0..3
    c5.record(ev(i))
res = c5.flush()
check(c5.sent_total == 2, f"partial failure: only acked events count as sent (got {c5.sent_total})")
check(c5.pending == 2, f"partial failure: un-acked events remain for retry (got {c5.pending})")
check(res.ok is False, "partial failure: flush reports partial (not fully ok)")


# --- process killed mid-flush: transport dies, batch fully preserved -------------------
def killed(batch):
    raise RuntimeError("process killed mid-flush")


c6 = CollectorClient(transport=killed, config=CollectorConfig(batch_size=10))
for i in range(3):
    c6.record(ev(i))
before = c6.pending
res = c6.flush()
check(c6.pending == before == 3, "killed mid-flush: the whole batch is preserved (nothing lost)")
check(c6.sent_total == 0, "killed mid-flush: nothing acked")


# --- offline mode: never touches the transport, just buffers ---------------------------
def must_not_call(batch):
    raise AssertionError("transport called in offline_mode")


c7 = CollectorClient(transport=must_not_call, config=CollectorConfig(offline_mode=True, batch_size=10))
raised = False
try:
    for i in range(3):
        c7.record(ev(i))
    res = c7.flush()
except Exception:  # noqa: BLE001
    raised = True
check(not raised, "offline mode: record + flush never raise")
check(c7.pending == 3, "offline mode: events buffered, transport never called")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
