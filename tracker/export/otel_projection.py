"""Dependency-free OpenTelemetry GenAI token metric projection.

The tracker remains the source-of-truth ledger. This module exposes the standard
``gen_ai.client.token.usage`` measurements without importing an OpenTelemetry SDK; callers
can pass an SDK Histogram to :func:`record_token_usage` when one is installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tracker.models.enums import PrecisionLevel, TokenType
from tracker.models.token_event import TokenEvent

TOKEN_USAGE_METRIC_NAME = "gen_ai.client.token.usage"
TOKEN_USAGE_UNIT = "{token}"
TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES = (
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
)

_PROVIDER_NAMES = {
    "azure_openai": "azure.ai.openai",
    "azure-openai": "azure.ai.openai",
    "azureopenai": "azure.ai.openai",
    "bedrock": "aws.bedrock",
    "gemini": "gcp.gemini",
    "vertex_ai": "gcp.vertex_ai",
    "vertex-ai": "gcp.vertex_ai",
    "vertexai": "gcp.vertex_ai",
    "mistral": "mistral_ai",
}

_OPERATION_NAMES = {
    "chat": "chat",
    "chat_completions": "chat",
    "messages": "chat",
    "responses": "chat",
    "converse": "chat",
    "invoke_model": "chat",
    "generate_content": "generate_content",
    "embeddings": "embeddings",
    "rerank": "rerank",
}

_INPUT_TYPES = frozenset(
    {
        TokenType.INPUT,
        TokenType.CACHED_INPUT,
        TokenType.CACHE_CREATION_INPUT,
        TokenType.EMBEDDING,
        TokenType.RERANK_INPUT,
        TokenType.AUDIO_INPUT,
        TokenType.IMAGE_INPUT,
        TokenType.VIDEO_INPUT,
    }
)
_OUTPUT_TYPES = frozenset(
    {
        TokenType.OUTPUT,
        TokenType.REASONING,
        TokenType.THINKING,
        TokenType.RERANK_OUTPUT,
        TokenType.AUDIO_OUTPUT,
    }
)


@dataclass(frozen=True)
class TokenUsageMeasurement:
    """One input/output observation for the standard GenAI token histogram."""

    value: int
    attributes: dict[str, str]


def _token_direction(token_type: TokenType) -> str | None:
    if token_type in _INPUT_TYPES:
        return "input"
    if token_type in _OUTPUT_TYPES:
        return "output"
    return None


def _base_attributes(event: TokenEvent) -> dict[str, str]:
    provider = event.provider or "unknown"
    surface = event.api_surface or "unknown"
    attributes = {
        "gen_ai.operation.name": _OPERATION_NAMES.get(surface, surface),
        "gen_ai.provider.name": _PROVIDER_NAMES.get(provider, provider),
        "token_tracker.api_surface": surface,
    }
    if event.model:
        attributes["gen_ai.response.model"] = event.model
    if event.workflow:
        attributes["gen_ai.workflow.name"] = event.workflow
    return attributes


def token_usage_measurements(event: TokenEvent, *, include_estimates: bool = False) -> tuple[TokenUsageMeasurement, ...]:
    """Project an event into at most two standard input/output token measurements.

    Only authoritative, effective quantities are considered. Subtotals and unverified
    quantities are excluded by ``included_in_total``. Estimates are omitted by default so
    the standard metric never presents a working estimate as provider-observed usage.
    """
    if event.superseded or not event.is_authoritative:
        return ()

    totals = {"input": 0, "output": 0}
    present: set[str] = set()
    for quantity in event.quantities:
        direction = _token_direction(quantity.token_type)
        if direction is None or not quantity.included_in_total:
            continue
        if quantity.precision_level == PrecisionLevel.ESTIMATE and not include_estimates:
            continue
        totals[direction] += quantity.quantity_in_total
        present.add(direction)

    base = _base_attributes(event)
    measurements = []
    for direction in ("input", "output"):
        if direction not in present:
            continue
        attributes = {**base, "gen_ai.token.type": direction}
        measurements.append(TokenUsageMeasurement(value=totals[direction], attributes=attributes))
    return tuple(measurements)


def record_token_usage(event: TokenEvent, histogram: Any, *, include_estimates: bool = False) -> int:
    """Record projected measurements into an OpenTelemetry-compatible Histogram.

    ``histogram`` is duck-typed and must provide ``record(value, attributes=...)``. The
    return value is the number of observations recorded.
    """
    measurements = token_usage_measurements(event, include_estimates=include_estimates)
    for measurement in measurements:
        histogram.record(measurement.value, attributes=measurement.attributes)
    return len(measurements)


__all__ = [
    "TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES",
    "TOKEN_USAGE_METRIC_NAME",
    "TOKEN_USAGE_UNIT",
    "TokenUsageMeasurement",
    "record_token_usage",
    "token_usage_measurements",
]
