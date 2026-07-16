"""Derived anomaly signals for operational inspection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tracker.derive.effective_events import iter_effective_events
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace


@dataclass(frozen=True)
class AnomalySignal:
    code: str
    event_id: str
    detail: str | None = None
    severity: str = "medium"
    magnitude: int | None = None


def event_anomalies(event: TokenEvent) -> list[AnomalySignal]:
    """Return anomaly signals for one event."""
    signals: list[AnomalySignal] = []
    if event.event_total_mismatch not in (None, 0):
        severity = "high" if event.over_attributed_tokens else "medium"
        signals.append(
            AnomalySignal(
                code="provider_total_mismatch",
                event_id=event.event_id,
                detail=str(event.event_total_mismatch),
                severity=severity,
                magnitude=abs(event.event_total_mismatch or 0),
            )
        )
    for flag in event.data_quality_flags:
        if flag == "provider_total_mismatch":
            continue
        signals.append(AnomalySignal(code=flag, event_id=event.event_id))
    return signals


def detect_anomalies_from_events(events: Iterable[TokenEvent]) -> list[AnomalySignal]:
    """Materialize event-level quality and mismatch signals without storing them."""
    signals: list[AnomalySignal] = []
    for event in iter_effective_events(events):
        signals.extend(event_anomalies(event))
    return signals


def detect_anomalies(trace: Trace) -> list[AnomalySignal]:
    """Materialize event-level quality and mismatch signals without storing them."""
    return detect_anomalies_from_events(trace.events)


__all__ = ["AnomalySignal", "detect_anomalies", "detect_anomalies_from_events", "event_anomalies"]
