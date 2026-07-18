"""Phase 3 — superseded event contributes 0 at the event grain (INV-5).

Run: python tests/test_event_grain_no_double_count.py

A superseded event contributes 0 everywhere. The trace rollup must count the live event
only, never the superseded one — proving event-grain totals don't double count a retry.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.reconciler import reconcile_events  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def out(quantity: int) -> TokenQuantity:
    return TokenQuantity(
        token_type=TokenType.OUTPUT,
        quantity=quantity,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=Additivity.TOTAL_CONTRIBUTING,
    )


# A partial estimate and final provider usage for one logical attempt.
attempt = TokenEvent(
    event_id="evt-attempt",
    request_correlation_id="rcid-attempt",
    trace_id="t-1",
    span_id="s-1",
    quantities=[
        TokenQuantity(
            token_type=TokenType.OUTPUT,
            quantity=120,
            precision_level=PrecisionLevel.ESTIMATE,
            usage_source=UsageSource.PARTIAL_STREAM_TOKENIZER,
            additivity=Additivity.TOTAL_CONTRIBUTING,
        )
    ],
    data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
    timestamp="2026-07-16T10:00:00Z",
    observation={"authoritative": True, "status": "incomplete"},
)
final = TokenEvent(
    event_id="evt-final",
    request_correlation_id="rcid-attempt",
    trace_id="t-1",
    span_id="s-1",
    quantities=[out(200)],
    provider_total_tokens=200,
    timestamp="2026-07-16T10:00:01Z",
    observation={"authoritative": True, "status": "complete"},
)

reconcile_events([attempt, final])

check(attempt.event_contributing_tokens == 0, "superseded event contributes 0")
check(final.event_contributing_tokens == 200, "live event contributes its tokens")

trace = Trace(trace_id="t-1")
trace.add_event(attempt)
trace.add_event(final)

rollup = observed_total_contributing_tokens(trace)
check(rollup == 200, f"trace total counts the live event only (got {rollup})")
check(
    rollup != 120 + final.provider_total_tokens,
    "trace total is NOT partial + final (no double count)",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
