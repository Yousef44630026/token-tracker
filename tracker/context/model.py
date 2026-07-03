"""Immutable trace identity shared by propagation and HTTP header codecs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Immutable identity for one active trace/span/provider-call attempt."""

    trace_id: str
    span_id: str
    request_correlation_id: str
    parent_span_id: str | None = None
    business_id: str | None = None
    workflow: str | None = None
    environment: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("trace_id", "span_id", "request_correlation_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")

    def child_span(self) -> TraceContext:
        return replace(
            self,
            span_id=_new_id(),
            parent_span_id=self.span_id,
            request_correlation_id=_new_id(),
        )

    def retry(self) -> TraceContext:
        return replace(self, request_correlation_id=_new_id())


def new_trace(
    *,
    business_id: str | None = None,
    workflow: str | None = None,
    environment: str | None = None,
    trace_id: str | None = None,
) -> TraceContext:
    """Mint a fresh root context, optionally continuing a supplied trace id."""
    return TraceContext(
        trace_id=trace_id or _new_id(),
        span_id=_new_id(),
        request_correlation_id=_new_id(),
        parent_span_id=None,
        business_id=business_id,
        workflow=workflow,
        environment=environment,
    )
