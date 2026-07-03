"""Derived reliability metrics for provider calls."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from tracker.analytics._common import ratio
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace

_INCOMPLETE_FLAGS = {
    "input_estimate_only",
    "provider_usage_missing",
    "provider_stream_usage_missing",
    "proxy_upstream_error",
    "raw_usage_missing",
}
_TIMEOUT_STATUSES = {"timeout", "timed_out"}
_RATE_LIMIT_STATUSES = {"rate_limited", "throttled"}
_ERROR_STATUSES = {"error", "failed", "timeout", "timed_out", "rate_limited", "throttled"}


def _status(event: TokenEvent) -> str:
    value = event.observation.get("status")
    return value if isinstance(value, str) and value else "unknown"


def _is_incomplete(event: TokenEvent) -> bool:
    if event.observation.get("authoritative") is False:
        return True
    return bool(_INCOMPLETE_FLAGS & set(event.data_quality_flags))


def _is_error(event: TokenEvent) -> bool:
    status = _status(event)
    http_status = event.observation.get("http_status")
    return (
        status in _ERROR_STATUSES
        or (isinstance(http_status, int) and not isinstance(http_status, bool) and http_status >= 400)
        or event.observation.get("provider_error_code") is not None
    )


def _is_judged(event: TokenEvent) -> bool:
    """Whether there is ANY operational signal to judge success/failure from.

    ``observation`` is optional and defaults to ``{}`` — nothing in this project's own
    workflow helpers (agent_tracker.py / rag_tracker.py) ever populates status/http_status/
    error fields, and neither do most of this project's own tests. Without this check,
    success_rate silently read 100% for every such event (no signal treated as "no error"),
    which is the same confident-zero INV-6 forbids at the token layer, unaddressed here.
    """
    status = event.observation.get("status")
    http_status = event.observation.get("http_status")
    return (
        (isinstance(status, str) and status not in ("", "unknown"))
        or (isinstance(http_status, int) and not isinstance(http_status, bool))
        or event.observation.get("provider_error_code") is not None
    )


def _block(events: list[TokenEvent]) -> dict[str, Any]:
    statuses = Counter(_status(event) for event in events)
    flags = Counter(flag for event in events for flag in event.data_quality_flags)
    retries = sum(
        value
        for event in events
        if isinstance((value := event.observation.get("retry_count")), int) and not isinstance(value, bool) and value > 0
    )
    fallbacks = sum(1 for event in events if event.observation.get("fallback_from") or event.observation.get("fallback_to"))
    rate_limited = sum(
        1
        for event in events
        if _status(event) in _RATE_LIMIT_STATUSES
        or event.observation.get("provider_error_code") in {"rate_limit", "throttling", "throttled"}
    )
    timeouts = sum(1 for event in events if _status(event) in _TIMEOUT_STATUSES)
    errors = sum(1 for event in events if _is_error(event))
    incomplete = sum(1 for event in events if _is_incomplete(event))
    missing_usage = sum(
        1
        for event in events
        if {"raw_usage_missing", "provider_usage_missing", "provider_stream_usage_missing"} & set(event.data_quality_flags)
    )
    mismatches = sum(1 for event in events if event.event_total_mismatch not in (None, 0))
    request_ids = sum(1 for event in events if event.observation.get("provider_request_id"))
    response_ids = sum(1 for event in events if event.observation.get("provider_response_id"))

    judged_count = sum(1 for event in events if _is_judged(event))
    unmeasured_count = len(events) - judged_count
    # success_count is judged-and-not-error, NOT len(events)-errors: an event with no
    # operational signal at all must not be silently counted as a success.
    success_count = judged_count - errors

    return {
        "event_count": len(events),
        "status_counts": dict(sorted(statuses.items())),
        "data_quality_flag_counts": dict(sorted(flags.items())),
        "judged_event_count": judged_count,
        "unmeasured_event_count": unmeasured_count,
        "success_count": success_count,
        "error_count": errors,
        "timeout_count": timeouts,
        "rate_limit_count": rate_limited,
        "retry_count": retries,
        "fallback_count": fallbacks,
        "incomplete_event_count": incomplete,
        "missing_usage_count": missing_usage,
        "provider_total_mismatch_count": mismatches,
        "events_with_provider_request_id": request_ids,
        "events_with_provider_response_id": response_ids,
        # denominator is JUDGED events only — None (not a false 100%/0%) when nothing could
        # be judged at all, e.g. no proxy/collector layer ever populated `observation`.
        "success_rate": ratio(success_count, judged_count),
        "error_rate": ratio(errors, judged_count),
        "unmeasured_rate": ratio(unmeasured_count, len(events)),
        "incomplete_rate": ratio(incomplete, len(events)),
        "missing_usage_rate": ratio(missing_usage, len(events)),
        "provider_request_id_coverage": ratio(request_ids, len(events)),
        "provider_response_id_coverage": ratio(response_ids, len(events)),
    }


def build_reliability_summary(trace: Trace) -> dict[str, Any]:
    """Return reliability metrics for all events and per provider."""
    events = list(trace.events)
    by_provider: dict[str, list[TokenEvent]] = defaultdict(list)
    for event in events:
        by_provider[event.provider or "unknown"].append(event)
    return {
        **_block(events),
        "by_provider": {provider: _block(group) for provider, group in sorted(by_provider.items())},
    }


__all__ = ["build_reliability_summary"]
