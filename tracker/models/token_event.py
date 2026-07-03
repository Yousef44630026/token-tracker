"""TokenEvent — source of truth (INV-1) + derived view (INV-2). (Phase 2)

STORED (serialized): event_id, request_correlation_id, identity/context fields
(trace_id, span_id, parent_span_id, business_id, workflow, environment), provider fields
(provider, model, api_surface), quantities[], provider_total_tokens, superseded,
superseded_by, data_quality_flags, hashes, timestamp, observation.

DERIVED (@property, never stored): event_contributing_tokens, event_total_mismatch
(see INV-2 / INV-5). A superseded event contributes 0 everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tracker.models.enums import Additivity
from tracker.models.token_quantity import TokenQuantity


@dataclass
class TokenEvent:
    """One provider call's worth of observed token usage, attached to a span."""

    # --- identity ---
    event_id: str
    request_correlation_id: str

    # --- context (from the propagation layer) ---
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    business_id: str | None = None
    workflow: str | None = None
    environment: str | None = None

    # --- provider ---
    provider: str | None = None
    model: str | None = None
    api_surface: str | None = None

    # --- observed usage ---
    quantities: list[TokenQuantity] = field(default_factory=list)
    provider_total_tokens: int | None = None  # raw provider data; NEVER summed across events

    # --- supersession (set by reconciler / stream tracker, never an adapter) ---
    superseded: bool = False
    superseded_by: str | None = None

    # --- quality + provenance ---
    data_quality_flags: list[str] = field(default_factory=list)
    request_hash: str | None = None
    response_hash: str | None = None
    timestamp: str | None = None
    observation: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("event_id", "request_correlation_id", "trace_id", "span_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.provider_total_tokens is not None:
            if isinstance(self.provider_total_tokens, bool) or not isinstance(self.provider_total_tokens, int):
                raise TypeError("provider_total_tokens must be an integer or None")
            if self.provider_total_tokens < 0:
                raise ValueError("provider_total_tokens cannot be negative")
        if any(not isinstance(q, TokenQuantity) for q in self.quantities):
            raise TypeError("quantities must contain TokenQuantity objects")
        # Referential integrity for subtotals (INV-4): a SUBTOTAL_OF quantity breaks down a
        # parent quantity within THIS event, so the named parent token_type must actually be
        # present among the siblings. A dangling subtotal claims to break down something that
        # isn't here — a structural contradiction, rejected like an empty subtotal_of already
        # is. (Totals are unaffected — subtotals contribute 0 — but the breakdown must not lie.)
        for q in self.quantities:
            if q.additivity == Additivity.SUBTOTAL_OF:
                if not any(other is not q and other.token_type.value == q.subtotal_of for other in self.quantities):
                    raise ValueError(
                        f"subtotal_of={q.subtotal_of!r} references a parent token_type not present "
                        f"in this event (dangling subtotal)"
                    )
        if self.superseded and not self.superseded_by:
            raise ValueError("a superseded event must identify superseded_by")
        if not self.superseded and self.superseded_by is not None:
            raise ValueError("superseded_by requires superseded=True")
        if not isinstance(self.observation, dict):
            raise TypeError("observation must be a dictionary")

    @property
    def is_authoritative(self) -> bool:
        """Whether this event is allowed into authoritative totals."""
        return self.observation.get("authoritative") is not False

    # --- derived: computed only (INV-2), never stored/serialized ---
    @property
    def _sum_quantity_in_total(self) -> int:
        return sum(q.quantity_in_total for q in self.quantities)

    @property
    def event_contributing_tokens(self) -> int:
        """0 if superseded/non-authoritative, else the sum of quantity_in_total."""
        return 0 if self.superseded or not self.is_authoritative else self._sum_quantity_in_total

    @property
    def event_total_mismatch(self) -> int | None:
        """provider_total_tokens - sum(quantity_in_total), or None if no provider total."""
        if self.provider_total_tokens is None:
            return None
        return self.provider_total_tokens - self._sum_quantity_in_total

    # --- serialization: STORED fields only ---
    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "request_correlation_id": self.request_correlation_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "business_id": self.business_id,
            "workflow": self.workflow,
            "environment": self.environment,
            "provider": self.provider,
            "model": self.model,
            "api_surface": self.api_surface,
            "quantities": [q.to_dict() for q in self.quantities],
            "provider_total_tokens": self.provider_total_tokens,
            "superseded": self.superseded,
            "superseded_by": self.superseded_by,
            "data_quality_flags": list(self.data_quality_flags),
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "timestamp": self.timestamp,
            "observation": dict(self.observation),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenEvent:
        return cls(
            event_id=d["event_id"],
            request_correlation_id=d["request_correlation_id"],
            trace_id=d["trace_id"],
            span_id=d["span_id"],
            parent_span_id=d.get("parent_span_id"),
            business_id=d.get("business_id"),
            workflow=d.get("workflow"),
            environment=d.get("environment"),
            provider=d.get("provider"),
            model=d.get("model"),
            api_surface=d.get("api_surface"),
            quantities=[TokenQuantity.from_dict(q) for q in d.get("quantities", [])],
            provider_total_tokens=d.get("provider_total_tokens"),
            superseded=d.get("superseded", False),
            superseded_by=d.get("superseded_by"),
            data_quality_flags=list(d.get("data_quality_flags", [])),
            request_hash=d.get("request_hash"),
            response_hash=d.get("response_hash"),
            timestamp=d.get("timestamp"),
            observation=dict(d.get("observation", {})),
        )
