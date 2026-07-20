"""Provider/surface validation matrix.

This module does not perform provider calls. It summarizes what the local codebase can prove
from registered adapters and fixture coverage: whether a surface exists, whether it has REAL
and/or SIMULATED payloads, and which validation gaps remain visible.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
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
    has_cache_usage: bool = False
    is_stream: bool = False
    capabilities: tuple[str, ...] = ("usage",)


class CertificationStatus(str, Enum):
    """Evidence state for one provider capability.

    ``proven`` requires a captured REAL fixture. A working adapter or a synthetic payload
    can never promote itself beyond ``simulated``. ``unsupported`` is an explicit product
    boundary, not a missing implementation that could otherwise be mistaken for coverage.
    """

    PROVEN = "proven"
    SIMULATED = "simulated"
    UNVALIDATED = "unvalidated"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CapabilityPolicy:
    provider: str
    api_surface: str
    capability: str
    supported: bool = True
    note: str = ""


def fixture_record(
    fixture_name: str,
    adapter_type: type[BaseAPISurfaceAdapter],
    *,
    has_cache_usage: bool | None = None,
    is_stream: bool | None = None,
    capabilities: Sequence[str] | None = None,
) -> FixtureValidationRecord:
    """Build one validation record from a fixture name and mapped adapter class."""
    cache_usage = "cache" in fixture_name if has_cache_usage is None else has_cache_usage
    stream = "stream" in fixture_name if is_stream is None else is_stream
    declared_capabilities = list(capabilities or ("usage",))
    if cache_usage and "cache" not in declared_capabilities:
        declared_capabilities.append("cache")
    if stream and "stream" not in declared_capabilities:
        declared_capabilities.append("stream")
    return FixtureValidationRecord(
        fixture_name=fixture_name,
        provider=adapter_type.provider,
        api_surface=adapter_type.api_surface,
        is_real=fixture_name.endswith(".REAL.json"),
        is_simulated=fixture_name.endswith(".SIMULATED.json"),
        has_cache_usage=cache_usage,
        is_stream=stream,
        capabilities=tuple(dict.fromkeys(declared_capabilities)),
    )


def records_from_fixture_map(
    fixture_adapters: Mapping[str, type[BaseAPISurfaceAdapter]],
    *,
    cache_fixture_names: Sequence[str] | None = None,
    stream_fixture_names: Sequence[str] | None = None,
    fixture_capabilities: Mapping[str, Sequence[str]] | None = None,
) -> list[FixtureValidationRecord]:
    cache_names = set(cache_fixture_names) if cache_fixture_names is not None else None
    stream_names = set(stream_fixture_names) if stream_fixture_names is not None else None
    return [
        fixture_record(
            fixture_name,
            adapter_type,
            has_cache_usage=(fixture_name in cache_names if cache_names is not None else None),
            is_stream=(fixture_name in stream_names if stream_names is not None else None),
            capabilities=(fixture_capabilities or {}).get(fixture_name),
        )
        for fixture_name, adapter_type in sorted(fixture_adapters.items())
    ]


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
        "chat",
        "chat_completions",
        "generate_content",
        "invoke_model",
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


def _certification_status(row: Mapping[str, Any]) -> str:
    if row["real_fixture_count"] and not row["gaps"]:
        return "proven"
    if row["real_fixture_count"]:
        return "partially_proven"
    if row["simulated_fixture_count"]:
        return "simulated"
    return "unvalidated"


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
            "cache_fixture_count": sum(1 for record in records if record.has_cache_usage),
            "real_cache_fixture_count": sum(1 for record in records if record.is_real and record.has_cache_usage),
            "stream_fixture_count": sum(1 for record in records if record.is_stream),
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
        row["certification_status"] = _certification_status(row)
        rows.append(row)
    return rows


def build_capability_certification_matrix(
    fixture_records: Sequence[FixtureValidationRecord],
    policies: Sequence[CapabilityPolicy],
) -> list[dict[str, Any]]:
    """Return explicit, evidence-backed certification for each declared capability."""

    grouped: dict[tuple[str, str, str], list[FixtureValidationRecord]] = defaultdict(list)
    for record in fixture_records:
        for capability in record.capabilities:
            grouped[(record.provider, record.api_surface, capability)].append(record)

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for policy in policies:
        key = (policy.provider, policy.api_surface, policy.capability)
        if key in seen:
            raise ValueError(f"duplicate capability policy: {':'.join(key)}")
        seen.add(key)
        evidence = grouped.get(key, [])
        real = sorted(record.fixture_name for record in evidence if record.is_real)
        simulated = sorted(record.fixture_name for record in evidence if record.is_simulated)
        if not policy.supported:
            status = CertificationStatus.UNSUPPORTED
        elif real:
            status = CertificationStatus.PROVEN
        elif simulated:
            status = CertificationStatus.SIMULATED
        else:
            status = CertificationStatus.UNVALIDATED
        rows.append(
            {
                "provider": policy.provider,
                "api_surface": policy.api_surface,
                "capability": policy.capability,
                "certification_status": status.value,
                "supported": policy.supported,
                "real_fixture_count": len(real),
                "simulated_fixture_count": len(simulated),
                "evidence": [*real, *simulated],
                "note": policy.note,
            }
        )
    return sorted(rows, key=lambda row: (row["provider"], row["api_surface"], row["capability"]))


def summarize_capability_certification(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {status.value: 0 for status in CertificationStatus}
    for row in rows:
        counts[str(row["certification_status"])] += 1
    return {"capability_count": len(rows), **{f"{name}_count": value for name, value in counts.items()}}


def capability_matrix_to_markdown(rows: Sequence[Mapping[str, Any]]) -> str:
    header = (
        "| Certification | Provider | Surface | Capability | Real | Simulated | Note |\n"
        "|---|---|---|---|---:|---:|---|"
    )
    body = [
        "| {certification_status} | {provider} | {api_surface} | {capability} | "
        "{real_fixture_count} | {simulated_fixture_count} | {note} |".format(
            **{**row, "note": str(row["note"]).replace("|", "\\|") or "-"}
        )
        for row in rows
    ]
    return "\n".join([header, *body])


def certification_requirement_failures(
    surface_rows: Sequence[Mapping[str, Any]],
    capability_rows: Sequence[Mapping[str, Any]],
    requirements: Sequence[str],
) -> list[str]:
    """Return unmet ``provider:surface[:capability]`` release requirements."""

    surfaces = {(str(row["provider"]), str(row["api_surface"])): row for row in surface_rows}
    capabilities = {
        (str(row["provider"]), str(row["api_surface"]), str(row["capability"])): row
        for row in capability_rows
    }
    failures: list[str] = []
    for requirement in requirements:
        parts = tuple(part.strip() for part in requirement.split(":"))
        if len(parts) not in {2, 3} or any(not part for part in parts):
            raise ValueError("requirements must use provider:surface[:capability]")
        if len(parts) == 2:
            row = surfaces.get((parts[0], parts[1]))
            actual = row.get("certification_status") if row else "missing"
        else:
            row = capabilities.get((parts[0], parts[1], parts[2]))
            actual = row.get("certification_status") if row else "missing"
        if actual != CertificationStatus.PROVEN.value:
            failures.append(f"{requirement}={actual}")
    return failures


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
    "CapabilityPolicy",
    "CertificationStatus",
    "FixtureValidationRecord",
    "build_capability_certification_matrix",
    "build_provider_validation_matrix",
    "capability_matrix_to_markdown",
    "certification_requirement_failures",
    "fixture_record",
    "matrix_to_markdown",
    "records_from_fixture_map",
    "summarize_capability_certification",
    "summarize_provider_validation",
]
