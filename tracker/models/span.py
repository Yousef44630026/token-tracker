"""Span — source-of-truth identity for one unit of work. (Phase 2)

A span stores identity and provenance only. It exposes NO total: span/trace rollups are
derived in ``derive/`` so a stored number can never disagree with the rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    """One unit of work (LLM call, tool call, stream, sub-agent) within a trace."""

    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    span_type: str | None = None  # e.g. "llm", "tool", "sub_agent", "stream"
    name: str | None = None
    start_ts: str | None = None
    end_ts: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.span_id, str) or not self.span_id.strip():
            raise ValueError("span_id must be a non-empty string")
        if not isinstance(self.trace_id, str) or not self.trace_id.strip():
            raise ValueError("trace_id must be a non-empty string")
        if self.parent_span_id == self.span_id:
            raise ValueError("a span cannot be its own parent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "span_type": self.span_type,
            "name": self.name,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Span:
        return cls(
            span_id=data["span_id"],
            trace_id=data["trace_id"],
            parent_span_id=data.get("parent_span_id"),
            span_type=data.get("span_type"),
            name=data.get("name"),
            start_ts=data.get("start_ts"),
            end_ts=data.get("end_ts"),
            metadata=dict(data.get("metadata", {})),
        )
