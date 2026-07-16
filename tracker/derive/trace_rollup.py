"""Derived trace totals over the canonical correlation-effective event view."""

from __future__ import annotations

from dataclasses import dataclass

from tracker.derive.effective_events import effective_events
from tracker.derive.headline import HeadlineBand, HeadlineBandAccumulator
from tracker.models.enums import Overlap, PrecisionLevel, Trust
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace


def live_authoritative_events(trace: Trace) -> list[TokenEvent]:
    """Events allowed to affect accounting and quality metrics."""
    return [event for event in effective_events(trace.events) if not event.superseded and event.is_authoritative]


def _band(events: list[TokenEvent]) -> HeadlineBand:
    accumulator = HeadlineBandAccumulator()
    for event in events:
        accumulator.add(event)
    return accumulator.to_band()


def observed_total_contributing_tokens(trace: Trace) -> int:
    return sum(event.event_contributing_tokens for event in effective_events(trace.events))


def estimated_contributing_tokens(trace: Trace) -> int:
    return sum(
        quantity.quantity_in_total
        for event in live_authoritative_events(trace)
        for quantity in event.quantities
        if quantity.precision_level == PrecisionLevel.ESTIMATE
    )


def unattributed_tokens(trace: Trace) -> int:
    return sum(event.under_attributed_tokens for event in live_authoritative_events(trace))


def over_attributed_tokens(trace: Trace) -> int:
    return sum(event.over_attributed_tokens for event in live_authoritative_events(trace))


def unverified_independent_tokens(trace: Trace) -> int:
    return sum(
        quantity.quantity or 0
        for event in live_authoritative_events(trace)
        for quantity in event.quantities
        if quantity.trust == Trust.UNVERIFIED
        and quantity.overlap == Overlap.INDEPENDENT
        and quantity.quantity is not None
    )


def headline_band(trace: Trace) -> tuple[int, int, int | None]:
    band = _band(effective_events(trace.events))
    return band.floor_tokens, band.estimate_tokens, band.ceiling_tokens


def capture_completeness_ratio(trace: Trace) -> float | None:
    return _band(effective_events(trace.events)).capture_completeness_ratio


def total_is_lower_bound(trace: Trace) -> bool:
    return _band(effective_events(trace.events)).total_is_lower_bound


def total_is_upper_bound(trace: Trace) -> bool:
    return _band(effective_events(trace.events)).total_is_upper_bound


@dataclass(frozen=True)
class TraceRollup:
    trace_id: str
    observed_total_contributing_tokens: int
    headline_floor_tokens: int
    headline_estimate_tokens: int
    headline_ceiling_tokens: int | None
    headline_upper_bound_status: str
    headline_status: str
    attribution_status: str
    capture_completeness_ratio: float | None
    unattributed_tokens: int
    over_attributed_tokens: int
    event_count: int
    superseded_event_count: int
    flagged_event_count: int
    total_is_lower_bound: bool
    total_is_upper_bound: bool
    open_upper_bound_event_count: int
    provider_reconciled_event_count: int


def roll_up(trace: Trace) -> TraceRollup:
    events = effective_events(trace.events)
    live = [event for event in events if not event.superseded and event.is_authoritative]
    accumulator = HeadlineBandAccumulator()
    for event in events:
        accumulator.add(event)
    band = accumulator.to_band()
    return TraceRollup(
        trace_id=trace.trace_id,
        observed_total_contributing_tokens=sum(event.event_contributing_tokens for event in events),
        headline_floor_tokens=band.floor_tokens,
        headline_estimate_tokens=band.estimate_tokens,
        headline_ceiling_tokens=band.ceiling_tokens,
        headline_upper_bound_status=band.upper_bound_status,
        headline_status=band.status,
        attribution_status=band.attribution_status,
        capture_completeness_ratio=band.capture_completeness_ratio,
        unattributed_tokens=sum(event.under_attributed_tokens for event in live),
        over_attributed_tokens=sum(event.over_attributed_tokens for event in live),
        event_count=len(events),
        superseded_event_count=sum(1 for event in events if event.superseded),
        flagged_event_count=sum(1 for event in events if event.data_quality_flags),
        total_is_lower_bound=band.total_is_lower_bound,
        total_is_upper_bound=band.total_is_upper_bound,
        open_upper_bound_event_count=band.open_upper_bound_event_count,
        provider_reconciled_event_count=band.provider_reconciled_event_count,
    )


__all__ = [
    "TraceRollup",
    "capture_completeness_ratio",
    "estimated_contributing_tokens",
    "headline_band",
    "live_authoritative_events",
    "observed_total_contributing_tokens",
    "over_attributed_tokens",
    "roll_up",
    "total_is_lower_bound",
    "total_is_upper_bound",
    "unattributed_tokens",
    "unverified_independent_tokens",
]
