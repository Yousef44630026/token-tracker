"""Regression — reliability success_rate must not default to a confident 100%.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_reliability_unmeasured_regression.py

Found during a rigorous logic/relevance review of tracker/analytics/reliability.py: `observation`
is optional and defaults to {} — nothing in tracker/workflows/agent_tracker.py or rag_tracker.py
ever populates status/http_status/error fields (verified by grep), and most of this project's own
tests never populate `observation` either. Before this fix, `_is_error()` could only return True
when a signal existed, so an event with NO observation data silently counted as a "success" via
`len(events) - errors` — success_rate read 100% not because anything succeeded, but because
nothing was measured at all. That is the exact confident-zero shape INV-6 forbids at the token
layer, previously unaddressed one layer up at the reliability-analytics layer.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.reliability import build_reliability_summary  # noqa: E402
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


def q(qty):
    return TokenQuantity(TokenType.OUTPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)


# --- 5 events, exactly like most of this project's own tests build them: NO observation
# data at all (the default {}). The old code would report success_rate == 1.0 here.
trace = Trace(trace_id="unmeasured-regression")
for i in range(5):
    trace.add_event(
        TokenEvent(
            event_id=f"evt-{i}",
            request_correlation_id=f"r-{i}",
            trace_id=trace.trace_id,
            span_id="s",
            quantities=[q(10)],
        )
    )

summary = build_reliability_summary(trace)
check(summary["event_count"] == 5, "setup: 5 events")
check(summary["judged_event_count"] == 0, "none of the 5 events carry any operational signal")
check(summary["unmeasured_event_count"] == 5, "all 5 are correctly counted as unmeasured")
check(
    summary["success_rate"] is None,
    f"FIXED: success_rate is None (unmeasurable), not a false 100% (got {summary['success_rate']})",
)
check(summary["error_rate"] is None, "error_rate is also None, not a false 0%")
check(summary["unmeasured_rate"] == 1.0, "unmeasured_rate correctly reports 100% unmeasured")

# --- mixing in ONE judged failure among 4 unmeasured events: success_rate must reflect ONLY
# the judged population (1 judged, 1 error -> 0% success among judged), not be diluted by the
# 4 events nothing is known about.
trace2 = Trace(trace_id="unmeasured-regression-2")
trace2.add_event(
    TokenEvent(
        event_id="known-failure",
        request_correlation_id="r-fail",
        trace_id=trace2.trace_id,
        span_id="s",
        quantities=[q(10)],
        observation={"status": "failed"},
    )
)
for i in range(4):
    trace2.add_event(
        TokenEvent(
            event_id=f"unmeasured-{i}",
            request_correlation_id=f"r-unmeasured-{i}",
            trace_id=trace2.trace_id,
            span_id="s",
            quantities=[q(10)],
        )
    )
summary2 = build_reliability_summary(trace2)
check(summary2["judged_event_count"] == 1, "only the explicitly-failed event is judged")
check(summary2["error_count"] == 1, "the one judged event is an error")
check(
    summary2["success_rate"] == 0.0,
    f"success_rate reflects the judged population ONLY (0% of 1 judged event succeeded), "
    f"not diluted to a falsely-reassuring 20% error / 80% success across all 5 (got {summary2['success_rate']})",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
