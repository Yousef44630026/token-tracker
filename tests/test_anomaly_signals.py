"""Extra — derived anomaly signals (analytics).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_anomaly_signals.py

detect_anomalies materializes one signal per non-zero provider/derived mismatch and one per
data-quality flag (de-duplicating the mismatch flag against the derived mismatch signal). All
derived, nothing stored. A clean event yields nothing.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.anomaly_signals import AnomalySignal, detect_anomalies  # noqa: E402
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


def out(qty):
    return TokenQuantity(TokenType.OUTPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)


def codes_for(signals, event_id):
    return sorted(s.code for s in signals if s.event_id == event_id)


# e1: provider total disagrees + the normalizer flag -> ONE mismatch signal (de-duplicated)
e1 = TokenEvent(
    event_id="e1",
    request_correlation_id="r1",
    trace_id="t",
    span_id="s",
    quantities=[out(100)],
    provider_total_tokens=999,
    data_quality_flags=["provider_total_mismatch"],
)
# e2: an unverified-additivity flag, no provider total
e2 = TokenEvent(
    event_id="e2",
    request_correlation_id="r2",
    trace_id="t",
    span_id="s",
    quantities=[out(300)],
    data_quality_flags=["unverified_additivity"],
)
# e3: clean -> no signals
e3 = TokenEvent(event_id="e3", request_correlation_id="r3", trace_id="t", span_id="s", quantities=[out(100)], provider_total_tokens=100)
# e4: superseded with two flags -> one signal per flag
e4 = TokenEvent(
    event_id="e4",
    request_correlation_id="r4",
    trace_id="t",
    span_id="s",
    quantities=[out(40)],
    superseded=True,
    superseded_by="e-final",
    data_quality_flags=["superseded", "partial_stream_estimate"],
)

trace = Trace(trace_id="t")
for e in (e1, e2, e3, e4):
    trace.add_event(e)

signals = detect_anomalies(trace)
check(all(isinstance(s, AnomalySignal) for s in signals), "detect_anomalies returns AnomalySignal objects")

# e1: exactly one mismatch signal, with the numeric detail, no duplicate from the flag
e1_signals = [s for s in signals if s.event_id == "e1"]
check(len(e1_signals) == 1 and e1_signals[0].code == "provider_total_mismatch", "e1: single mismatch signal (flag de-duplicated)")
check(e1_signals[0].detail == "899", "e1: mismatch detail == 999 - 100 == 899")

check(codes_for(signals, "e2") == ["unverified_additivity"], "e2: one unverified_additivity signal")
check(codes_for(signals, "e3") == [], "e3: clean event -> no signals")
check(codes_for(signals, "e4") == ["partial_stream_estimate", "superseded"], "e4: one signal per flag")

check(len(signals) == 4, f"4 signals total across the trace (got {len(signals)})")

# empty trace -> no signals
check(detect_anomalies(Trace(trace_id="empty")) == [], "empty trace -> no signals")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
