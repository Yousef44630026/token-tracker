"""Trace — source-of-truth container for one logical run. (Phase 2)

A trace stores identity, its spans, and its events. It deliberately exposes NO stored
total: ``trace.observed_total_contributing_tokens`` and friends are computed in
``derive/trace_rollup`` (INV-2) from ``event.event_contributing_tokens``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tracker.models.span import Span
from tracker.models.token_event import TokenEvent


@dataclass
class Trace:
    """One root run: a bag of spans and the token events recorded under them."""

    trace_id: str
    business_id: str | None = None
    workflow: str | None = None
    environment: str | None = None
    spans: list[Span] = field(default_factory=list)
    events: list[TokenEvent] = field(default_factory=list)
    _span_ids: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
        compare=False,
    )
    _event_ids: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.trace_id, str) or not self.trace_id.strip():
            raise ValueError("trace_id must be a non-empty string")
        original_spans = list(self.spans)
        original_events = list(self.events)
        self.spans = []
        self.events = []
        self._span_ids.clear()
        self._event_ids.clear()
        for span in original_spans:
            self.add_span(span)
        for event in original_events:
            self.add_event(event)

    def add_span(self, span: Span) -> None:
        if span.trace_id != self.trace_id:
            raise ValueError("span trace_id does not match the trace")
        if span.span_id in self._span_ids:
            raise ValueError(f"duplicate span_id: {span.span_id}")
        self.spans.append(span)
        self._span_ids.add(span.span_id)

    def add_event(self, event: TokenEvent) -> None:
        if event.trace_id != self.trace_id:
            raise ValueError("event trace_id does not match the trace")
        if event.event_id in self._event_ids:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        self.events.append(event)
        self._event_ids.add(event.event_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "business_id": self.business_id,
            "workflow": self.workflow,
            "environment": self.environment,
            "spans": [span.to_dict() for span in self.spans],
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trace:
        return cls(
            trace_id=data["trace_id"],
            business_id=data.get("business_id"),
            workflow=data.get("workflow"),
            environment=data.get("environment"),
            spans=[Span.from_dict(span) for span in data.get("spans", [])],
            events=[TokenEvent.from_dict(event) for event in data.get("events", [])],
        )
