"""Derived service/provider attribution metrics."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tracker.analytics._common import (
    authoritative_events,
    event_input_tokens,
    event_output_tokens,
    first_quantity_metadata,
    is_non_negative_number,
    round_metric,
)
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace

_CLOUD_BY_PROVIDER = {
    "azure_openai": "azure",
    "bedrock": "aws",
    "vertex_ai": "gcp",
    # NOT "gemini": "gcp" — direct Gemini API-key access (Google AI Studio) is a genuinely
    # different billing/auth surface from Vertex AI and is often not tied to any GCP project
    # at all (true of this project's own real Gemini captures). Merging them under "gcp" here
    # would make this attribution report unreconcilable against an actual GCP billing export,
    # which would show Vertex AI usage but never direct-Gemini usage under the same project.
    # Direct Gemini falls through to its own provider name below instead of a fabricated cloud.
}


def _first_observation(event: TokenEvent, *keys: str) -> Any:
    for key in keys:
        value = event.observation.get(key)
        if value not in (None, ""):
            return value
    return None


def _deployment(event: TokenEvent) -> str:
    value = _first_observation(
        event,
        "deployment",
        "deployment_name",
        "azure_deployment",
        "aws_model_id",
        "model_id",
    )
    if value not in (None, ""):
        return str(value)
    quantity_deployment = first_quantity_metadata(event, "azure_deployment")
    if quantity_deployment not in (None, ""):
        return str(quantity_deployment)
    return event.model or "unknown"


def _region(event: TokenEvent) -> str:
    value = _first_observation(
        event,
        "region",
        "provider_region",
        "azure_region",
        "aws_region",
        "_region",
    )
    return str(value) if value not in (None, "") else "unknown"


def _cloud(event: TokenEvent) -> str:
    value = _first_observation(event, "cloud_provider", "cloud")
    if value not in (None, ""):
        return str(value)
    return _CLOUD_BY_PROVIDER.get(event.provider or "", event.provider or "unknown")


def _service_name(event: TokenEvent) -> str:
    value = _first_observation(event, "service_name", "service", "application")
    return str(value) if value not in (None, "") else "unknown"


def _tenant(event: TokenEvent) -> str:
    value = _first_observation(
        event,
        "tenant",
        "tenant_id",
        "customer_id",
        "subscription_id",
        "account_id",
        "project_id",
    )
    return str(value) if value not in (None, "") else "unknown"


def build_service_attribution(trace: Trace) -> dict[str, Any]:
    """Group usage by service, cloud, region, provider, model, deployment, and workflow."""
    events = authoritative_events(trace)
    groups: dict[tuple[str, ...], list[TokenEvent]] = defaultdict(list)
    for event in events:
        key = (
            _service_name(event),
            _tenant(event),
            _cloud(event),
            _region(event),
            event.provider or "unknown",
            event.api_surface or "unknown",
            event.model or "unknown",
            _deployment(event),
            event.workflow or trace.workflow or "unknown",
            event.environment or trace.environment or "unknown",
        )
        groups[key].append(event)

    rows = []
    for key, group in sorted(groups.items()):
        durations = [float(value) for event in group if is_non_negative_number(value := event.observation.get("duration_ms"))]
        rows.append(
            {
                "service_name": key[0],
                "tenant": key[1],
                "cloud_provider": key[2],
                "region": key[3],
                "provider": key[4],
                "api_surface": key[5],
                "model": key[6],
                "deployment": key[7],
                "workflow": key[8],
                "environment": key[9],
                "event_count": len(group),
                "input_tokens": sum(event_input_tokens(event) for event in group),
                "output_tokens": sum(event_output_tokens(event) for event in group),
                "contributing_tokens": sum(event.event_contributing_tokens for event in group),
                "flagged_event_count": sum(1 for event in group if event.data_quality_flags),
                "provider_total_mismatch_count": sum(1 for event in group if event.event_total_mismatch not in (None, 0)),
                "average_duration_ms": round_metric(
                    sum(durations) / len(durations) if durations else None,
                    3,
                ),
            }
        )

    return {
        "group_count": len(rows),
        "rows": rows,
    }


__all__ = ["build_service_attribution"]
