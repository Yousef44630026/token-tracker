"""Phase 2 / step 3 — minimal Trace/Span source-of-truth models.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_trace_span.py

Trace/Span store identity + the events that belong to them. They deliberately carry NO
total: trace rollups are derived (derive/trace_rollup, Phase 3), so the model must not
expose a stored total that could drift from the rules.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.span import Span  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def mk_event(eid: str, qty: int) -> TokenEvent:
    return TokenEvent(
        event_id=eid,
        request_correlation_id="c-" + eid,
        trace_id="tr-1",
        span_id="sp-1",
        quantities=[
            TokenQuantity(
                token_type=TokenType.OUTPUT,
                quantity=qty,
                precision_level=PrecisionLevel.EXACT,
                usage_source=UsageSource.PROVIDER_RESPONSE,
                additivity=Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        provider_total_tokens=qty,
        observation={"authoritative": True},
    )


def main() -> int:
    span = Span(span_id="sp-1", trace_id="tr-1", parent_span_id=None, span_type="llm")
    check(span.span_id == "sp-1" and span.trace_id == "tr-1", "Span stores identity")
    check(not hasattr(span, "total"), "Span exposes no stored total (rollups are derived)")

    trace = Trace(trace_id="tr-1", workflow="wf")
    trace.add_event(mk_event("e1", 10))
    trace.add_event(mk_event("e2", 20))
    check(len(trace.events) == 2, "Trace collects its events")
    check(not hasattr(trace, "total"), "Trace exposes no stored total (derive/ owns totals)")

    # the model does NOT compute totals, but the raw ingredients are reachable for derive/
    naive = sum(ev.event_contributing_tokens for ev in trace.events)
    check(naive == 30, "events under a trace expose event_contributing_tokens for rollup")

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
