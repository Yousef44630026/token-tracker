"""Extra — Trace.to_dict / from_dict round-trip (INV-2).

Run: python tests/test_trace_serialization.py

A trace serializes its labels, spans (with metadata), and events losslessly, stores no derived
totals, and reloads to an equal Trace that re-derives its totals.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.span import Span  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


tr = Trace(trace_id="t-1", business_id="biz", workflow="rag", environment="prod")
tr.add_span(Span(span_id="s1", trace_id="t-1", span_type="tool", name="search", metadata={"tool_name": "search", "result_tokens": 12}))
tr.add_event(
    TokenEvent(
        event_id="e1",
        request_correlation_id="r1",
        trace_id="t-1",
        span_id="s1",
        provider="openai",
        api_surface="chat_completions",
        quantities=[
            TokenQuantity(TokenType.OUTPUT, 200, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
        ],
        provider_total_tokens=200,
        observation={"authoritative": True},
    )
)

d = tr.to_dict()
back = Trace.from_dict(d)

check(back == tr, "to_dict -> from_dict round-trips to an equal Trace")
check(back.business_id == "biz" and back.workflow == "rag" and back.environment == "prod", "trace labels preserved")
check(back.spans[0].metadata["tool_name"] == "search" and back.spans[0].name == "search", "span + metadata preserved")
check(back.events[0].event_contributing_tokens == 200, "event total re-derives on reload")

# no derived totals in the serialized form
raw = json.dumps(d)
for derived in ("event_contributing_tokens", "quantity_in_total", "included_in_total", "event_total_mismatch"):
    check(derived not in raw, f"derived field '{derived}' absent from serialization")

# empty trace round-trips
empty = Trace(trace_id="empty")
check(Trace.from_dict(empty.to_dict()) == empty, "empty trace round-trips")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
