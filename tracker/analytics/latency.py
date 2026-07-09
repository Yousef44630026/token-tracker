"""Derived latency and throughput analytics."""

from __future__ import annotations

from collections import defaultdict
from math import ceil
from typing import Any

from tracker.analytics._common import (
    authoritative_events,
    event_duration_ms,
    event_output_tokens,
    is_non_negative_number,
    ratio,
    round_metric,
)
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _observation_number(event: TokenEvent, key: str) -> float | None:
    value = event.observation.get(key)
    if is_non_negative_number(value):
        return float(value)
    return None


def _duration_ms(event: TokenEvent) -> float | None:
    # Shared with service attribution (and any future view) via _common so every view reads the
    # same duration keys in the same order — see tracker/analytics/_common.event_duration_ms.
    return event_duration_ms(event)


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _latency_block(events: list[TokenEvent]) -> dict[str, Any]:
    durations = [value for event in events if (value := _duration_ms(event)) is not None]
    ttfts = [value for event in events if (value := _observation_number(event, "time_to_first_token_ms")) is not None]
    time_to_last = [value for event in events if (value := _observation_number(event, "time_to_last_token_ms")) is not None]
    output_rates = []
    for event in events:
        duration = _duration_ms(event)
        output_tokens = event_output_tokens(event)
        if duration and duration > 0 and output_tokens > 0:
            output_rates.append(output_tokens / (duration / 1000))

    slowest = max(
        ((event, duration) for event in events if (duration := _duration_ms(event)) is not None),
        key=lambda item: item[1],
        default=None,
    )
    return {
        "event_count": len(events),
        "events_with_duration": len(durations),
        "events_with_time_to_first_token": len(ttfts),
        "duration_coverage_ratio": ratio(len(durations), len(events)),
        "average_duration_ms": round_metric(_average(durations), 3),
        "p50_duration_ms": round_metric(_percentile(durations, 0.50), 3),
        "p95_duration_ms": round_metric(_percentile(durations, 0.95), 3),
        "p99_duration_ms": round_metric(_percentile(durations, 0.99), 3),
        "average_time_to_first_token_ms": round_metric(_average(ttfts), 3),
        "p95_time_to_first_token_ms": round_metric(_percentile(ttfts, 0.95), 3),
        "average_time_to_last_token_ms": round_metric(_average(time_to_last), 3),
        "average_output_tokens_per_second": round_metric(_average(output_rates), 3),
        "slowest_event_id": slowest[0].event_id if slowest else None,
        "slowest_duration_ms": round_metric(slowest[1], 3) if slowest else None,
    }


def build_latency_summary(trace: Trace) -> dict[str, Any]:
    """Return trace-level latency metrics plus provider/model breakdowns."""
    events = authoritative_events(trace)
    by_provider: dict[str, list[TokenEvent]] = defaultdict(list)
    by_model: dict[str, list[TokenEvent]] = defaultdict(list)
    for event in events:
        by_provider[event.provider or "unknown"].append(event)
        by_model[event.model or "unknown"].append(event)

    return {
        **_latency_block(events),
        "by_provider": {provider: _latency_block(group) for provider, group in sorted(by_provider.items())},
        "by_model": {model: _latency_block(group) for model, group in sorted(by_model.items())},
    }


__all__ = ["build_latency_summary"]
