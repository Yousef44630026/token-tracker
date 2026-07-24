"""Compact per-event aggregation record (scale chantier S5-lite).

`aggregate()` needs a fixed set of scalars per event, not the full typed `TokenEvent`. Rebuilding
a `TokenEvent` from the index (`from_dict` + quantity/observation construction + validation) costs
~80µs/event; a compact record costs ~10µs. This module computes that record ONCE at projection
time so the dashboard/query path reads cheap scalars instead of reconstructing the model.

The headline-band contribution is captured by RUNNING `HeadlineBandAccumulator.add` on a fresh
accumulator for the single event and reading the resulting deltas. Because the accumulator is
purely additive, that is exactly the event's contribution — so the record can never drift from
`HeadlineBandAccumulator.add`: it IS that function.

The record is derived, never source of truth (INV-2): it is recomputed whenever the event's
effective state (supersession, authority, quality flags) changes, and the ledger remains the only
place a number is stored.
"""

from __future__ import annotations

from typing import Any

from tracker.derive.headline import HeadlineBandAccumulator
from tracker.models.enums import Overlap, PrecisionLevel, Trust
from tracker.models.token_event import TokenEvent

RECORD_VERSION = 1


def aggregation_record(event: TokenEvent) -> dict[str, Any]:
    """Compute the compact aggregation record for one already-effective event."""
    headline = HeadlineBandAccumulator()
    headline.add(event)

    exact = estimated = unverified = 0
    unknown = 0
    for quantity in event.quantities:
        if quantity.precision_level == PrecisionLevel.EXACT:
            exact += quantity.quantity_in_total
        elif quantity.precision_level == PrecisionLevel.ESTIMATE:
            estimated += quantity.quantity_in_total
        elif quantity.precision_level == PrecisionLevel.UNKNOWN or quantity.quantity is None:
            unknown += 1
        if quantity.trust == Trust.UNVERIFIED and quantity.overlap == Overlap.INDEPENDENT and quantity.quantity is not None:
            unverified += quantity.quantity

    return {
        "v": RECORD_VERSION,
        "ts": event.timestamp,
        "flags": list(event.data_quality_flags),
        "superseded": bool(event.superseded),
        "authoritative": bool(event.is_authoritative),
        "contrib": event.event_contributing_tokens,
        "provider_total_present": event.provider_total_tokens is not None,
        "mismatch_nonzero": event.event_total_mismatch not in (None, 0),
        "under": event.under_attributed_tokens,
        "over": event.over_attributed_tokens,
        "request_id": event.request_correlation_id or event.event_id,
        "duration_observed": event.observation.get("duration_ms") is not None,
        "exact": exact,
        "estimated": estimated,
        "unverified": unverified,
        "unknown": unknown,
        "service": str(event.observation.get("service_name") or "unknown"),
        "provider": event.provider or "unknown",
        "model": event.model or "unknown",
        # HeadlineBandAccumulator deltas, in field order (see apply_headline_record).
        "hl": [
            headline.observed_tokens,
            headline.floor_tokens,
            headline.estimate_tokens,
            headline.finite_ceiling_tokens,
            headline.open_upper_bound_event_count,
            headline.provider_reconciled_event_count,
            headline.under_attributed_tokens,
            headline.over_attributed_tokens,
        ],
    }


def apply_headline_record(headline: HeadlineBandAccumulator, hl: list[int]) -> None:
    """Add one record's stored headline deltas into a live accumulator."""
    headline.observed_tokens += hl[0]
    headline.floor_tokens += hl[1]
    headline.estimate_tokens += hl[2]
    headline.finite_ceiling_tokens += hl[3]
    headline.open_upper_bound_event_count += hl[4]
    headline.provider_reconciled_event_count += hl[5]
    headline.under_attributed_tokens += hl[6]
    headline.over_attributed_tokens += hl[7]


__all__ = ["aggregation_record", "apply_headline_record", "RECORD_VERSION"]
