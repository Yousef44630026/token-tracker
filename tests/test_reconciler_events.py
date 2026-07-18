"""Extra — reconcile_events: refresh quality flags AND apply supersession together.

Run: python tests/test_reconciler_events.py

reconcile_events first re-derives the normalizer-owned flags for each event (dropping a now-
stale one, keeping foreign flags) and then applies correlation-based supersession across the
batch.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.normalization.reconciler import reconcile_events  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def out(qty, prec=PrecisionLevel.EXACT, src=UsageSource.PROVIDER_RESPONSE):
    return TokenQuantity(TokenType.OUTPUT, qty, prec, src, Additivity.TOTAL_CONTRIBUTING)


# a partial estimate + its final usage, same request_correlation_id
partial = TokenEvent(
    event_id="p",
    request_correlation_id="r1",
    trace_id="t",
    span_id="s",
    quantities=[out(40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER)],
    data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
    observation={"authoritative": True},
)
final = TokenEvent(
    event_id="f",
    request_correlation_id="r1",
    trace_id="t",
    span_id="s",
    quantities=[out(200, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL)],
    provider_total_tokens=200,
    observation={"authoritative": True},
)
# an event carrying a now-STALE normalizer flag (no real mismatch: 100 == 100)
stale = TokenEvent(
    event_id="x",
    request_correlation_id="r-stale",
    trace_id="t",
    span_id="s",
    quantities=[out(100)],
    provider_total_tokens=100,
    data_quality_flags=["provider_total_mismatch"],
    observation={"authoritative": True},
)

result = reconcile_events([partial, final, stale])
check(len(result) == 3, "reconcile_events returns all events")

# 1) stale normalizer-owned flag is refreshed away
check(stale.data_quality_flags == [], "stale provider_total_mismatch flag removed (no real mismatch)")

# 2) supersession applied across the batch, foreign flags preserved
check(partial.superseded is True and partial.superseded_by == "f", "partial superseded by its final (same rcid)")
check("superseded" in partial.data_quality_flags, "supersession flag added")
check("partial_stream_estimate" in partial.data_quality_flags, "foreign stream flags preserved through refresh")
check(final.superseded is False, "final is not superseded")

# 3) totals: final + stale only (partial contributes 0)
total = sum(e.event_contributing_tokens for e in result)
check(total == 300, f"contributing total == 200 + 100 + 0 == 300 (got {total})")

# refresh-to-ADD: when a source field changes to create a mismatch, reconcile flags it
ev = TokenEvent(
    event_id="m",
    request_correlation_id="rm",
    trace_id="t",
    span_id="s",
    quantities=[out(100)],
    provider_total_tokens=100,
    observation={"authoritative": True},
)
ev.provider_total_tokens = 999
reconcile_events([ev])
check("provider_total_mismatch" in ev.data_quality_flags, "reconcile flags a newly-introduced mismatch")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
