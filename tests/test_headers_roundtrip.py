"""Extra — cross-service header inject/extract round-trip (Phase 1).

Run: python tests/test_headers_roundtrip.py

Every field survives inject -> extract; optional fields are omitted when None; extract is
case-insensitive, ignores unrelated headers, and returns None when the required identity is
incomplete (the propagation layer turns that None into propagation_lost).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context.headers import PREFIX, extract, inject  # noqa: E402
from tracker.context.propagation import TraceContext  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


full = TraceContext(
    trace_id="tr",
    span_id="sp",
    request_correlation_id="rc",
    parent_span_id="par",
    business_id="biz",
    workflow="wf",
    environment="prod",
)

# --- full round-trip ---
h = inject(full)
check(len(h) == 7, "all 7 fields injected")
check(h[PREFIX + "Trace-Id"] == "tr" and h[PREFIX + "Request-Correlation-Id"] == "rc", "required headers present")
check(extract(h) == full, "extract(inject(full)) == full")

# --- required-only: optionals omitted ---
minimal = TraceContext(trace_id="tr", span_id="sp", request_correlation_id="rc")
hm = inject(minimal)
check(len(hm) == 3, "only the 3 required headers injected when optionals are None")
check(PREFIX + "Business-Id" not in hm, "None optional field is omitted")
check(extract(hm) == minimal, "required-only round-trips")

# --- case-insensitive extract ---
lowered = {k.lower(): v for k, v in h.items()}
check(extract(lowered) == full, "extract is case-insensitive on header keys")

# --- unrelated headers ignored ---
noisy = dict(h)
noisy["Content-Type"] = "application/json"
noisy["X-Other"] = "ignore me"
check(extract(noisy) == full, "unrelated headers are ignored")

# --- incomplete identity -> None ---
broken = dict(h)
del broken[PREFIX + "Span-Id"]
check(extract(broken) is None, "missing a required field -> None")
check(extract({}) is None, "no tracker headers -> None")
empty_required = {PREFIX + "Trace-Id": "", PREFIX + "Span-Id": "sp", PREFIX + "Request-Correlation-Id": "rc"}
check(extract(empty_required) is None, "empty required value -> None")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
