"""Directional, audit-grade headline bounds derived from effective events."""

from __future__ import annotations

from dataclasses import dataclass

from tracker.models.enums import DataQualityFlag, Overlap, PrecisionLevel, Trust
from tracker.models.token_event import TokenEvent

_USAGE_LOSS_FLAGS = frozenset(
    {
        DataQualityFlag.RAW_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_RESPONSE_UNPARSEABLE.value,
        DataQualityFlag.NORMALIZATION_ERROR.value,
    }
)


def _known_independent_tokens(event: TokenEvent) -> int:
    return sum(
        quantity.quantity or 0
        for quantity in event.quantities
        if quantity.overlap == Overlap.INDEPENDENT and quantity.quantity is not None
    )


def _has_open_independent_quantity(event: TokenEvent) -> bool:
    return any(
        quantity.overlap == Overlap.INDEPENDENT
        and (
            quantity.quantity is None
            or quantity.precision_level in {PrecisionLevel.UNKNOWN, PrecisionLevel.ESTIMATE}
        )
        for quantity in event.quantities
    )


@dataclass(frozen=True)
class HeadlineBand:
    floor_tokens: int
    estimate_tokens: int
    ceiling_tokens: int | None
    upper_bound_status: str
    status: str
    attribution_status: str
    capture_completeness_ratio: float | None
    total_is_lower_bound: bool
    total_is_upper_bound: bool
    open_upper_bound_event_count: int
    provider_reconciled_event_count: int


@dataclass
class HeadlineBandAccumulator:
    """Single-pass bound accumulator over already-effective events."""

    observed_tokens: int = 0
    floor_tokens: int = 0
    estimate_tokens: int = 0
    finite_ceiling_tokens: int = 0
    open_upper_bound_event_count: int = 0
    provider_reconciled_event_count: int = 0
    under_attributed_tokens: int = 0
    over_attributed_tokens: int = 0

    def add(self, event: TokenEvent) -> None:
        flags = set(event.data_quality_flags)
        if event.superseded:
            # A proven correlation collision is not an ordinary duplicate. The retained
            # event remains the conservative point estimate, while the dropped measurement
            # expands the ceiling so the headline cannot still claim false exactness.
            if flags & {
                DataQualityFlag.CORRELATION_ID_COLLISION.value,
                DataQualityFlag.DUPLICATE_FINAL_UNVERIFIED.value,
            }:
                known_tokens = _known_independent_tokens(event)
                if event.provider_total_tokens is not None:
                    self.finite_ceiling_tokens += max(event.provider_total_tokens, known_tokens)
                else:
                    self.finite_ceiling_tokens += known_tokens
                    if not event.quantities or _has_open_independent_quantity(event):
                        self.open_upper_bound_event_count += 1
            return
        if not event.is_authoritative:
            # Failed normalization or absent provider usage can hide a real call even though
            # the authority gate correctly excludes it from the canonical point total.
            if flags & _USAGE_LOSS_FLAGS:
                self.open_upper_bound_event_count += 1
            return

        observed = event.event_contributing_tokens
        self.observed_tokens += observed
        self.under_attributed_tokens += event.under_attributed_tokens
        self.over_attributed_tokens += event.over_attributed_tokens

        if event.provider_total_tokens is not None:
            provider_total = event.provider_total_tokens
            self.floor_tokens += provider_total
            self.estimate_tokens += provider_total
            self.finite_ceiling_tokens += provider_total
            if event.event_total_mismatch not in (None, 0):
                self.provider_reconciled_event_count += 1
            return

        estimated = sum(
            quantity.quantity_in_total
            for quantity in event.quantities
            if quantity.precision_level == PrecisionLevel.ESTIMATE
        )
        known_unverified_independent = sum(
            quantity.quantity or 0
            for quantity in event.quantities
            if quantity.trust == Trust.UNVERIFIED
            and quantity.overlap == Overlap.INDEPENDENT
            and quantity.quantity is not None
        )
        has_open_quantity = _has_open_independent_quantity(event) or bool(flags & _USAGE_LOSS_FLAGS)

        self.floor_tokens += max(observed - estimated, 0)
        self.estimate_tokens += observed
        self.finite_ceiling_tokens += observed + known_unverified_independent
        if has_open_quantity:
            self.open_upper_bound_event_count += 1

    def to_band(self) -> HeadlineBand:
        ceiling = None if self.open_upper_bound_event_count else self.finite_ceiling_tokens
        if self.under_attributed_tokens and self.over_attributed_tokens:
            attribution_status = "mixed"
        elif self.under_attributed_tokens:
            attribution_status = "under_attributed"
        elif self.over_attributed_tokens:
            attribution_status = "over_attributed"
        else:
            attribution_status = "exact"

        if ceiling is None:
            status = "open"
        elif self.floor_tokens != ceiling:
            status = "bounded"
        elif self.provider_reconciled_event_count:
            status = "provider_reconciled"
        else:
            status = "exact"

        lower_bound = (
            self.over_attributed_tokens == 0
            and self.observed_tokens <= self.floor_tokens
            and status != "exact"
        )
        upper_bound = (
            self.under_attributed_tokens == 0
            and self.over_attributed_tokens > 0
            and ceiling is not None
            and self.observed_tokens >= ceiling
        )
        completeness = None
        if ceiling not in (None, 0) and self.over_attributed_tokens == 0:
            completeness = round(self.observed_tokens / ceiling, 4)

        return HeadlineBand(
            floor_tokens=self.floor_tokens,
            estimate_tokens=self.estimate_tokens,
            ceiling_tokens=ceiling,
            upper_bound_status="open" if ceiling is None else "finite",
            status=status,
            attribution_status=attribution_status,
            capture_completeness_ratio=completeness,
            total_is_lower_bound=lower_bound,
            total_is_upper_bound=upper_bound,
            open_upper_bound_event_count=self.open_upper_bound_event_count,
            provider_reconciled_event_count=self.provider_reconciled_event_count,
        )


__all__ = ["HeadlineBand", "HeadlineBandAccumulator"]
