"""Extra — trace rollup totals + counts (INV-2 / INV-5).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_trace_rollup.py

The rollup sums event_contributing_tokens (superseded and unknown contribute 0) and reports
event-grain counts, all recomputed, nothing stored.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.trace_rollup import TraceRollup, observed_total_contributing_tokens, roll_up  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def out(qty, prec=PrecisionLevel.EXACT, src=UsageSource.PROVIDER_RESPONSE):
    return TokenQuantity(TokenType.OUTPUT, qty, prec, src, Additivity.TOTAL_CONTRIBUTING)


def event(eid, qty, *, correlation_id=None, source=UsageSource.PROVIDER_RESPONSE, flags=None):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=correlation_id or f"r-{eid}",
        trace_id="t-1",
        span_id="s",
        quantities=[out(qty, src=source)],
        data_quality_flags=flags or [],
        observation={"authoritative": True},
    )


trace = Trace(trace_id="t-1")
trace.add_event(event("a", 100))
trace.add_event(event("b", 200))
trace.add_event(
    event(
        "c",
        999,
        correlation_id="r-a",
        source=UsageSource.PARTIAL_STREAM_TOKENIZER,
        flags=["partial_stream_estimate"],
    )
)  # superseded by a, contributes 0
trace.add_event(
    TokenEvent(
        event_id="d",
        request_correlation_id="r-d",
        trace_id="t-1",
        span_id="s",
        quantities=[out(None, PrecisionLevel.UNKNOWN, UsageSource.NONE)],
        data_quality_flags=["unknown_quantity_present"],
        observation={"authoritative": True},
    )
)  # contributes 0

check(observed_total_contributing_tokens(trace) == 300, "observed total == 300 (superseded + unknown count 0)")

r = roll_up(trace)
check(isinstance(r, TraceRollup), "roll_up returns a TraceRollup")
check(r.trace_id == "t-1", "rollup carries the trace_id")
check(r.observed_total_contributing_tokens == 300, "rollup total == 300")
check(r.event_count == 4, "event_count == 4")
check(r.superseded_event_count == 1, "superseded_event_count == 1")
check(r.flagged_event_count == 2, "flagged_event_count == 2 (c and d have flags)")

# empty trace
empty = Trace(trace_id="empty")
check(observed_total_contributing_tokens(empty) == 0 and roll_up(empty).event_count == 0, "empty trace -> 0 / 0")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
