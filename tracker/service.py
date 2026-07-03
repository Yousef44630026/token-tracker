"""Small public façade for the common response-tracking workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tracker.context.propagation import TraceContext
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace
from tracker.normalization.normalizer import normalize
from tracker.streaming.stream_tracker import StreamTracker

if TYPE_CHECKING:
    from tracker.adapters.base import BaseAPISurfaceAdapter
    from tracker.collector.client import CollectorClient
    from tracker.storage.file_repository import FileRepository


@dataclass(frozen=True)
class TrackingResult:
    """Event plus optional sink outcomes; normalization itself remains non-throwing."""

    event: TokenEvent
    persisted: bool | None = None
    collected: bool | None = None
    sink_errors: tuple[str, ...] = field(default_factory=tuple)


def track_response(
    response: Any,
    adapter: BaseAPISurfaceAdapter,
    *,
    context: TraceContext | None = None,
    trace: Trace | None = None,
    repository: FileRepository | None = None,
    collector: CollectorClient | None = None,
    **normalize_options: Any,
) -> TrackingResult:
    """Normalize a response, attach it to a trace, and best-effort fan out to sinks."""
    event = normalize(response, adapter, context=context, **normalize_options)
    if trace is not None:
        trace.add_event(event)

    errors: list[str] = []
    persisted: bool | None = None
    if repository is not None:
        try:
            repository.append(event)
            persisted = True
        except Exception as exc:  # noqa: BLE001 - tracking must not break the caller
            persisted = False
            errors.append(f"repository:{type(exc).__name__}")

    collected: bool | None = None
    if collector is not None:
        collected = collector.record(event)
        if not collected:
            errors.append("collector:rejected")

    return TrackingResult(
        event=event,
        persisted=persisted,
        collected=collected,
        sink_errors=tuple(errors),
    )


def track_stream(
    *,
    context: TraceContext,
    provider: str | None = None,
    api_surface: str | None = None,
    model: str | None = None,
) -> StreamTracker:
    """Create a StreamTracker from propagated context."""
    return StreamTracker.from_context(
        context,
        provider=provider,
        api_surface=api_surface,
        model=model,
    )
