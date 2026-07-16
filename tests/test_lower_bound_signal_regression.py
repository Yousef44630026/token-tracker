"""Regression (P2 / L2) — the observed total must carry its own epistemic status.

observed_total_contributing_tokens is a POINT value only when every real token was both
measured and counted. When any quantity is unverified (a real count we don't trust, so it
contributes 0) or unknown (a lost measurement, contributes 0), or a provider total we could not
reconcile, the observed total is a FLOOR — the true total is >= it. The number most likely to be
consumed as gospel must not travel without that status.

Two concrete gaps this pins:
  L2: coverage counts quantities by PRECISION only, so a precision=EXACT quantity that is
      additivity=unverified (contributes 0) is reported as fully "measured" while it silently
      vanishes from the total. Coverage must expose unverified_quantity_count so "measured" and
      "counted" can differ visibly.
  P2: the rollup / coverage must expose total_is_lower_bound so a consumer sees the floor.

Run: python tests/test_lower_bound_signal_regression.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.coverage import build_coverage_exactness  # noqa: E402
from tracker.derive.trace_rollup import roll_up, total_is_lower_bound  # noqa: E402
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


# --- an EXACTLY measured but UNVERIFIED quantity: measured, yet not counted ---
# input is counted (100). cached_input is a perfectly exact 900 but additivity=unverified,
# so it contributes 0. The true usage clearly exceeds 100 -> the total is a lower bound.
e = TokenEvent(
    event_id="e1",
    request_correlation_id="r1",
    trace_id="t",
    span_id="s",
    quantities=[
        q(TokenType.INPUT, 100, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE),
        q(TokenType.CACHED_INPUT, 900, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, add=Additivity.UNVERIFIED),
    ],
    observation={"authoritative": True},
)
trace = Trace(trace_id="t")
trace.add_event(e)

c = build_coverage_exactness(trace)
check(c["observed_total_contributing_tokens"] == 100, "only the counted input reaches the total (unverified contributes 0)")
check(c["exact_quantity_count"] == 2, "BOTH quantities are precision=EXACT — precision alone says 'fully measured'")
check(
    c.get("unverified_quantity_count") == 1,
    "coverage exposes unverified_quantity_count == 1, so 'measured' and 'counted' differ visibly",
)
check(c.get("total_is_lower_bound") is True, "coverage marks the total as a lower bound (an unverified real count was excluded)")

r = roll_up(trace)
check(
    getattr(r, "total_is_lower_bound", None) is True,
    "the rollup itself carries total_is_lower_bound=True (the headline number knows its status)",
)
check(total_is_lower_bound(trace) is True, "total_is_lower_bound(trace) helper agrees")

# --- a fully clean trace: everything counted and exactly measured, provider total reconciles ---
clean = Trace(trace_id="clean")
clean.add_event(
    TokenEvent(
        event_id="c1",
        request_correlation_id="rc",
        trace_id="clean",
        span_id="s",
        quantities=[
            q(TokenType.INPUT, 100, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE),
            q(TokenType.OUTPUT, 50, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE),
        ],
        provider_total_tokens=150,
        observation={"authoritative": True},
    )
)
cc = build_coverage_exactness(clean)
check(cc.get("unverified_quantity_count") == 0, "clean trace: no unverified quantities")
check(cc.get("total_is_lower_bound") is False, "clean trace: total is exact, NOT a lower bound")
check(total_is_lower_bound(clean) is False, "clean trace: helper agrees the total is exact")
check(roll_up(clean).total_is_lower_bound is False, "clean trace: rollup marks the total exact")

# --- an unknown (lost) quantity also makes the total a floor ---
lost = Trace(trace_id="lost")
lost.add_event(
    TokenEvent(
        event_id="l1",
        request_correlation_id="rl",
        trace_id="lost",
        span_id="s",
        quantities=[q(TokenType.OUTPUT, None, PrecisionLevel.UNKNOWN, UsageSource.NONE)],
        observation={"authoritative": True},
    )
)
check(total_is_lower_bound(lost) is True, "a lost/unknown quantity makes the total a lower bound too")

# --- a superseded event's imperfections must NOT taint the live total's status ---
# The live event is clean; a correlated partial carrying an unverified quantity should not
# flip the trace to lower-bound, because superseded events contribute 0 by design (not by loss).
sup_trace = Trace(trace_id="sup")
sup_trace.add_event(
    TokenEvent(
        event_id="live",
        request_correlation_id="rs",
        trace_id="sup",
        span_id="s",
        quantities=[q(TokenType.OUTPUT, 100, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
        provider_total_tokens=100,
        observation={"authoritative": True},
    )
)
sup_trace.add_event(
    TokenEvent(
        event_id="dead",
        request_correlation_id="rs",
        trace_id="sup",
        span_id="s",
        quantities=[q(TokenType.OUTPUT, 900, PrecisionLevel.EXACT, UsageSource.PARTIAL_STREAM_TOKENIZER, add=Additivity.UNVERIFIED)],
        data_quality_flags=["partial_stream_estimate"],
        observation={"authoritative": True},
    )
)
check(
    total_is_lower_bound(sup_trace) is False,
    "a superseded event's unverified quantity does not taint the live total's status (it contributes 0 by design)",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
