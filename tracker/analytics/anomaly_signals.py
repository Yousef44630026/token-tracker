"""Derived anomaly signals for operational inspection."""

from __future__ import annotations

from dataclasses import dataclass

from tracker.models.trace import Trace


@dataclass(frozen=True)
class AnomalySignal:
    code: str
    event_id: str
    detail: str | None = None


def detect_anomalies(trace: Trace) -> list[AnomalySignal]:
    """Materialize event-level quality and mismatch signals without storing them."""
    signals: list[AnomalySignal] = []
    for event in trace.events:
        if event.event_total_mismatch not in (None, 0):
            signals.append(
                AnomalySignal(
                    code="provider_total_mismatch",
                    event_id=event.event_id,
                    detail=str(event.event_total_mismatch),
                )
            )
        for flag in event.data_quality_flags:
            if flag == "provider_total_mismatch":
                continue
            signals.append(AnomalySignal(code=flag, event_id=event.event_id))
    return signals


__all__ = ["AnomalySignal", "detect_anomalies"]
