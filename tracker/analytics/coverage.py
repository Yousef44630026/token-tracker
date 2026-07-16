"""Coverage + exactness rollup for the CoverageExactness sheet. (Phase 9)

All values are DERIVED from the events (nothing stored). The headline number,
``observed_total_contributing_tokens``, is the same one derive/trace_rollup computes — so
the exported sheet can never disagree with the model. The rest are honest quality counts:
how much usage was exactly measured vs estimated vs lost (unknown), how many events carried
a provider total, and how many showed a provider/derived mismatch.

``exactness_ratio`` is computed over ALL quantities (exact + estimate + unknown), never just
the known ones — a denominator of only exact+estimate would let a trace with 90% UNKNOWN
quantities still report "100% exact" as long as the tiny known slice was all exact, which is
precisely the confident-zero-in-disguise INV-6 forbids at the token layer. ``known_exactness_ratio``
is kept as a narrower, explicitly-labeled second lens ("of what we actually measured, how much
was exact") for anyone who wants that specific question answered, but it is never the headline.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from tracker.derive.effective_events import iter_effective_events
from tracker.derive.headline import HeadlineBandAccumulator
from tracker.models.enums import DataQualityFlag, Overlap, PrecisionLevel, Trust
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace


def _ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


@dataclass
class CoverageExactnessAccumulator:
    """Single-pass accumulator for CoverageExactness metrics."""

    observed_total_contributing_tokens: int = 0
    estimated_contributing_tokens: int = 0
    unattributed_tokens: int = 0
    over_attributed_tokens: int = 0
    unverified_independent_tokens: int = 0
    event_count: int = 0
    live_event_count: int = 0
    superseded_event_count: int = 0
    quantity_count: int = 0
    exact_quantity_count: int = 0
    estimate_quantity_count: int = 0
    unknown_quantity_count: int = 0
    unverified_quantity_count: int = 0
    provider_total_mismatch_count: int = 0
    events_with_provider_total: int = 0
    non_authoritative_event_count: int = 0
    authority_missing_event_count: int = 0
    propagation_lost_event_count: int = 0
    partial_stream_estimate_event_count: int = 0
    stream_interrupted_event_count: int = 0
    correlation_id_collision_event_count: int = 0
    headline: HeadlineBandAccumulator = field(default_factory=HeadlineBandAccumulator)

    def add(self, event: TokenEvent) -> None:
        self.headline.add(event)
        self.event_count += 1
        self.observed_total_contributing_tokens += event.event_contributing_tokens
        flags = set(event.data_quality_flags)
        if not event.is_authoritative:
            self.non_authoritative_event_count += 1
        if DataQualityFlag.AUTHORITY_MISSING.value in flags:
            self.authority_missing_event_count += 1
        if DataQualityFlag.PROPAGATION_LOST.value in flags:
            self.propagation_lost_event_count += 1
        if DataQualityFlag.PARTIAL_STREAM_ESTIMATE.value in flags:
            self.partial_stream_estimate_event_count += 1
        if DataQualityFlag.STREAM_INTERRUPTED.value in flags:
            self.stream_interrupted_event_count += 1
        if DataQualityFlag.CORRELATION_ID_COLLISION.value in flags:
            self.correlation_id_collision_event_count += 1
        if event.superseded:
            self.superseded_event_count += 1
        if event.superseded or not event.is_authoritative:
            return

        self.live_event_count += 1
        if event.provider_total_tokens is not None:
            self.events_with_provider_total += 1
        if event.event_total_mismatch not in (None, 0):
            self.provider_total_mismatch_count += 1
        self.unattributed_tokens += event.under_attributed_tokens
        self.over_attributed_tokens += event.over_attributed_tokens

        for quantity in event.quantities:
            self.quantity_count += 1
            if quantity.precision_level == PrecisionLevel.EXACT:
                self.exact_quantity_count += 1
            elif quantity.precision_level == PrecisionLevel.ESTIMATE:
                self.estimate_quantity_count += 1
                self.estimated_contributing_tokens += quantity.quantity_in_total
            elif quantity.precision_level == PrecisionLevel.UNKNOWN:
                self.unknown_quantity_count += 1

            if quantity.trust == Trust.UNVERIFIED:
                self.unverified_quantity_count += 1
            if quantity.trust == Trust.UNVERIFIED and quantity.overlap == Overlap.INDEPENDENT and quantity.quantity is not None:
                self.unverified_independent_tokens += quantity.quantity

    def to_dict(self) -> dict[str, Any]:
        known = self.exact_quantity_count + self.estimate_quantity_count
        band = self.headline.to_band()

        return {
            "observed_total_contributing_tokens": self.observed_total_contributing_tokens,
            "headline_floor_tokens": band.floor_tokens,
            "headline_estimate_tokens": band.estimate_tokens,
            "headline_ceiling_tokens": band.ceiling_tokens,
            "headline_upper_bound_status": band.upper_bound_status,
            "headline_status": band.status,
            "attribution_status": band.attribution_status,
            "capture_completeness_ratio": band.capture_completeness_ratio,
            "total_is_lower_bound": band.total_is_lower_bound,
            "total_is_upper_bound": band.total_is_upper_bound,
            "open_upper_bound_event_count": band.open_upper_bound_event_count,
            "provider_reconciled_event_count": band.provider_reconciled_event_count,
            "estimated_contributing_tokens": self.estimated_contributing_tokens,
            "unattributed_tokens": self.unattributed_tokens,
            "over_attributed_tokens": self.over_attributed_tokens,
            "unverified_independent_tokens": self.unverified_independent_tokens,
            "event_count": self.live_event_count,
            "excluded_event_count": self.event_count - self.live_event_count,
            "superseded_event_count": self.superseded_event_count,
            "quantity_count": self.quantity_count,
            "exact_quantity_count": self.exact_quantity_count,
            "estimate_quantity_count": self.estimate_quantity_count,
            "unknown_quantity_count": self.unknown_quantity_count,
            # Precision says a quantity was MEASURED; this says how many measured-or-not quantities
            # were nonetheless NOT COUNTED because their additivity is unverified (contribute 0).
            # Without this, an exact-but-unverified quantity looks "fully measured" while silently
            # vanishing from the total.
            "unverified_quantity_count": self.unverified_quantity_count,
            "provider_total_mismatch_count": self.provider_total_mismatch_count,
            "events_with_provider_total": self.events_with_provider_total,
            "non_authoritative_event_count": self.non_authoritative_event_count,
            "authority_missing_event_count": self.authority_missing_event_count,
            "propagation_lost_event_count": self.propagation_lost_event_count,
            "partial_stream_estimate_event_count": self.partial_stream_estimate_event_count,
            "stream_interrupted_event_count": self.stream_interrupted_event_count,
            "correlation_id_collision_event_count": self.correlation_id_collision_event_count,
            "coverage_ratio": _ratio(self.events_with_provider_total, self.live_event_count),
            # exact / EVERYTHING (including unknown) - the honest headline (see module docstring).
            "exactness_ratio": _ratio(self.exact_quantity_count, self.quantity_count),
            # exact / (exact + estimate) - a narrower, explicitly-labeled second lens; never the
            # headline, because excluding unknown from its own denominator is what made the old
            # "exactness_ratio" able to read 100% while most of the data was actually missing.
            "known_exactness_ratio": _ratio(self.exact_quantity_count, known),
        }


def build_coverage_exactness_from_events(events: Iterable[TokenEvent]) -> dict[str, Any]:
    """Return CoverageExactness metrics from a streaming event source."""
    accumulator = CoverageExactnessAccumulator()
    for event in iter_effective_events(events):
        accumulator.add(event)
    return accumulator.to_dict()


def build_coverage_exactness(trace: Trace) -> dict[str, Any]:
    """Return the ordered CoverageExactness metrics for a trace."""
    return build_coverage_exactness_from_events(trace.events)
