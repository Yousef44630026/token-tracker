"""Regression — duration must be read the same way in every analytics view.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_service_attribution_duration_parity.py

The observation contract recognizes THREE duration keys (duration_ms, total_duration_ms,
provider_duration_ms). LatencySummary reads all three, but ServiceAttribution read only
``duration_ms`` — so an event that reports its duration under ``total_duration_ms`` counted in the
latency average yet was invisible in the per-service average. Two sheets in the SAME export then
disagreed about the same underlying fact. Both must derive duration from the one shared helper.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.latency import build_latency_summary  # noqa: E402
from tracker.analytics.service_attribution import (
    build_service_attribution,
)  # noqa: E402
from tracker.models.enums import (
    Additivity,
    PrecisionLevel,
    TokenType,
    UsageSource,
)  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(tt, n):
    return TokenQuantity(
        tt,
        n,
        PrecisionLevel.EXACT,
        UsageSource.PROVIDER_RESPONSE,
        Additivity.TOTAL_CONTRIBUTING,
    )


# Two authoritative events that fall into ONE service group (identical provider/model, no service
# fields), reporting duration under DIFFERENT contract keys: one duration_ms=1000, one
# total_duration_ms=500. The per-service average must see BOTH -> (1000 + 500) / 2 == 750.
trace = Trace(trace_id="dur")
trace.add_event(
    TokenEvent(
        event_id="a",
        request_correlation_id="ra",
        trace_id="dur",
        span_id="s",
        provider="openai",
        model="m",
        api_surface="responses",
        quantities=[q(TokenType.OUTPUT, 10)],
        observation={"status": "complete", "authoritative": True, "duration_ms": 1000},
    )
)
trace.add_event(
    TokenEvent(
        event_id="b",
        request_correlation_id="rb",
        trace_id="dur",
        span_id="s",
        provider="openai",
        model="m",
        api_surface="responses",
        quantities=[q(TokenType.OUTPUT, 10)],
        observation={
            "status": "complete",
            "authoritative": True,
            "total_duration_ms": 500,
        },
    )
)

latency = build_latency_summary(trace)
attribution = build_service_attribution(trace)
check(attribution["group_count"] == 1, "both events fall into one service group")
row = attribution["rows"][0]

check(
    latency["average_duration_ms"] == 750.0,
    "latency average sees both duration keys -> 750",
)
check(
    row["average_duration_ms"] == 750.0,
    f"service attribution average also sees total_duration_ms -> 750 (got {row['average_duration_ms']})",
)
check(
    row["average_duration_ms"] == latency["average_duration_ms"],
    "the two views agree on the same duration fact",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
