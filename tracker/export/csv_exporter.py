"""CSV export — materializes the derived columns. (Phase 9)

Writes three row sets from a Trace, materializing derived values where appropriate:
be summed correctly WITHOUT re-deriving anything:

  - token_quantities: one row per quantity, with ``quantity_in_total`` (the ONLY summable
    column) and ``export_warning``. Superseded-event quantities are materialized as 0, so
    the column is safe to sum directly.
  - token_events: one row per event, with ``event_contributing_tokens`` (0 if superseded).
  - token_spans: one row per span, including RAG/agent/tool metadata as JSON.

The raw ``quantity`` and ``provider_total_tokens`` are written for reference but must NEVER
be summed across rows; sum ``quantity_in_total`` (quantity grain) or
``event_contributing_tokens`` (event grain) — never mix the two in one sum.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any

from tracker.analytics.agent import build_agent_summary
from tracker.analytics.cache import build_cache_summary
from tracker.analytics.latency import build_latency_summary
from tracker.analytics.observation_contract import build_observation_contract_summary
from tracker.analytics.rag import build_rag_summary
from tracker.analytics.reliability import build_reliability_summary
from tracker.analytics.service_attribution import build_service_attribution
from tracker.models.trace import Trace

QUANTITY_HEADERS = [
    "event_id",
    "event_superseded",
    "token_type",
    "token_role",
    "quantity",
    "precision_level",
    "usage_source",
    "additivity",
    "subtotal_of",
    "quantity_in_total",
    "export_warning",
]
EVENT_HEADERS = [
    "event_id",
    "request_correlation_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "provider",
    "model",
    "api_surface",
    "provider_total_tokens",
    "superseded",
    "superseded_by",
    "event_contributing_tokens",
    "event_total_mismatch",
    "data_quality_flags",
    "authoritative",
    "observation_status",
    "http_status",
    "provider_request_id",
    "provider_response_id",
    "proxy_session_id",
    "request_sequence",
    "duration_ms",
    "prompt_fingerprint",
    "prompt_sequence",
    "prompt_cycle",
    "time_to_first_token_ms",
    "timestamp",
    "observation",
]
SPAN_HEADERS = [
    "span_id",
    "trace_id",
    "parent_span_id",
    "span_type",
    "name",
    "start_ts",
    "end_ts",
    "metadata",
]
METRIC_HEADERS = ["metric", "value"]
SERVICE_ATTRIBUTION_HEADERS = [
    "service_name",
    "tenant",
    "cloud_provider",
    "region",
    "provider",
    "api_surface",
    "model",
    "deployment",
    "workflow",
    "environment",
    "event_count",
    "input_tokens",
    "output_tokens",
    "contributing_tokens",
    "flagged_event_count",
    "provider_total_mismatch_count",
    "average_duration_ms",
]


def quantity_rows(trace: Trace) -> list[dict[str, Any]]:
    """One materialized row per quantity (derived columns included)."""
    rows: list[dict[str, Any]] = []
    for e in trace.events:
        for q in e.quantities:
            rows.append(
                {
                    "event_id": e.event_id,
                    "event_superseded": e.superseded,
                    "token_type": q.token_type.value,
                    "token_role": q.token_role,
                    "quantity": q.quantity,
                    "precision_level": q.precision_level.value,
                    "usage_source": q.usage_source.value,
                    "additivity": q.additivity.value,
                    "subtotal_of": q.subtotal_of,
                    "quantity_in_total": (0 if e.superseded or not e.is_authoritative else q.quantity_in_total),
                    "export_warning": q.export_warning,
                }
            )
    return rows


def event_rows(trace: Trace) -> list[dict[str, Any]]:
    """One materialized row per event (supersession-aware contributing total)."""
    return [
        {
            "event_id": e.event_id,
            "request_correlation_id": e.request_correlation_id,
            "trace_id": e.trace_id,
            "span_id": e.span_id,
            "parent_span_id": e.parent_span_id,
            "provider": e.provider,
            "model": e.model,
            "api_surface": e.api_surface,
            "provider_total_tokens": e.provider_total_tokens,
            "superseded": e.superseded,
            "superseded_by": e.superseded_by,
            "event_contributing_tokens": e.event_contributing_tokens,
            "event_total_mismatch": e.event_total_mismatch,
            "data_quality_flags": ";".join(e.data_quality_flags),
            "authoritative": e.is_authoritative,
            "observation_status": e.observation.get("status"),
            "http_status": e.observation.get("http_status"),
            "provider_request_id": e.observation.get("provider_request_id"),
            "provider_response_id": e.observation.get("provider_response_id"),
            "proxy_session_id": e.observation.get("proxy_session_id"),
            "request_sequence": e.observation.get("request_sequence"),
            "prompt_fingerprint": e.observation.get("prompt_fingerprint"),
            "prompt_sequence": e.observation.get("prompt_sequence"),
            "prompt_cycle": e.observation.get("prompt_cycle"),
            "duration_ms": e.observation.get("duration_ms"),
            "time_to_first_token_ms": e.observation.get("time_to_first_token_ms"),
            "timestamp": e.timestamp,
            "observation": json.dumps(
                e.observation,
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        for e in trace.events
    ]


def span_rows(trace: Trace) -> list[dict[str, Any]]:
    """One source-of-truth row per span, including RAG/agent/tool metadata."""
    return [
        {
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "parent_span_id": span.parent_span_id,
            "span_type": span.span_type,
            "name": span.name,
            "start_ts": span.start_ts,
            "end_ts": span.end_ts,
            "metadata": json.dumps(span.metadata, ensure_ascii=False, sort_keys=True),
        }
        for span in trace.spans
    ]


def metric_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize summary metrics as simple metric/value rows."""
    rows = []
    for key, value in summary.items():
        if key == "rows":
            continue
        rows.append(
            {
                "metric": key,
                "value": (json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value),
            }
        )
    return rows


def build_metric_exports(trace: Trace) -> dict[str, list[dict[str, Any]]]:
    """Build all derived metric row sets for CSV/Excel export."""
    return {
        "LatencySummary": metric_rows(build_latency_summary(trace)),
        "ReliabilitySummary": metric_rows(build_reliability_summary(trace)),
        "ObservationContract": metric_rows(build_observation_contract_summary(trace)),
        "CacheEfficiency": metric_rows(build_cache_summary(trace)),
        "RagEfficiency": metric_rows(build_rag_summary(trace)),
        "AgentEfficiency": metric_rows(build_agent_summary(trace)),
        "ServiceAttribution": build_service_attribution(trace)["rows"],
    }


def _write(path: str, headers: list[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row[k] is None else row[k]) for k in headers})


def export_csv(
    trace: Trace,
    out_dir: str,
) -> dict[str, str]:
    """Write quantity, event, and span CSV files into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    q_path = os.path.join(out_dir, "token_quantities.csv")
    e_path = os.path.join(out_dir, "token_events.csv")
    s_path = os.path.join(out_dir, "token_spans.csv")
    _write(q_path, QUANTITY_HEADERS, quantity_rows(trace))
    _write(e_path, EVENT_HEADERS, event_rows(trace))
    _write(s_path, SPAN_HEADERS, span_rows(trace))
    paths = {"token_quantities": q_path, "token_events": e_path, "token_spans": s_path}
    metric_exports = build_metric_exports(trace)
    for sheet_name, rows in metric_exports.items():
        filename = "".join(f"_{char.lower()}" if char.isupper() else char for char in sheet_name).lstrip("_")
        path = os.path.join(out_dir, f"{filename}.csv")
        headers = SERVICE_ATTRIBUTION_HEADERS if sheet_name == "ServiceAttribution" else METRIC_HEADERS
        _write(path, headers, rows)
        paths[filename] = path
    return paths
