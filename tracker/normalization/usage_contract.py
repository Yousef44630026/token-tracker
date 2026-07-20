"""Shared provider-usage contract checks for response and streaming paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracker.models.enums import DataQualityFlag

if TYPE_CHECKING:
    from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage


def inspect_usage_contract(
    adapter: BaseAPISurfaceAdapter,
    usage: NormalizedUsage,
) -> tuple[list[str], tuple[str, ...]]:
    """Return normalized flags and bounded unknown usage-field paths.

    Every ingestion path must call this after adapter extraction. Keeping the check here
    prevents response normalization, direct stream consumption, and proxy streaming from
    drifting into different data-quality behavior.
    """
    flags = list(dict.fromkeys(usage.data_quality_flags))
    raw_missing = DataQualityFlag.RAW_USAGE_MISSING.value
    if not usage.quantities and raw_missing not in flags:
        flags.append(raw_missing)

    detector = getattr(adapter, "unmapped_usage_token_paths", None)
    raw_usage = getattr(usage, "raw_usage", None)
    unmapped_paths = detector(raw_usage) if callable(detector) else ()
    drift = DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value
    if unmapped_paths and drift not in flags:
        flags.append(drift)
    return flags, unmapped_paths


def usage_contract_observation(unmapped_paths: tuple[str, ...] | list[str]) -> dict[str, object]:
    """Build low-cardinality audit metadata for unknown provider usage fields."""
    bounded = sorted(set(unmapped_paths))[:8]
    if not bounded:
        return {}
    return {
        "unmapped_usage_fields": bounded,
        "unmapped_usage_field_count": len(bounded),
    }


__all__ = ["inspect_usage_contract", "usage_contract_observation"]
