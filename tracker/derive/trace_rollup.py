"""Derived trace totals (INV-2) — never stored. (Phase 3)

A trace exposes no stored total. The contributing total is rolled up here from
``event.event_contributing_tokens``, which is itself 0 for any superseded event (INV-5).
So the rollup sums ``quantity_in_total`` (the only summable column) over live events only —
never the raw ``quantity`` column and never ``provider_total_tokens`` across events.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracker.derive.derived_fields import event_contributing_tokens
from tracker.models.trace import Trace


def observed_total_contributing_tokens(trace: Trace) -> int:
    """Sum of contributing tokens across the trace's events (superseded events count 0)."""
    return sum(event_contributing_tokens(e) for e in trace.events)


@dataclass(frozen=True)
class TraceRollup:
    """A small derived snapshot of a trace's totals and event-grain counts."""

    trace_id: str
    observed_total_contributing_tokens: int
    event_count: int
    superseded_event_count: int
    flagged_event_count: int


def roll_up(trace: Trace) -> TraceRollup:
    """Compute the derived totals + counts for a trace (all recomputed, nothing stored)."""
    return TraceRollup(
        trace_id=trace.trace_id,
        observed_total_contributing_tokens=observed_total_contributing_tokens(trace),
        event_count=len(trace.events),
        superseded_event_count=sum(1 for e in trace.events if e.superseded),
        flagged_event_count=sum(1 for e in trace.events if e.data_quality_flags),
    )
