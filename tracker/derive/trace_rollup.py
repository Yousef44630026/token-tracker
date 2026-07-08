"""Derived trace totals (INV-2) — never stored. (Phase 3)

A trace exposes no stored total. The contributing total is rolled up here from
``event.event_contributing_tokens``, which is itself 0 for any superseded event (INV-5).
So the rollup sums ``quantity_in_total`` (the only summable column) over live events only —
never the raw ``quantity`` column and never ``provider_total_tokens`` across events.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracker.derive.derived_fields import event_contributing_tokens
from tracker.models.enums import Overlap, PrecisionLevel, Trust
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace


def live_authoritative_events(trace: Trace) -> list[TokenEvent]:
    """Events allowed to affect accounting quality metrics."""
    return [event for event in trace.events if not event.superseded and event.is_authoritative]


def observed_total_contributing_tokens(trace: Trace) -> int:
    """Sum of contributing tokens across the trace's events (superseded events count 0)."""
    return sum(event_contributing_tokens(e) for e in trace.events)


def estimated_contributing_tokens(trace: Trace) -> int:
    """Magnitude of estimated quantities currently included in the headline total."""
    return sum(
        q.quantity_in_total
        for event in live_authoritative_events(trace)
        for q in event.quantities
        if q.precision_level == PrecisionLevel.ESTIMATE
    )


def unattributed_tokens(trace: Trace) -> int:
    """Provider-counted tokens not attributable to normalized quantities."""
    return sum(event.under_attributed_tokens for event in live_authoritative_events(trace))


def over_attributed_tokens(trace: Trace) -> int:
    """Tokens attributed above provider totals; high-severity overcount risk."""
    return sum(event.over_attributed_tokens for event in live_authoritative_events(trace))


def unverified_independent_tokens(trace: Trace) -> int:
    """Known independent quantities excluded only because additivity trust is missing."""
    return sum(
        q.quantity or 0
        for event in live_authoritative_events(trace)
        for q in event.quantities
        if q.trust == Trust.UNVERIFIED and q.overlap == Overlap.INDEPENDENT and q.quantity is not None
    )


def headline_band(trace: Trace) -> tuple[int, int, int]:
    """Return ``(floor, estimate, ceiling)`` for the trace headline.

    ``estimate`` is the best current total: observed contributing tokens plus provider-known
    unattributed tokens. ``floor`` removes estimated contributing quantities. ``ceiling`` adds
    known independent unverified quantities. Unknown quantities have no magnitude, so they are
    surfaced through counts/reasons rather than invented as a number.
    """
    observed = observed_total_contributing_tokens(trace)
    estimated = estimated_contributing_tokens(trace)
    unattributed = unattributed_tokens(trace)
    best = observed + unattributed
    floor = max(best - estimated, 0)
    ceiling = best + unverified_independent_tokens(trace)
    return floor, best, ceiling


def capture_completeness_ratio(trace: Trace) -> float:
    """Observed attributed total divided by the finite headline ceiling."""
    _, _, ceiling = headline_band(trace)
    return round(observed_total_contributing_tokens(trace) / ceiling, 4) if ceiling else 0.0


def total_is_lower_bound(trace: Trace) -> bool:
    """True when ``observed_total_contributing_tokens`` is a FLOOR, not a point value.

    The observed total is exact only when every real token was both measured and counted.
    It is a lower bound (the true total is >= it) whenever a LIVE event carries:
      - an ``unverified`` quantity  — a real count we don't trust, excluded as 0; or
      - an ``unknown`` quantity      — a lost measurement, contributing 0 (INV-6); or
      - a provider total we could not reconcile (``event_total_mismatch`` != 0), i.e. the
        provider counted tokens we could not attribute.
    Superseded / non-authoritative events are skipped: they contribute 0 by DESIGN (a
    duplicate or a retired attempt), not because real tokens were lost, so their
    imperfections must not taint the live total's status.
    """
    for e in live_authoritative_events(trace):
        for q in e.quantities:
            if q.trust == Trust.UNVERIFIED:
                return True
            if q.quantity is None or q.precision_level == PrecisionLevel.UNKNOWN:
                return True
        if e.event_total_mismatch not in (None, 0):
            return True
    return False


@dataclass(frozen=True)
class TraceRollup:
    """A small derived snapshot of a trace's totals and event-grain counts.

    ``total_is_lower_bound`` travels WITH the headline total so a consumer can never take the
    number as a point estimate when it is actually a floor (see ``total_is_lower_bound``)."""

    trace_id: str
    observed_total_contributing_tokens: int
    headline_floor_tokens: int
    headline_estimate_tokens: int
    headline_ceiling_tokens: int
    capture_completeness_ratio: float
    unattributed_tokens: int
    over_attributed_tokens: int
    event_count: int
    superseded_event_count: int
    flagged_event_count: int
    total_is_lower_bound: bool


def roll_up(trace: Trace) -> TraceRollup:
    """Compute the derived totals + counts for a trace (all recomputed, nothing stored)."""
    floor, estimate, ceiling = headline_band(trace)
    return TraceRollup(
        trace_id=trace.trace_id,
        observed_total_contributing_tokens=observed_total_contributing_tokens(trace),
        headline_floor_tokens=floor,
        headline_estimate_tokens=estimate,
        headline_ceiling_tokens=ceiling,
        capture_completeness_ratio=capture_completeness_ratio(trace),
        unattributed_tokens=unattributed_tokens(trace),
        over_attributed_tokens=over_attributed_tokens(trace),
        event_count=len(trace.events),
        superseded_event_count=sum(1 for e in trace.events if e.superseded),
        flagged_event_count=sum(1 for e in trace.events if e.data_quality_flags),
        total_is_lower_bound=total_is_lower_bound(trace),
    )
