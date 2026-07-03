"""Derived trace totals (INV-2) — never stored. (Phase 3)

A trace exposes no stored total. The contributing total is rolled up here from
``event.event_contributing_tokens``, which is itself 0 for any superseded event (INV-5).
So the rollup sums ``quantity_in_total`` (the only summable column) over live events only —
never the raw ``quantity`` column and never ``provider_total_tokens`` across events.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracker.derive.derived_fields import event_contributing_tokens
from tracker.models.enums import Additivity, PrecisionLevel
from tracker.models.trace import Trace


def observed_total_contributing_tokens(trace: Trace) -> int:
    """Sum of contributing tokens across the trace's events (superseded events count 0)."""
    return sum(event_contributing_tokens(e) for e in trace.events)


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
    for e in trace.events:
        if e.superseded or not e.is_authoritative:
            continue
        for q in e.quantities:
            if q.additivity == Additivity.UNVERIFIED:
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
    event_count: int
    superseded_event_count: int
    flagged_event_count: int
    total_is_lower_bound: bool


def roll_up(trace: Trace) -> TraceRollup:
    """Compute the derived totals + counts for a trace (all recomputed, nothing stored)."""
    return TraceRollup(
        trace_id=trace.trace_id,
        observed_total_contributing_tokens=observed_total_contributing_tokens(trace),
        event_count=len(trace.events),
        superseded_event_count=sum(1 for e in trace.events if e.superseded),
        flagged_event_count=sum(1 for e in trace.events if e.data_quality_flags),
        total_is_lower_bound=total_is_lower_bound(trace),
    )
