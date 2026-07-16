"""Derived validation for the TokenEvent.observation contract.

``observation`` is typed but retains an open metadata mapping for provider-specific fields.
This module audits the operational subset and can still diagnose a legacy/corrupt in-memory
shape without mutating it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tracker.observability.status import STATUS_VALUES

if TYPE_CHECKING:
    from tracker.models.token_event import TokenEvent
    from tracker.models.trace import Trace

STRING_FIELDS = {
    "provider_request_id",
    "provider_response_id",
    "proxy_session_id",
    "provider_error_code",
    "service_name",
    "service",
    "application",
    "tenant",
    "tenant_id",
    "customer_id",
    "subscription_id",
    "account_id",
    "project_id",
    "cloud_provider",
    "cloud",
    "region",
    "provider_region",
    "azure_region",
    "aws_region",
    "deployment",
    "deployment_name",
    "azure_deployment",
    "aws_model_id",
    "model_id",
    "fallback_from",
    "fallback_to",
    "prompt_fingerprint",
    "suite_prompt_fingerprint",
    "suite_prompt_label",
    "suite_prompt_source",
}
NON_NEGATIVE_NUMBER_FIELDS = {
    "duration_ms",
    "time_to_first_token_ms",
    "time_to_last_token_ms",
    "provider_duration_ms",
    "total_duration_ms",
}
NON_NEGATIVE_INT_FIELDS = {
    "retry_count",
    "request_sequence",
    "prompt_sequence",
    "prompt_cycle",
    "suite_prompt_sequence",
}


@dataclass(frozen=True)
class ObservationContractIssue:
    event_id: str
    code: str
    field: str
    detail: str | None = None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_string(event: TokenEvent, field: str, value: Any) -> ObservationContractIssue | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        return ObservationContractIssue(event.event_id, "invalid_string_field", field)
    return None


def _check_non_negative_number(event: TokenEvent, field: str, value: Any) -> ObservationContractIssue | None:
    if value is None:
        return None
    if not _is_number(value) or value < 0:
        return ObservationContractIssue(event.event_id, "invalid_non_negative_number", field)
    return None


def _check_non_negative_int(event: TokenEvent, field: str, value: Any) -> ObservationContractIssue | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return ObservationContractIssue(event.event_id, "invalid_non_negative_integer", field)
    return None


def validate_event_observation(event: TokenEvent) -> list[ObservationContractIssue]:
    """Validate the operational subset of ``event.observation``."""
    issues: list[ObservationContractIssue] = []
    observation = event.observation

    status = observation.get("status")
    if status is not None:
        if not isinstance(status, str) or status not in STATUS_VALUES:
            issues.append(
                ObservationContractIssue(
                    event.event_id,
                    "invalid_status",
                    "status",
                    ",".join(sorted(STATUS_VALUES)),
                )
            )

    authoritative = observation.get("authoritative")
    if authoritative is not None and not isinstance(authoritative, bool):
        issues.append(ObservationContractIssue(event.event_id, "invalid_boolean_field", "authoritative"))

    http_status = observation.get("http_status")
    if http_status is not None:
        if isinstance(http_status, bool) or not isinstance(http_status, int) or not 100 <= http_status <= 599:
            issues.append(ObservationContractIssue(event.event_id, "invalid_http_status", "http_status"))

    for field in sorted(STRING_FIELDS):
        issue = _check_string(event, field, observation.get(field))
        if issue:
            issues.append(issue)
    for field in sorted(NON_NEGATIVE_NUMBER_FIELDS):
        issue = _check_non_negative_number(event, field, observation.get(field))
        if issue:
            issues.append(issue)
    for field in sorted(NON_NEGATIVE_INT_FIELDS):
        issue = _check_non_negative_int(event, field, observation.get(field))
        if issue:
            issues.append(issue)

    fallback_from = observation.get("fallback_from")
    fallback_to = observation.get("fallback_to")
    if (fallback_from and not fallback_to) or (fallback_to and not fallback_from):
        issues.append(
            ObservationContractIssue(
                event.event_id,
                "incomplete_fallback_pair",
                "fallback_from/fallback_to",
            )
        )
    return issues


def validate_trace_observations(trace: Trace) -> list[ObservationContractIssue]:
    return [issue for event in trace.events for issue in validate_event_observation(event)]


def build_observation_contract_summary(trace: Trace) -> dict[str, Any]:
    issues = validate_trace_observations(trace)
    fields = Counter(issue.field for issue in issues)
    codes = Counter(issue.code for issue in issues)
    return {
        "event_count": len(trace.events),
        "issue_count": len(issues),
        "events_with_issues": len({issue.event_id for issue in issues}),
        "issue_counts": dict(sorted(codes.items())),
        "field_issue_counts": dict(sorted(fields.items())),
    }


__all__ = [
    "ObservationContractIssue",
    "STATUS_VALUES",
    "build_observation_contract_summary",
    "validate_event_observation",
    "validate_trace_observations",
]
