"""Post-assembly reconciliation helpers for event collections."""

from __future__ import annotations

from collections.abc import Iterable

from tracker.models.token_event import TokenEvent
from tracker.normalization.data_quality import (
    PROVIDER_TOTAL_MISMATCH,
    UNKNOWN_QUANTITY_PRESENT,
    UNVERIFIED_ADDITIVITY,
    normalizer_flags,
)
from tracker.normalization.event_builder import deduplicate_flags
from tracker.normalization.supersession import reconcile_supersession

_NORMALIZER_OWNED_FLAGS = {
    UNVERIFIED_ADDITIVITY,
    UNKNOWN_QUANTITY_PRESENT,
    PROVIDER_TOTAL_MISMATCH,
}


def reconcile_event_quality(event: TokenEvent) -> TokenEvent:
    """Refresh normalizer-owned flags after source-of-truth fields change."""
    foreign_flags = [flag for flag in event.data_quality_flags if flag not in _NORMALIZER_OWNED_FLAGS]
    event.data_quality_flags = deduplicate_flags(
        [
            *foreign_flags,
            *normalizer_flags(event.quantities, event.provider_total_tokens),
        ]
    )
    return event


def reconcile_events(events: Iterable[TokenEvent]) -> list[TokenEvent]:
    """Refresh quality flags, then apply correlation-based supersession."""
    materialized = [reconcile_event_quality(event) for event in events]
    return reconcile_supersession(materialized)


__all__ = ["reconcile_event_quality", "reconcile_events"]
