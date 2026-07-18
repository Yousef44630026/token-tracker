"""Phase 6 — precision per quantity + unknown-reason classifier (INV-3 / INV-6).

Run: python tests/test_precision_per_quantity.py

Precision is a per-quantity property, orthogonal to token_type (INV-3): one event may hold
an EXACT input, an ESTIMATE output, and an UNKNOWN reasoning all at once. A None quantity is
always UNKNOWN — never a confident zero (INV-6) — and carries a classified UnknownReason.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.classification.precision_classifier import classify_precision  # noqa: E402
from tracker.classification.unknown_reason_classifier import classify_unknown_reason  # noqa: E402
from tracker.models.enums import (  # noqa: E402
    Additivity,  # noqa: E402
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


# --- precision is derived from the usage source (with a known quantity) ---
check(classify_precision(UsageSource.PROVIDER_RESPONSE, 100) == PrecisionLevel.EXACT, "provider_response -> exact")
check(classify_precision(UsageSource.PROVIDER_STREAM_FINAL, 100) == PrecisionLevel.EXACT, "stream_final -> exact")
check(classify_precision(UsageSource.PARTIAL_STREAM_TOKENIZER, 40) == PrecisionLevel.ESTIMATE, "partial_stream -> estimate")
check(classify_precision(UsageSource.LOCAL_TOKENIZER, 40) == PrecisionLevel.ESTIMATE, "local_tokenizer -> estimate")
check(classify_precision(UsageSource.HISTORICAL_FORECAST, 40) == PrecisionLevel.ESTIMATE, "historical_forecast -> estimate")
check(classify_precision(UsageSource.NONE, None) == PrecisionLevel.UNKNOWN, "no source + None -> unknown")

# --- INV-6: a None quantity is UNKNOWN even from an otherwise-exact source ---
check(classify_precision(UsageSource.PROVIDER_RESPONSE, None) == PrecisionLevel.UNKNOWN, "None quantity -> unknown, never a confident zero")


# --- precision is PER QUANTITY: one event, three different precisions ---
def q(tt, qty, src):
    return TokenQuantity(
        token_type=tt,
        quantity=qty,
        precision_level=classify_precision(src, qty),
        usage_source=src,
        additivity=Additivity.TOTAL_CONTRIBUTING if tt != TokenType.REASONING else Additivity.SUBTOTAL_OF,
        subtotal_of="output" if tt == TokenType.REASONING else None,
        unknown_reason=(classify_unknown_reason(interrupted=True) if qty is None else None),
    )


event = TokenEvent(
    event_id="e1",
    request_correlation_id="r1",
    trace_id="t1",
    span_id="s1",
    quantities=[
        q(TokenType.INPUT, 1000, UsageSource.PROVIDER_RESPONSE),
        q(TokenType.OUTPUT, 40, UsageSource.PARTIAL_STREAM_TOKENIZER),
        q(TokenType.REASONING, None, UsageSource.NONE),
    ],
    observation={"authoritative": True},
)
precisions = [qq.precision_level for qq in event.quantities]
check(precisions == [PrecisionLevel.EXACT, PrecisionLevel.ESTIMATE, PrecisionLevel.UNKNOWN], "each quantity keeps its own precision")
check(event.quantities[2].quantity is None, "the unknown quantity stays None (not 0)")
check(event.quantities[2].unknown_reason == UnknownReason.STREAM_INTERRUPTED, "unknown quantity carries a reason")

# --- unknown-reason classifier maps each cause; None when nothing is wrong ---
check(classify_unknown_reason(timed_out=True) == UnknownReason.STREAM_TIMEOUT, "timed_out -> stream_timeout")
check(classify_unknown_reason(interrupted=True) == UnknownReason.STREAM_INTERRUPTED, "interrupted -> stream_interrupted")
check(classify_unknown_reason(raw_usage_missing=True) == UnknownReason.RAW_USAGE_MISSING, "raw_usage_missing -> raw_usage_missing")
check(classify_unknown_reason(provider_omitted=True) == UnknownReason.PROVIDER_OMITTED, "provider_omitted -> provider_omitted")
check(classify_unknown_reason(normalization_error=True) == UnknownReason.NORMALIZATION_ERROR, "normalization_error -> normalization_error")
check(classify_unknown_reason() is None, "no cause -> None (the quantity is known)")
# precedence: an adapter error is the most fundamental cause
check(
    classify_unknown_reason(normalization_error=True, interrupted=True) == UnknownReason.NORMALIZATION_ERROR,
    "normalization_error wins over interrupted (precedence)",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
