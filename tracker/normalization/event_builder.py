"""Shared TokenEvent construction.

Full-response normalization and streaming both terminate here so identity wiring and
normalizer-owned data-quality flags cannot drift between ingestion paths.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from tracker.context.propagation import TraceContext
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.normalization.data_quality import normalizer_flags
from tracker.normalization.quality_flags import normalize_quality_flags
from tracker.observability.observation import Observation


def deduplicate_flags(flags: Iterable[str]) -> list[str]:
    """Preserve flag order while removing duplicates and empty values."""
    seen: set[str] = set()
    output: list[str] = []
    for flag in flags:
        if flag and flag not in seen:
            seen.add(flag)
            output.append(flag)
    return output


def data_quality_flags(flags: Iterable[str]) -> list[str]:
    """Normalize registered quality flags and cap unknown labels to ``custom``."""
    return normalize_quality_flags(flags)


def build_event(
    *,
    context: TraceContext,
    provider: str | None,
    api_surface: str | None,
    model: str | None,
    quantities: list[TokenQuantity],
    provider_total_tokens: int | None,
    event_id: str | None = None,
    leading_flags: Iterable[str] = (),
    trailing_flags: Iterable[str] = (),
    request_hash: str | None = None,
    response_hash: str | None = None,
    timestamp: str | None = None,
    observation: dict[str, Any] | Observation | None = None,
) -> TokenEvent:
    """Build one event and apply the common quality policy exactly once."""
    observed_at = timestamp or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    flags = deduplicate_flags(
        [
            *leading_flags,
            *normalizer_flags(quantities, provider_total_tokens),
            *trailing_flags,
        ]
    )
    return TokenEvent(
        event_id=event_id or f"evt-{uuid.uuid4().hex[:12]}",
        request_correlation_id=context.request_correlation_id,
        trace_id=context.trace_id,
        span_id=context.span_id,
        parent_span_id=context.parent_span_id,
        business_id=context.business_id,
        workflow=context.workflow,
        environment=context.environment,
        provider=provider,
        model=model,
        api_surface=api_surface,
        quantities=quantities,
        provider_total_tokens=provider_total_tokens,
        data_quality_flags=data_quality_flags(flags),
        request_hash=request_hash,
        response_hash=response_hash,
        timestamp=observed_at,
        observation=Observation() if observation is None else observation,
    )
