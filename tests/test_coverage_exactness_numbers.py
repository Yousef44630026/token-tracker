"""Extra — CoverageExactness numeric correctness (Phase 9 analytics).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_coverage_exactness_numbers.py

Crafts a trace with a known mix (exact / estimate / unknown quantities, some events with a
provider total, some without) and pins every coverage/exactness number.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.coverage import build_coverage_exactness  # noqa: E402
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


def q(tt, qty, prec, src, add=Additivity.TOTAL_CONTRIBUTING, parent=None):
    return TokenQuantity(tt, qty, prec, src, add, subtotal_of=parent)


# e1: exact input+output + an exact cached subtotal; provider total present and matching
e1 = TokenEvent(
    event_id="e1",
    request_correlation_id="r1",
    trace_id="t",
    span_id="s",
    quantities=[
        q(TokenType.INPUT, 1000, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE),
        q(TokenType.OUTPUT, 300, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE),
        q(TokenType.CACHED_INPUT, 800, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, add=Additivity.SUBTOTAL_OF, parent="input"),
    ],
    provider_total_tokens=1300,
)
# e2: a partial-stream estimate, no provider total
e2 = TokenEvent(
    event_id="e2",
    request_correlation_id="r2",
    trace_id="t",
    span_id="s",
    quantities=[q(TokenType.OUTPUT, 40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER)],
)
# e3: a lost (unknown) output, no provider total
e3 = TokenEvent(
    event_id="e3",
    request_correlation_id="r3",
    trace_id="t",
    span_id="s",
    quantities=[q(TokenType.OUTPUT, None, PrecisionLevel.UNKNOWN, UsageSource.NONE)],
)
# e4/e5 are preserved in the trace for audit but excluded from coverage denominators.
e4 = TokenEvent(
    event_id="e4",
    request_correlation_id="r4",
    trace_id="t",
    span_id="s",
    quantities=[q(TokenType.OUTPUT, 999, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
    provider_total_tokens=999,
    superseded=True,
    superseded_by="e1",
)
e5 = TokenEvent(
    event_id="e5",
    request_correlation_id="r5",
    trace_id="t",
    span_id="s",
    quantities=[q(TokenType.OUTPUT, 888, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
    provider_total_tokens=888,
    observation={"status": "failed", "authoritative": False},
)

trace = Trace(trace_id="t")
for e in (e1, e2, e3, e4, e5):
    trace.add_event(e)

c = build_coverage_exactness(trace)

check(c["observed_total_contributing_tokens"] == 1340, "observed total == 1300 + 40 + 0")
check(c["total_is_lower_bound"] is True, "unknown live output makes observed total a lower bound")
check(c["event_count"] == 3, "event_count excludes superseded/non-authoritative events")
check(c["excluded_event_count"] == 2, "excluded_event_count == superseded + non-authoritative")
check(c["superseded_event_count"] == 1, "superseded_event_count == 1")
check(c["quantity_count"] == 5, "quantity_count == 5")
check(c["exact_quantity_count"] == 3, "exact_quantity_count == 3")
check(c["estimate_quantity_count"] == 1, "estimate_quantity_count == 1")
check(c["unknown_quantity_count"] == 1, "unknown_quantity_count == 1")
check(c["unverified_quantity_count"] == 0, "unverified_quantity_count == 0")
check(c["provider_total_mismatch_count"] == 0, "no mismatch (e1 reconciles, others have no total)")
check(c["events_with_provider_total"] == 1, "events_with_provider_total == 1")
check(c["coverage_ratio"] == round(1 / 3, 4), "coverage_ratio == 1/3")
check(c["exactness_ratio"] == 0.6, "exactness_ratio == exact/ALL quantities (3/5, unknown counts in the denominator) == 0.6")
check(c["known_exactness_ratio"] == 0.75, "known_exactness_ratio == exact/(exact+estimate) == 0.75 (narrower, non-headline lens)")

# --- regression: exactness_ratio must NOT be able to read 100% while most data is unknown ---
# (this is the exact failure mode the old exact/(exact+estimate) formula allowed: excluding
# unknown from its own denominator let a trace with 90% missing data still claim "100% exact")
mostly_unknown = Trace(trace_id="mostly-unknown")
mostly_unknown.add_event(
    TokenEvent(
        event_id="known-good",
        request_correlation_id="r",
        trace_id="mostly-unknown",
        span_id="s",
        quantities=[q(TokenType.INPUT, 10, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
    )
)
for i in range(9):
    mostly_unknown.add_event(
        TokenEvent(
            event_id=f"lost-{i}",
            request_correlation_id=f"r-lost-{i}",
            trace_id="mostly-unknown",
            span_id="s",
            quantities=[q(TokenType.OUTPUT, None, PrecisionLevel.UNKNOWN, UsageSource.NONE)],
        )
    )
mostly_unknown_summary = build_coverage_exactness(mostly_unknown)
check(
    mostly_unknown_summary["exact_quantity_count"] == 1 and mostly_unknown_summary["unknown_quantity_count"] == 9,
    "regression setup: 1 exact, 9 unknown",
)
check(
    mostly_unknown_summary["exactness_ratio"] == 0.1,
    f"regression: 90% unknown data correctly drags exactness_ratio down to 0.1, "
    f"not up to 1.0 (got {mostly_unknown_summary['exactness_ratio']})",
)
check(
    mostly_unknown_summary["known_exactness_ratio"] == 1.0,
    "regression: known_exactness_ratio (the narrower, explicitly-labeled lens) DOES read 1.0 here — "
    "correct for ITS question, but it is never the headline precisely because of this gap",
)

# tracker/analytics/exactness.py is a compatibility re-export shim (no logic of its own,
# nothing in the codebase currently imports it) — nothing exercised it directly until now.
# Proving it re-exports the SAME function (not a stale/diverged copy) is proportionate to
# what the shim actually is; it doesn't warrant a whole separate test file.
from tracker.analytics.exactness import build_coverage_exactness as build_coverage_exactness_via_shim  # noqa: E402

check(
    build_coverage_exactness_via_shim is build_coverage_exactness,
    "analytics.exactness compatibility shim re-exports the exact same function object as analytics.coverage",
)
check(
    build_coverage_exactness_via_shim(trace) == c,
    "the shim's re-exported function produces identical output to calling coverage.py directly",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
