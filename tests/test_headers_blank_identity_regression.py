"""Regression — extract() must return None (never raise) on a blank/whitespace-only required id.

Run: python tests/test_headers_blank_identity_regression.py

Found during a rigorous review of the proxy path (tracker/proxy/server.py -> _measurement ->
extract_context). extract()'s guard used truthiness (`not values[field]`), which an all-spaces
header value passes (a space string is truthy). It then fed that blank value into
TraceContext(...), whose __post_init__ rejects a whitespace-only id, so extract() RAISED
instead of returning None — violating its documented "return None on incomplete identity, never
raise" contract. In the proxy, extract_context runs inside _measurement(), OUTSIDE the try that
guards the upstream call, so that raise aborted the REAL provider call (the proxy's explicit
"observation must never break the API call" invariant). Fixed with a `.strip()` check in
extract() plus a defensive wrap around _measurement() in the proxy.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context.headers import extract  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


valid_others = {
    "X-TokenTracker-Span-Id": "span-1",
    "X-TokenTracker-Request-Correlation-Id": "req-1",
}

# the exact failing case: a whitespace-only required id
result = None
raised = False
try:
    result = extract({"X-TokenTracker-Trace-Id": "   ", **valid_others})
except Exception as exc:  # noqa: BLE001
    raised = True
    result = exc
check(not raised, f"FIXED: a whitespace-only Trace-Id header no longer raises (was {type(result).__name__ if raised else 'ok'})")
check(result is None, "a whitespace-only required id is treated as missing identity -> None")

# tabs / newlines are blank too
check(extract({"X-TokenTracker-Trace-Id": "\t\n", **valid_others}) is None, "tab/newline-only required id -> None")

# each required field independently
check(
    extract({"X-TokenTracker-Trace-Id": "t1", "X-TokenTracker-Span-Id": "  ", "X-TokenTracker-Request-Correlation-Id": "r1"}) is None,
    "blank Span-Id -> None",
)
check(
    extract({"X-TokenTracker-Trace-Id": "t1", "X-TokenTracker-Span-Id": "s1", "X-TokenTracker-Request-Correlation-Id": " "}) is None,
    "blank Request-Correlation-Id -> None",
)

# a value with SURROUNDING whitespace but real content is still accepted (not stripped away)
ctx = extract({"X-TokenTracker-Trace-Id": " t1 ", "X-TokenTracker-Span-Id": "s1", "X-TokenTracker-Request-Correlation-Id": "r1"})
check(ctx is not None, "a required id with real content survives even if surrounded by spaces")

# fully valid identity still round-trips unchanged
valid = extract({"X-TokenTracker-Trace-Id": "t1", "X-TokenTracker-Span-Id": "s1", "X-TokenTracker-Request-Correlation-Id": "r1"})
check(valid is not None and valid.trace_id == "t1", "a fully valid identity still rehydrates correctly")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
