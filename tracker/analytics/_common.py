"""Shared helpers for derived analytics.

The analytics package deliberately derives metrics from the source-of-truth models instead
of storing them on Trace/Event/Span. These helpers keep filtering and small math rules
consistent across the individual metric modules.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tracker.derive.effective_events import effective_events
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, Trust
from tracker.models.span import Span
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.models.trace import Trace


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_non_negative_number(value: Any) -> bool:
    return is_number(value) and value >= 0


def round_metric(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def ratio(numerator: float, denominator: float, digits: int = 6) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, digits)


def authoritative_events(trace: Trace) -> list[TokenEvent]:
    """Events allowed into operational metrics and totals."""
    return [event for event in effective_events(trace.events) if not event.superseded and event.is_authoritative]


def known_quantity(quantity: TokenQuantity) -> bool:
    return quantity.quantity is not None and quantity.precision_level != PrecisionLevel.UNKNOWN


def verified_quantity(quantity: TokenQuantity) -> bool:
    return known_quantity(quantity) and quantity.trust != Trust.UNVERIFIED


def quantity_sum(
    events: Iterable[TokenEvent],
    token_type: TokenType,
    *,
    include_subtotals: bool = True,
    include_unverified: bool = False,
) -> int:
    """Sum known quantities of one token type across events."""
    total = 0
    for event in events:
        for quantity in event.quantities:
            if quantity.token_type != token_type or not known_quantity(quantity):
                continue
            if not include_unverified and quantity.trust == Trust.UNVERIFIED:
                continue
            if not include_subtotals and quantity.additivity == Additivity.SUBTOTAL_OF:
                continue
            total += quantity.quantity or 0
    return total


def event_input_tokens(event: TokenEvent) -> int:
    """Prompt/input tokens that are authoritative for one event."""
    return sum(
        quantity.quantity_in_total
        for quantity in event.quantities
        if quantity.token_type
        in (
            TokenType.INPUT,
            TokenType.CACHED_INPUT,
            TokenType.CACHE_CREATION_INPUT,
            TokenType.EMBEDDING,
            TokenType.RERANK_INPUT,
            TokenType.AUDIO_INPUT,
            TokenType.IMAGE_INPUT,
            TokenType.VIDEO_INPUT,
        )
    )


def event_output_tokens(event: TokenEvent) -> int:
    """Output/generation tokens that are authoritative for one event."""
    return sum(
        quantity.quantity_in_total
        for quantity in event.quantities
        if quantity.token_type
        in (
            TokenType.OUTPUT,
            TokenType.REASONING,
            TokenType.THINKING,
            TokenType.RERANK_OUTPUT,
            TokenType.AUDIO_OUTPUT,
        )
    )


def event_duration_ms(event: TokenEvent) -> float | None:
    """Return an event's wall-clock duration from its observation, or None.

    The observation contract recognizes three duration keys; they are tried in a fixed order so
    every analytics view (latency, service attribution, ...) reads the SAME number for the same
    event and cannot disagree about whether duration data exists.
    """
    for key in ("duration_ms", "total_duration_ms", "provider_duration_ms"):
        value = event.observation.get(key)
        if is_non_negative_number(value):
            return float(value)
    return None


def span_duration_ms(span: Span) -> float | None:
    """Return duration from metadata or ISO-ish timestamps when available.

    The codebase mostly stores durations directly in metadata/observation. We avoid adding a
    datetime dependency here because current spans do not guarantee timestamp format.
    """
    for key in ("duration_ms", "latency_ms", "elapsed_ms"):
        value = span.metadata.get(key)
        if is_non_negative_number(value):
            return float(value)
    return None


def span_events(trace: Trace, span_id: str) -> list[TokenEvent]:
    return [event for event in trace.events if event.span_id == span_id]


def first_quantity_metadata(event: TokenEvent, key: str) -> Any:
    for quantity in event.quantities:
        if key in quantity.metadata:
            return quantity.metadata[key]
    return None
