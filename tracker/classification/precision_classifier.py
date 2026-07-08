"""Per-quantity precision classifier (INV-3 / INV-6). (Phase 6)

Precision says HOW WELL a quantity was measured — orthogonal to token_type. It is decided
per quantity (not per event), from the usage source it came from:

    provider_response / provider_stream_final         -> exact
    provider_stream_partial / partial_stream_tokenizer /
        local_tokenizer / historical_forecast         -> estimate
    none / anything else                              -> unknown

``provider_stream_partial`` is the provider's own cumulative count from a mid-stream event:
exact for what was produced so far, but a FLOOR of the final output (the stream was cut), so
as a measurement of the final quantity it is an ESTIMATE, not exact.

A None quantity is ALWAYS ``unknown`` regardless of source — a lost count is never a
confident zero (INV-6).
"""

from __future__ import annotations

from tracker.models.enums import PrecisionLevel, UsageSource

_EXACT_SOURCES = {UsageSource.PROVIDER_RESPONSE, UsageSource.PROVIDER_STREAM_FINAL}
_ESTIMATE_SOURCES = {
    UsageSource.PROVIDER_STREAM_PARTIAL,
    UsageSource.PARTIAL_STREAM_TOKENIZER,
    UsageSource.LOCAL_TOKENIZER,
    UsageSource.HISTORICAL_FORECAST,
}


def classify_precision(usage_source: UsageSource, quantity: int | None) -> PrecisionLevel:
    """Classify the precision of one quantity from its source and whether it is known."""
    if quantity is None:
        return PrecisionLevel.UNKNOWN
    if usage_source in _EXACT_SOURCES:
        return PrecisionLevel.EXACT
    if usage_source in _ESTIMATE_SOURCES:
        return PrecisionLevel.ESTIMATE
    return PrecisionLevel.UNKNOWN
