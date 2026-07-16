"""Single audit-oriented trust report for one trace."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from tracker.analytics.anomaly_signals import AnomalySignal, detect_anomalies, event_anomalies
from tracker.analytics.coverage import CoverageExactnessAccumulator, build_coverage_exactness
from tracker.derive.effective_events import iter_effective_events
from tracker.derive.trace_rollup import roll_up
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace


@dataclass(frozen=True)
class TrustReport:
    """One object answering: what is the number, and why might it be wrong?"""

    trace_id: str
    observed_total_contributing_tokens: int
    headline_floor_tokens: int
    headline_estimate_tokens: int
    headline_ceiling_tokens: int | None
    headline_upper_bound_status: str
    headline_status: str
    attribution_status: str
    capture_completeness_ratio: float | None
    total_is_lower_bound: bool
    total_is_upper_bound: bool
    unattributed_tokens: int
    over_attributed_tokens: int
    event_count: int
    excluded_event_count: int
    superseded_event_count: int
    flagged_event_count: int
    open_upper_bound_event_count: int
    provider_reconciled_event_count: int
    anomaly_count: int
    anomalies: list[AnomalySignal]
    coverage: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["anomalies"] = [asdict(signal) for signal in self.anomalies]
        return data


def build_trust_report(trace: Trace) -> TrustReport:
    """Build the single audit report for a trace."""
    rollup = roll_up(trace)
    coverage = build_coverage_exactness(trace)
    anomalies = detect_anomalies(trace)
    return TrustReport(
        trace_id=trace.trace_id,
        observed_total_contributing_tokens=rollup.observed_total_contributing_tokens,
        headline_floor_tokens=rollup.headline_floor_tokens,
        headline_estimate_tokens=rollup.headline_estimate_tokens,
        headline_ceiling_tokens=rollup.headline_ceiling_tokens,
        headline_upper_bound_status=rollup.headline_upper_bound_status,
        headline_status=rollup.headline_status,
        attribution_status=rollup.attribution_status,
        capture_completeness_ratio=rollup.capture_completeness_ratio,
        total_is_lower_bound=rollup.total_is_lower_bound,
        total_is_upper_bound=rollup.total_is_upper_bound,
        unattributed_tokens=rollup.unattributed_tokens,
        over_attributed_tokens=rollup.over_attributed_tokens,
        event_count=rollup.event_count,
        excluded_event_count=coverage["excluded_event_count"],
        superseded_event_count=rollup.superseded_event_count,
        flagged_event_count=rollup.flagged_event_count,
        open_upper_bound_event_count=rollup.open_upper_bound_event_count,
        provider_reconciled_event_count=rollup.provider_reconciled_event_count,
        anomaly_count=len(anomalies),
        anomalies=anomalies,
        coverage=coverage,
    )


def build_trust_report_from_events(
    events: Iterable[TokenEvent],
    *,
    trace_id: str | None = None,
    collect_anomalies: bool = True,
) -> TrustReport:
    """Build an audit report from a streaming event source.

    Set ``collect_anomalies=False`` for aggregate-only high-volume exports. The report keeps
    the exact anomaly_count while leaving the detailed anomaly list empty, avoiding memory
    growth proportional to the number of findings.
    """
    coverage_accumulator = CoverageExactnessAccumulator()
    anomalies: list[AnomalySignal] = []
    anomaly_count = 0
    flagged_event_count = 0
    first_trace_id: str | None = None
    mixed_trace_ids = False

    for event in iter_effective_events(events):
        coverage_accumulator.add(event)
        signals = event_anomalies(event)
        anomaly_count += len(signals)
        if collect_anomalies:
            anomalies.extend(signals)
        if event.data_quality_flags:
            flagged_event_count += 1
        if first_trace_id is None:
            first_trace_id = event.trace_id
        elif event.trace_id != first_trace_id:
            mixed_trace_ids = True

    coverage = coverage_accumulator.to_dict()
    report_trace_id = trace_id or ("multiple" if mixed_trace_ids else first_trace_id) or "unknown"
    return TrustReport(
        trace_id=report_trace_id,
        observed_total_contributing_tokens=coverage["observed_total_contributing_tokens"],
        headline_floor_tokens=coverage["headline_floor_tokens"],
        headline_estimate_tokens=coverage["headline_estimate_tokens"],
        headline_ceiling_tokens=coverage["headline_ceiling_tokens"],
        headline_upper_bound_status=coverage["headline_upper_bound_status"],
        headline_status=coverage["headline_status"],
        attribution_status=coverage["attribution_status"],
        capture_completeness_ratio=coverage["capture_completeness_ratio"],
        total_is_lower_bound=coverage["total_is_lower_bound"],
        total_is_upper_bound=coverage["total_is_upper_bound"],
        unattributed_tokens=coverage["unattributed_tokens"],
        over_attributed_tokens=coverage["over_attributed_tokens"],
        event_count=coverage_accumulator.event_count,
        excluded_event_count=coverage["excluded_event_count"],
        superseded_event_count=coverage["superseded_event_count"],
        flagged_event_count=flagged_event_count,
        open_upper_bound_event_count=coverage["open_upper_bound_event_count"],
        provider_reconciled_event_count=coverage["provider_reconciled_event_count"],
        anomaly_count=anomaly_count,
        anomalies=anomalies,
        coverage=coverage,
    )


__all__ = ["TrustReport", "build_trust_report", "build_trust_report_from_events"]
