"""Provider/surface validation matrix.

This module does not perform provider calls. It summarizes what the local codebase can prove
from registered adapters and fixture coverage: whether a surface exists, whether it has REAL
and/or SIMULATED payloads, and which validation gaps remain visible.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter
from tracker.adapters.registry import available_adapters, create_adapter


@dataclass(frozen=True)
class FixtureValidationRecord:
    fixture_name: str
    provider: str
    api_surface: str
    is_real: bool
    is_simulated: bool


def fixture_record(
    fixture_name: str,
    adapter_type: type[BaseAPISurfaceAdapter],
) -> FixtureValidationRecord:
    """Build one validation record from a fixture name and mapped adapter class."""
    return FixtureValidationRecord(
        fixture_name=fixture_name,
        provider=adapter_type.provider,
        api_surface=adapter_type.api_surface,
        is_real=fixture_name.endswith(".REAL.json"),
        is_simulated=fixture_name.endswith(".SIMULATED.json"),
    )


def records_from_fixture_map(
    fixture_adapters: Mapping[str, type[BaseAPISurfaceAdapter]],
) -> list[FixtureValidationRecord]:
    return [fixture_record(fixture_name, adapter_type) for fixture_name, adapter_type in sorted(fixture_adapters.items())]


def _adapter_name(provider: str, surface: str) -> str:
    try:
        return type(create_adapter(provider, surface)).__name__
    except Exception:
        return ""


def _gaps(row: dict[str, Any]) -> list[str]:
    gaps = []
    if row["real_fixture_count"] == 0:
        gaps.append("no_real_fixture")
    if row["simulated_fixture_count"] == 0:
        gaps.append("no_simulated_fixture")
    if row["cache_fixture_count"] and row["real_cache_fixture_count"] == 0:
        gaps.append("cache_not_real_validated")
    if row["stream_fixture_count"] == 0 and row["api_surface"] in {
        "chat_completions",
        "responses",
        "messages",
        "converse",
    }:
        gaps.append("no_stream_fixture")
    return gaps


def _status(row: dict[str, Any]) -> str:
    if row["fixture_count"] == 0:
        return "fail"
    if row["real_fixture_count"] == 0:
        return "warn"
    if "cache_not_real_validated" in row["gaps"]:
        return "warn"
    if "no_stream_fixture" in row["gaps"]:
        return "warn"
    return "pass"


def build_provider_validation_matrix(
    fixture_records: Sequence[FixtureValidationRecord],
    adapter_pairs: Sequence[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Return one row per registered provider/API surface."""
    pairs = sorted(adapter_pairs or available_adapters())
    grouped: dict[tuple[str, str], list[FixtureValidationRecord]] = defaultdict(list)
    for record in fixture_records:
        grouped[(record.provider, record.api_surface)].append(record)

    rows = []
    for provider, surface in pairs:
        records = grouped.get((provider, surface), [])
        names = sorted(record.fixture_name for record in records)
        row = {
            "provider": provider,
            "api_surface": surface,
            "adapter_available": True,
            "adapter_name": _adapter_name(provider, surface),
            "fixture_count": len(records),
            "real_fixture_count": sum(1 for record in records if record.is_real),
            "simulated_fixture_count": sum(1 for record in records if record.is_simulated),
            "cache_fixture_count": sum(1 for record in records if "cache" in record.fixture_name),
            "real_cache_fixture_count": sum(1 for record in records if record.is_real and "cache" in record.fixture_name),
            "stream_fixture_count": sum(1 for record in records if "stream" in record.fixture_name),
            "fixture_names": names,
        }
        row["gaps"] = _gaps(row)
        if row["real_fixture_count"] and row["simulated_fixture_count"]:
            row["validation_level"] = "real_and_simulated"
        elif row["real_fixture_count"]:
            row["validation_level"] = "real_only"
        elif row["simulated_fixture_count"]:
            row["validation_level"] = "simulated_only"
        else:
            row["validation_level"] = "adapter_only"
        row["status"] = _status(row)
        rows.append(row)
    return rows


def summarize_provider_validation(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return pass/warn/fail counts for a provider validation matrix."""
    pass_count = sum(1 for row in rows if row["status"] == "pass")
    warn_count = sum(1 for row in rows if row["status"] == "warn")
    fail_count = sum(1 for row in rows if row["status"] == "fail")
    real_validated = sum(1 for row in rows if row["real_fixture_count"] > 0)
    simulated_validated = sum(1 for row in rows if row["simulated_fixture_count"] > 0)
    adapter_only = sum(1 for row in rows if row["validation_level"] == "adapter_only")
    if fail_count:
        overall = "fail"
    elif warn_count:
        overall = "warn"
    else:
        overall = "pass"
    return {
        "overall_status": overall,
        "surface_count": len(rows),
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "real_validated_surface_count": real_validated,
        "simulated_validated_surface_count": simulated_validated,
        "adapter_only_surface_count": adapter_only,
        "surfaces_with_gaps": sum(1 for row in rows if row["gaps"]),
    }


def matrix_to_markdown(rows: Sequence[dict[str, Any]]) -> str:
    """Render the matrix as a compact Markdown table for docs/reviews."""
    header = "| Status | Provider | Surface | Adapter | Validation | Real | Simulated | Gaps |\n" "|---|---|---|---|---:|---:|---:|---|"
    body = []
    for row in rows:
        body.append(
            "| {status} | {provider} | {api_surface} | {adapter_name} | {validation_level} | "
            "{real_fixture_count} | {simulated_fixture_count} | {gaps} |".format(
                **{
                    **row,
                    "gaps": ", ".join(row["gaps"]) or "-",
                }
            )
        )
    return "\n".join([header, *body])


__all__ = [
    "FixtureValidationRecord",
    "build_provider_validation_matrix",
    "fixture_record",
    "matrix_to_markdown",
    "records_from_fixture_map",
    "summarize_provider_validation",
]
