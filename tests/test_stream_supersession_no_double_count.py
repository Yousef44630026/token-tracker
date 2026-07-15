"""Phase 3 — correlated stream supersession (INV-5).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_stream_supersession_no_double_count.py

An interrupted stream yields a partial output ESTIMATE; the final usage arrives later with
the SAME request_correlation_id. The reconciler matches them by request_correlation_id
(NOT span_id), marks the partial superseded_by the final, and raises the 'superseded' flag.
The contributing total must be the FINAL usage only — never partial + final.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402

_failures = 0
RCID = "rcid-shared"


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


partial = TokenEvent(
    event_id="evt-partial",
    request_correlation_id=RCID,
    trace_id="t-1",
    span_id="s-1",  # same span as final on a clean stream
    quantities=[
        TokenQuantity(
            token_type=TokenType.OUTPUT,
            quantity=40,  # partial guess from the tokenizer
            precision_level=PrecisionLevel.ESTIMATE,
            usage_source=UsageSource.PARTIAL_STREAM_TOKENIZER,
            additivity=Additivity.TOTAL_CONTRIBUTING,
        )
    ],
)
final = TokenEvent(
    event_id="evt-final",
    request_correlation_id=RCID,
    trace_id="t-1",
    span_id="s-1",
    quantities=[
        TokenQuantity(
            token_type=TokenType.OUTPUT,
            quantity=210,  # the real, full output
            precision_level=PrecisionLevel.EXACT,
            usage_source=UsageSource.PROVIDER_STREAM_FINAL,
            additivity=Additivity.TOTAL_CONTRIBUTING,
        )
    ],
    provider_total_tokens=210,
)

events = [partial, final]
reconcile_supersession(events)

check(partial.superseded is True, "partial is marked superseded")
check(partial.superseded_by == "evt-final", "partial.superseded_by == final.event_id")
check("superseded" in partial.data_quality_flags, "'superseded' flag is raised on the partial")
check(final.superseded is False, "the final usage is NOT superseded")

total = sum(e.event_contributing_tokens for e in events)
check(total == 210, f"contributing total == final usage only (got {total})")
check(total != 40 + 210, "total is NOT partial + final (no double count)")

# An interrupted stream can retain exact provider input alongside its output estimate.
# If a later incomplete final reports input only, the shared input must still be counted once.
enriched_partial = TokenEvent(
    event_id="evt-enriched-partial",
    request_correlation_id="rcid-enriched",
    trace_id="t-2",
    span_id="s-2",
    quantities=[
        TokenQuantity(
            TokenType.INPUT,
            100,
            PrecisionLevel.EXACT,
            UsageSource.PROVIDER_RESPONSE,
            Additivity.TOTAL_CONTRIBUTING,
        ),
        TokenQuantity(
            TokenType.OUTPUT,
            40,
            PrecisionLevel.ESTIMATE,
            UsageSource.PARTIAL_STREAM_TOKENIZER,
            Additivity.TOTAL_CONTRIBUTING,
        ),
    ],
    data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
)
input_only_final = TokenEvent(
    event_id="evt-input-only-final",
    request_correlation_id="rcid-enriched",
    trace_id="t-2",
    span_id="s-2",
    quantities=[
        TokenQuantity(
            TokenType.INPUT,
            100,
            PrecisionLevel.EXACT,
            UsageSource.PROVIDER_STREAM_FINAL,
            Additivity.TOTAL_CONTRIBUTING,
        )
    ],
    provider_total_tokens=100,
)
enriched_events = [enriched_partial, input_only_final]
reconcile_supersession(enriched_events)
enriched_total = sum(event.event_contributing_tokens for event in enriched_events)
check(enriched_partial.superseded is True, "input-only final supersedes an enriched partial with overlapping provider input")
check(enriched_total == 100, f"enriched partial and input-only final count shared input once (got {enriched_total})")
check(enriched_total != 240, "enriched partial input is never added to the same final input")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
