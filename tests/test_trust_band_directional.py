"""Trust bands must preserve direction and admit when no finite ceiling exists."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.trace_rollup import roll_up  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UnknownReason, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def event(trace_id, event_id, quantities, provider_total=None):
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=f"request-{event_id}",
        trace_id=trace_id,
        span_id="span",
        quantities=quantities,
        provider_total_tokens=provider_total,
        observation={"authoritative": True, "status": "complete"},
    )


def q(value, precision=PrecisionLevel.EXACT, additivity=Additivity.TOTAL_CONTRIBUTING, reason=None):
    source = UsageSource.NONE if value is None else UsageSource.PROVIDER_RESPONSE
    return TokenQuantity(TokenType.OUTPUT, value, precision, source, additivity, unknown_reason=reason)


over = Trace(trace_id="over", events=[event("over", "over", [q(200)], 100)])
over_rollup = roll_up(over)
check(over_rollup.observed_total_contributing_tokens == 200, "canonical observed total remains the attributed quantity total")
check(
    (over_rollup.headline_floor_tokens, over_rollup.headline_estimate_tokens, over_rollup.headline_ceiling_tokens)
    == (100, 100, 100),
    "provider total pins the trust band despite over-attribution",
)
check(over_rollup.total_is_lower_bound is False, "over-attribution is not mislabeled as a lower bound")
check(over_rollup.total_is_upper_bound is True, "over-attributed observed total is directionally identified")
check(over_rollup.capture_completeness_ratio is None, "over-attribution has no misleading completeness ratio")
check(over_rollup.headline_status == "provider_reconciled", "mismatch is visibly provider-reconciled")

unknown = Trace(
    trace_id="unknown",
    events=[event("unknown", "unknown", [q(None, PrecisionLevel.UNKNOWN, reason=UnknownReason.STREAM_TIMEOUT)])],
)
unknown_rollup = roll_up(unknown)
check(unknown_rollup.headline_ceiling_tokens is None, "unknown independent usage opens the upper bound")
check(unknown_rollup.capture_completeness_ratio is None, "open upper bound makes completeness unknown")
check(unknown_rollup.total_is_lower_bound is True, "zero observed usage remains an honest lower bound")
check(unknown_rollup.headline_status == "open", "open uncertainty is explicit")

estimated = Trace(trace_id="estimated", events=[event("estimated", "estimated", [q(40, PrecisionLevel.ESTIMATE)])])
estimated_rollup = roll_up(estimated)
check(
    (estimated_rollup.headline_floor_tokens, estimated_rollup.headline_estimate_tokens, estimated_rollup.headline_ceiling_tokens)
    == (0, 40, None),
    "an unbounded estimate has a floor and working estimate, not a fabricated ceiling",
)
check(estimated_rollup.total_is_lower_bound is False, "a point estimate is not mislabeled as the floor")

bounded = Trace(
    trace_id="bounded",
    events=[
        event(
            "bounded",
            "bounded",
            [
                TokenQuantity(
                    TokenType.INPUT,
                    100,
                    PrecisionLevel.EXACT,
                    UsageSource.PROVIDER_RESPONSE,
                    Additivity.TOTAL_CONTRIBUTING,
                ),
                TokenQuantity(
                    TokenType.CACHED_INPUT,
                    900,
                    PrecisionLevel.EXACT,
                    UsageSource.PROVIDER_RESPONSE,
                    Additivity.UNVERIFIED,
                ),
            ],
        )
    ],
)
bounded_rollup = roll_up(bounded)
check(bounded_rollup.headline_ceiling_tokens == 1000, "known unverified independent usage creates a finite ceiling")
check(bounded_rollup.capture_completeness_ratio == 0.1, "finite bounded uncertainty has a meaningful capture ratio")
check(bounded_rollup.headline_status == "bounded", "finite uncertainty is labeled bounded")

missing_usage = event("missing", "missing", [])
missing_usage.data_quality_flags.append("raw_usage_missing")
missing_rollup = roll_up(Trace(trace_id="missing", events=[missing_usage]))
check(missing_rollup.headline_ceiling_tokens is None, "event-level missing usage opens the upper bound even without a typed quantity")
check(missing_rollup.total_is_lower_bound is True, "missing provider usage can never be reported as exact zero")
check(missing_rollup.headline_status == "open", "missing provider usage is visibly open-ended")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
