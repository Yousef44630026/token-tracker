"""Regression (L3) — the Power BI export must not double-count a duplicated event_id.

The Trace model rejects duplicate event_ids (add_event), so any total rolled up through a Trace
is safe. But export_powerbi_events aggregates a RAW Sequence[TokenEvent], bypassing that guard.
A collector's at-least-once delivery (or a re-read of an appended JSONL) can present the same
event twice; the fact table would then carry two rows and fact_service_daily would sum
contributing_tokens twice. The export must dedupe by event_id (identity is the event_id) so the
central 'never double-count' promise holds at this boundary too.

Run: python tests/test_powerbi_dedup_event_id.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.export.powerbi_exporter import fact_service_daily_rows, fact_token_event_rows  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def event(eid):
    return TokenEvent(
        event_id=eid,
        request_correlation_id="rc",
        trace_id="t",
        span_id="s",
        provider="openai",
        api_surface="responses",
        model="gpt-x",
        timestamp="2026-07-03T10:00:00",
        quantities=[
            TokenQuantity(TokenType.INPUT, 100, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING),
            TokenQuantity(TokenType.OUTPUT, 50, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING),
        ],
        provider_total_tokens=150,
    )


# the SAME event delivered twice (at-least-once delivery / duplicate JSONL append)
one = event("evt-1")
dup = event("evt-1")
events = [one, dup]

fact = fact_token_event_rows(events)
check(len(fact) == 1, f"fact_token_events dedupes the repeated event_id (one row, got {len(fact)})")

daily = fact_service_daily_rows(events)
total = sum(int(r["contributing_tokens"]) for r in daily)
check(total == 150, f"fact_service_daily counts the event once: 150, not 300 (got {total})")

# distinct event_ids are of course all kept
distinct = fact_token_event_rows([event("a"), event("b"), event("c")])
check(len(distinct) == 3, "distinct event_ids are all retained (dedupe only collapses true duplicates)")

# order-preserving: the first occurrence is the one kept
ordered = fact_token_event_rows([event("x"), event("y"), event("x")])
check([r["event_id"] for r in ordered] == ["x", "y"], "dedupe keeps first occurrence, preserves order")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
