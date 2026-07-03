"""Extra — make_http_transport safe-failure on network errors.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_http_transport_errors.py

The HTTP transport must never raise into the collector: an unreachable endpoint, a refused
connection, or a garbage URL all return [] (nothing acked) so the client requeues the batch.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import make_http_transport  # noqa: E402
from tracker.collector.client import CollectorClient  # noqa: E402

_failures = 0
BATCH = [{"event_id": "e1"}, {"event_id": "e2"}]


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# connection refused (nothing listening on these loopback ports)
check(make_http_transport("http://127.0.0.1:1/v1/events")(BATCH) == [], "refused connection -> [] (no ack)")
check(make_http_transport("http://127.0.0.1:9/v1/events", timeout=1.0)(BATCH) == [], "unreachable port -> [] (no ack)")

# empty batch is harmless
check(make_http_transport("http://127.0.0.1:1/v1/events")([]) == [], "empty batch -> []")

# a malformed URL: the transport may raise at Request() construction, but the COLLECTOR
# boundary (where it is actually used) never lets that reach the caller.
bad = CollectorClient(make_http_transport("not-a-valid-url"))
bad.record({"event_id": "g1"})
check(bad.flush().ok is False and bad.pending == 1, "malformed URL stays safe at the collector boundary, batch requeued")

# the collector keeps the batch for retry when delivery fails (safe-failure contract)
collector = CollectorClient(make_http_transport("http://127.0.0.1:1/v1/events"))
collector.record({"event_id": "k1"})
collector.record({"event_id": "k2"})
result = collector.flush()
check(result.ok is False, "flush against a dead endpoint is not ok")
check(collector.pending == 2 and collector.sent_total == 0, "failed delivery requeues the batch (nothing lost)")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
