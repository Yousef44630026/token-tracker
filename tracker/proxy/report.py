"""Reliability summaries for real-call proxy event files."""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from tracker.models.enums import Additivity, PrecisionLevel, TokenType
from tracker.models.token_event import TokenEvent
from tracker.normalization.additivity import assign_additivity

_PROMPT_TYPES = {
    TokenType.INPUT,
    TokenType.CACHED_INPUT,
    TokenType.CACHE_CREATION_INPUT,
}


def _comparison(event: TokenEvent) -> dict | None:
    for quantity in event.quantities:
        value = quantity.metadata.get("prompt_estimate")
        if isinstance(value, dict):
            return value
    return None


def _quantity(event: TokenEvent, token_type: TokenType) -> int:
    return sum(
        quantity.quantity or 0
        for quantity in event.quantities
        if quantity.token_type == token_type and quantity.precision_level == PrecisionLevel.EXACT
    )


def _quantity_metadata_total(
    events: Iterable[TokenEvent],
    token_type: TokenType,
    metadata_key: str,
) -> int:
    return sum(
        value
        for event in events
        for quantity in event.quantities
        if quantity.token_type == token_type
        and isinstance((value := quantity.metadata.get(metadata_key)), int)
        and not isinstance(value, bool)
    )


def _cache_creation_lifetime_detail_counts(events: Iterable[TokenEvent]) -> tuple[int, int]:
    total = 0
    with_lifetime_detail = 0
    for event in events:
        for quantity in event.quantities:
            if (
                quantity.token_type != TokenType.CACHE_CREATION_INPUT
                or quantity.precision_level != PrecisionLevel.EXACT
                or not quantity.quantity
            ):
                continue
            total += 1
            has_5m = isinstance(
                quantity.metadata.get("ephemeral_5m_input_tokens"),
                int,
            ) and not isinstance(
                quantity.metadata.get("ephemeral_5m_input_tokens"),
                bool,
            )
            has_1h = isinstance(
                quantity.metadata.get("ephemeral_1h_input_tokens"),
                int,
            ) and not isinstance(
                quantity.metadata.get("ephemeral_1h_input_tokens"),
                bool,
            )
            if has_5m or has_1h:
                with_lifetime_detail += 1
    return total, with_lifetime_detail


def _provider_prompt_tokens(event: TokenEvent, comparison: dict | None) -> int:
    if comparison is not None:
        stored = comparison.get("provider_prompt_tokens")
        if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 0:
            return stored
    return sum(
        quantity.quantity or 0
        for quantity in event.quantities
        if quantity.token_type in _PROMPT_TYPES
        and quantity.precision_level == PrecisionLevel.EXACT
        and assign_additivity(
            event.provider or "",
            event.api_surface or "",
            quantity.token_type,
        )[0]
        == Additivity.TOTAL_CONTRIBUTING
    )


def _uses_legacy_rules(event: TokenEvent) -> bool:
    return any(
        assign_additivity(
            event.provider or "",
            event.api_surface or "",
            quantity.token_type,
        )
        != (quantity.additivity, quantity.subtotal_of)
        for quantity in event.quantities
    )


def _current_rule_total(event: TokenEvent) -> int:
    if event.superseded or not event.is_authoritative:
        return 0
    total = 0
    for quantity in event.quantities:
        additivity, _ = assign_additivity(
            event.provider or "",
            event.api_surface or "",
            quantity.token_type,
        )
        if additivity == Additivity.TOTAL_CONTRIBUTING and quantity.quantity is not None:
            total += quantity.quantity
    return total


def _is_incomplete(event: TokenEvent) -> bool:
    if event.observation.get("authoritative") is False:
        return True
    return any(
        flag in event.data_quality_flags
        for flag in (
            "input_estimate_only",
            "provider_usage_missing",
            "provider_stream_usage_missing",
            "proxy_upstream_error",
        )
    )


def _is_countable(event: TokenEvent) -> bool:
    return not event.superseded and not _is_incomplete(event)


def _prompt_group_identity(event: TokenEvent) -> tuple[int, str, str, str | None] | None:
    observation = event.observation
    sequence = observation.get("suite_prompt_sequence")
    fingerprint = observation.get("suite_prompt_fingerprint")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0 or not isinstance(fingerprint, str) or not fingerprint:
        return None
    label = observation.get("suite_prompt_label")
    source = observation.get("suite_prompt_source")
    return (
        sequence,
        label if isinstance(label, str) and label else f"prompt-{sequence}",
        fingerprint,
        source if isinstance(source, str) and source else None,
    )


def _comparison_totals(events: Iterable[TokenEvent]) -> tuple[int, int, int, int]:
    comparison_events = 0
    estimate_tokens = 0
    provider_prompt_tokens = 0
    absolute_error = 0
    for event in events:
        if not _is_countable(event):
            continue
        comparison = _comparison(event)
        if comparison is None:
            continue
        estimate = comparison.get("quantity")
        if not isinstance(estimate, int) or isinstance(estimate, bool) or estimate < 0:
            continue
        prompt_total = _provider_prompt_tokens(event, comparison)
        comparison_events += 1
        estimate_tokens += estimate
        provider_prompt_tokens += prompt_total
        absolute_error += abs(prompt_total - estimate)
    return comparison_events, estimate_tokens, provider_prompt_tokens, absolute_error


def _prompt_group_summaries(events: Iterable[TokenEvent]) -> list[dict]:
    groups: dict[tuple[int, str, str, str | None], list[TokenEvent]] = {}
    for event in events:
        identity = _prompt_group_identity(event)
        if identity is None:
            continue
        groups.setdefault(identity, []).append(event)

    return _prompt_group_summaries_from_groups(groups)


def _prompt_group_summaries_from_groups(groups: dict[tuple[int, str, str, str | None], list[TokenEvent]]) -> list[dict]:
    summaries: list[dict] = []
    for (sequence, label, fingerprint, source), group_events in sorted(
        groups.items(),
        key=lambda item: item[0][0],
    ):
        countable_events = [event for event in group_events if _is_countable(event)]
        comparison = _comparison_totals(group_events)
        provider_prompt_tokens = comparison[2]
        if comparison[0] == 0:
            provider_prompt_tokens = sum(_provider_prompt_tokens(event, None) for event in countable_events)
        estimate_tokens = comparison[1]
        coverage = round(estimate_tokens / provider_prompt_tokens, 6) if comparison[0] > 0 and provider_prompt_tokens else None
        summaries.append(
            {
                "sequence": sequence,
                "label": label,
                "fingerprint": fingerprint,
                "source": source,
                "events": len(group_events),
                "exact_usage_events": sum(
                    1
                    for event in countable_events
                    if any(quantity.precision_level == PrecisionLevel.EXACT for quantity in event.quantities)
                ),
                "incomplete_events": sum(1 for event in group_events if _is_incomplete(event)),
                "superseded_events": sum(1 for event in group_events if event.superseded),
                "statuses": dict(sorted(Counter(event.observation.get("status", "legacy") for event in group_events).items())),
                "fresh_input_tokens": sum(_quantity(event, TokenType.INPUT) for event in countable_events),
                "cache_read_input_tokens": sum(_quantity(event, TokenType.CACHED_INPUT) for event in countable_events),
                "cache_creation_input_tokens": sum(_quantity(event, TokenType.CACHE_CREATION_INPUT) for event in countable_events),
                "output_tokens": sum(_quantity(event, TokenType.OUTPUT) for event in countable_events),
                "contributing_tokens": sum(_current_rule_total(event) for event in countable_events),
                "comparison_events": comparison[0],
                "estimated_prompt_tokens": estimate_tokens,
                "provider_prompt_tokens": provider_prompt_tokens,
                "absolute_estimation_error": comparison[3],
                "estimate_coverage_ratio": coverage,
            }
        )
    return summaries


def summarize_events(events: Iterable[TokenEvent]) -> dict:
    """Return aggregate facts without changing or reclassifying stored events."""
    event_count = 0
    models: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    sessions: set[object] = set()
    prompt_fingerprints: set[object] = set()
    prompt_groups: dict[tuple[int, str, str, str | None], list[TokenEvent]] = {}
    max_prompt_cycle = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    duration_total = 0.0
    duration_count = 0
    ttft_total = 0.0
    ttft_count = 0
    provider_request_id_count = 0
    provider_response_id_count = 0
    exact_usage_events = 0
    incomplete_events = 0
    superseded_events = 0
    legacy_rule_events = 0
    comparison_events = 0
    estimate_tokens = 0
    provider_prompt_tokens = 0
    absolute_error = 0
    fresh_input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    cache_creation_5m_input_tokens = 0
    cache_creation_1h_input_tokens = 0
    cache_creation_quantity_count = 0
    cache_creation_lifetime_detail_count = 0
    output_tokens = 0
    stored_contributing_tokens = 0
    incomplete_estimated_tokens = 0
    contributing_tokens = 0

    for event in events:
        event_count += 1
        models[event.model or "unknown"] += 1
        statuses[event.observation.get("status", "legacy")] += 1
        if event.observation.get("proxy_session_id"):
            sessions.add(event.observation.get("proxy_session_id"))
        if event.observation.get("prompt_fingerprint"):
            prompt_fingerprints.add(event.observation.get("prompt_fingerprint"))
        prompt_cycle = event.observation.get("prompt_cycle", 0)
        if isinstance(prompt_cycle, int) and not isinstance(prompt_cycle, bool):
            max_prompt_cycle = max(max_prompt_cycle, prompt_cycle)
        if event.timestamp:
            first_timestamp = event.timestamp if first_timestamp is None else min(first_timestamp, event.timestamp)
            last_timestamp = event.timestamp if last_timestamp is None else max(last_timestamp, event.timestamp)
        duration = event.observation.get("duration_ms")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            duration_total += duration
            duration_count += 1
        ttft = event.observation.get("time_to_first_token_ms")
        if isinstance(ttft, (int, float)) and not isinstance(ttft, bool):
            ttft_total += ttft
            ttft_count += 1
        if event.observation.get("provider_request_id"):
            provider_request_id_count += 1
        if event.observation.get("provider_response_id"):
            provider_response_id_count += 1
        identity = _prompt_group_identity(event)
        if identity is not None:
            prompt_groups.setdefault(identity, []).append(event)

        incomplete = _is_incomplete(event)
        countable = _is_countable(event)
        if event.superseded:
            superseded_events += 1
        if any(quantity.precision_level == PrecisionLevel.EXACT for quantity in event.quantities) and countable:
            exact_usage_events += 1
        if incomplete:
            incomplete_events += 1
            incomplete_estimated_tokens += event.event_contributing_tokens
        elif countable:
            fresh_input_tokens += _quantity(event, TokenType.INPUT)
            cache_read_input_tokens += _quantity(event, TokenType.CACHED_INPUT)
            cache_creation_input_tokens += _quantity(event, TokenType.CACHE_CREATION_INPUT)
            cache_creation_5m_input_tokens += _quantity_metadata_total(
                [event],
                TokenType.CACHE_CREATION_INPUT,
                "ephemeral_5m_input_tokens",
            )
            cache_creation_1h_input_tokens += _quantity_metadata_total(
                [event],
                TokenType.CACHE_CREATION_INPUT,
                "ephemeral_1h_input_tokens",
            )
            event_cache_creation_count, event_cache_lifetime_count = _cache_creation_lifetime_detail_counts([event])
            cache_creation_quantity_count += event_cache_creation_count
            cache_creation_lifetime_detail_count += event_cache_lifetime_count
            output_tokens += _quantity(event, TokenType.OUTPUT)
            stored_contributing_tokens += event.event_contributing_tokens
            contributing_tokens += _current_rule_total(event)
        if _uses_legacy_rules(event):
            legacy_rule_events += 1

        comparison = _comparison(event)
        if comparison is None or not countable:
            continue
        estimate = comparison.get("quantity")
        if not isinstance(estimate, int) or isinstance(estimate, bool) or estimate < 0:
            continue
        prompt_total = _provider_prompt_tokens(event, comparison)
        comparison_events += 1
        estimate_tokens += estimate
        provider_prompt_tokens += prompt_total
        absolute_error += abs(prompt_total - estimate)

    weighted_absolute_percentage_error = round(absolute_error / provider_prompt_tokens * 100, 4) if provider_prompt_tokens else None
    estimate_coverage_ratio = round(estimate_tokens / provider_prompt_tokens, 6) if provider_prompt_tokens else None

    return {
        "events": event_count,
        "exact_usage_events": exact_usage_events,
        "incomplete_events": incomplete_events,
        "superseded_events": superseded_events,
        "legacy_rule_events": legacy_rule_events,
        "comparison_events": comparison_events,
        "models": dict(sorted(models.items())),
        "statuses": dict(sorted(statuses.items())),
        "proxy_sessions": len(sessions),
        "distinct_prompt_fingerprints": len(prompt_fingerprints),
        "max_prompt_cycle": max_prompt_cycle,
        "events_with_provider_request_id": provider_request_id_count,
        "events_with_provider_response_id": provider_response_id_count,
        "average_duration_ms": (round(duration_total / duration_count, 3) if duration_count else None),
        "average_time_to_first_token_ms": (round(ttft_total / ttft_count, 3) if ttft_count else None),
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "fresh_input_tokens": fresh_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_creation_5m_input_tokens": cache_creation_5m_input_tokens,
        "cache_creation_1h_input_tokens": cache_creation_1h_input_tokens,
        "cache_creation_quantity_count": cache_creation_quantity_count,
        "cache_creation_lifetime_detail_count": cache_creation_lifetime_detail_count,
        "output_tokens": output_tokens,
        "stored_contributing_tokens": stored_contributing_tokens,
        "incomplete_estimated_tokens": incomplete_estimated_tokens,
        "contributing_tokens": contributing_tokens,
        "estimated_prompt_tokens": estimate_tokens,
        "provider_prompt_tokens": provider_prompt_tokens,
        "absolute_estimation_error": absolute_error,
        "weighted_absolute_percentage_error": weighted_absolute_percentage_error,
        "estimate_coverage_ratio": estimate_coverage_ratio,
        "prompt_groups": _prompt_group_summaries_from_groups(prompt_groups),
    }


def render_summary(summary: dict) -> str:
    """Render a compact human-readable report."""
    models = ", ".join(f"{name}={count}" for name, count in summary["models"].items()) or "none"
    wape = summary["weighted_absolute_percentage_error"]
    coverage = summary["estimate_coverage_ratio"]
    cache_lifetime_detail_count = summary.get("cache_creation_lifetime_detail_count", 0)
    cache_creation_quantity_count = summary.get("cache_creation_quantity_count", 0)
    if summary["cache_creation_input_tokens"] and cache_lifetime_detail_count == 0:
        cache_lifetime = (
            "cache creation lifetime: "
            "5m=unknown 1h=unknown "
            f"(details missing for {cache_creation_quantity_count} cache-creation events)"
        )
    else:
        suffix = f" detail_events={cache_lifetime_detail_count}/{cache_creation_quantity_count}" if cache_creation_quantity_count else ""
        cache_lifetime = (
            "cache creation lifetime: "
            f"5m={summary['cache_creation_5m_input_tokens']} "
            f"1h={summary['cache_creation_1h_input_tokens']}"
            f"{suffix}"
        )
    lines = [
        "Real-call reliability report",
        f"events: {summary['events']}",
        f"exact usage events: {summary['exact_usage_events']}",
        f"incomplete events: {summary['incomplete_events']}",
        f"superseded events: {summary.get('superseded_events', 0)}",
        f"legacy-rule events: {summary['legacy_rule_events']}",
        f"models: {models}",
        ("statuses: " + (", ".join(f"{name}={count}" for name, count in summary["statuses"].items()) or "none")),
        f"proxy sessions: {summary['proxy_sessions']}",
        ("prompt attribution: " f"distinct={summary['distinct_prompt_fingerprints']} " f"max_cycle={summary['max_prompt_cycle']}"),
        (
            "provider ids: "
            f"request={summary['events_with_provider_request_id']} "
            f"response={summary['events_with_provider_response_id']}"
        ),
        (
            "latency: "
            f"average_duration_ms={summary['average_duration_ms']} "
            "average_time_to_first_token_ms="
            f"{summary['average_time_to_first_token_ms']}"
        ),
        (
            "input buckets: "
            f"fresh={summary['fresh_input_tokens']} "
            f"cache_read={summary['cache_read_input_tokens']} "
            f"cache_creation={summary['cache_creation_input_tokens']}"
        ),
        cache_lifetime,
        f"output tokens: {summary['output_tokens']}",
        (
            "contributing tokens: "
            f"current_rules={summary['contributing_tokens']} "
            f"stored_rules={summary['stored_contributing_tokens']}"
        ),
        f"incomplete estimated tokens (excluded): {summary['incomplete_estimated_tokens']}",
        (
            "prompt comparison: "
            f"estimate={summary['estimated_prompt_tokens']} "
            f"provider={summary['provider_prompt_tokens']} "
            f"absolute_error={summary['absolute_estimation_error']}"
        ),
        f"weighted absolute percentage error: {wape if wape is not None else 'n/a'}%",
        f"estimate/provider ratio: {coverage if coverage is not None else 'n/a'}",
        f"time range: {summary['first_timestamp']} -> {summary['last_timestamp']}",
    ]
    prompt_groups = summary.get("prompt_groups") or []
    if prompt_groups:
        lines.append("per-prompt:")
        for group in prompt_groups:
            group_coverage = group["estimate_coverage_ratio"]
            lines.append(
                "  "
                f"{group['sequence']}. {group['label']}: "
                f"events={group['events']} "
                f"exact={group['exact_usage_events']} "
                f"incomplete={group['incomplete_events']} "
                f"superseded={group.get('superseded_events', 0)} "
                f"tokens={group['contributing_tokens']} "
                f"prompt_provider={group['provider_prompt_tokens']} "
                f"estimate={group['estimated_prompt_tokens']} "
                f"ratio={group_coverage if group_coverage is not None else 'n/a'} "
                f"output={group['output_tokens']}"
            )
    return "\n".join(lines)


def render_json(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)


_PROMPT_GROUP_CSV_FIELDS = [
    "sequence",
    "label",
    "source",
    "fingerprint",
    "events",
    "exact_usage_events",
    "incomplete_events",
    "superseded_events",
    "statuses",
    "fresh_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "contributing_tokens",
    "comparison_events",
    "estimated_prompt_tokens",
    "provider_prompt_tokens",
    "absolute_estimation_error",
    "estimate_coverage_ratio",
]


def write_prompt_groups_csv(summary: dict, path: str) -> None:
    """Write one row per prompt-suite group for spreadsheet analysis."""
    target = Path(path)
    if target.parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_PROMPT_GROUP_CSV_FIELDS)
        writer.writeheader()
        for group in summary.get("prompt_groups", []):
            row = {field: group.get(field) for field in _PROMPT_GROUP_CSV_FIELDS}
            row["statuses"] = json.dumps(
                row.get("statuses") or {},
                ensure_ascii=False,
                sort_keys=True,
            )
            writer.writerow(row)
