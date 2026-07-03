"""Standalone HTML operational report for one Trace.

The report is intentionally derived-only: it renders existing Trace/Event/Span facts and
analytics summaries, but it does not store new totals or introduce pricing logic.
"""

from __future__ import annotations

import html
import json
import os
from collections.abc import Iterable
from typing import Any

from tracker.analytics.agent import build_agent_summary
from tracker.analytics.anomaly_signals import detect_anomalies
from tracker.analytics.cache import build_cache_summary
from tracker.analytics.coverage import build_coverage_exactness
from tracker.analytics.latency import build_latency_summary
from tracker.analytics.observation_contract import build_observation_contract_summary
from tracker.analytics.provider_validation import (
    build_provider_validation_matrix,
    summarize_provider_validation,
)
from tracker.analytics.rag import build_rag_summary
from tracker.analytics.reliability import build_reliability_summary
from tracker.analytics.service_attribution import build_service_attribution
from tracker.derive.trace_rollup import roll_up
from tracker.models.trace import Trace
from tracker.validation.fixture_manifest import realistic_fixture_records


def _value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return html.escape(json.dumps(value, ensure_ascii=False, sort_keys=True))
    if value is None:
        return ""
    return html.escape(str(value))


def _metric_table(title: str, summary: dict[str, Any]) -> str:
    rows = []
    for key, value in summary.items():
        if key == "rows":
            continue
        rows.append(f"<tr><th>{html.escape(key)}</th><td>{_value(value)}</td></tr>")
    return f"<section><h2>{html.escape(title)}</h2><table>{''.join(rows)}</table></section>"


def _status_badge(status: str) -> str:
    normalized = status if status in {"pass", "warn", "fail"} else "unknown"
    return f'<span class="badge {normalized}">{html.escape(status)}</span>'


def _rows_table(title: str, rows: Iterable[dict[str, Any]]) -> str:
    materialized = list(rows)
    if not materialized:
        return f"<section><h2>{html.escape(title)}</h2><p>No rows.</p></section>"
    headers = list(materialized[0])
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = []
    for row in materialized:
        status_value = row.get("status")
        # escaped: `status` can trace back to event.observation, which any proxy/collector may
        # populate from an external provider response — never trust it unescaped in an HTML
        # attribute, even though today's call sites only ever pass known-safe literals.
        row_class = f' class="status-{html.escape(str(status_value))}"' if status_value else ""
        body.append(
            f"<tr{row_class}>"
            + "".join(f"<td>{_status_badge(row.get(header)) if header == 'status' else _value(row.get(header))}</td>" for header in headers)
            + "</tr>"
        )
    return f"<section><h2>{html.escape(title)}</h2><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></section>"


def _anomaly_table(trace: Trace) -> str:
    rows = [{"event_id": signal.event_id, "code": signal.code, "detail": signal.detail} for signal in detect_anomalies(trace)]
    return _rows_table("Anomalies", rows)


def render_html_report(trace: Trace, *, title: str | None = None) -> str:
    """Render a standalone HTML report string."""
    rollup = roll_up(trace)
    title_text = title or f"Token Tracker Report - {trace.trace_id}"
    trace_summary = {
        "trace_id": trace.trace_id,
        "business_id": trace.business_id,
        "workflow": trace.workflow,
        "environment": trace.environment,
        "observed_total_contributing_tokens": rollup.observed_total_contributing_tokens,
        "event_count": rollup.event_count,
        "superseded_event_count": rollup.superseded_event_count,
        "flagged_event_count": rollup.flagged_event_count,
        "span_count": len(trace.spans),
    }
    provider_matrix = build_provider_validation_matrix(realistic_fixture_records())
    readiness = summarize_provider_validation(provider_matrix)
    sections = [
        _metric_table("Readiness Overview", readiness),
        _metric_table("Trace Summary", trace_summary),
        _metric_table("Coverage And Exactness", build_coverage_exactness(trace)),
        _metric_table("Latency", build_latency_summary(trace)),
        _metric_table("Reliability", build_reliability_summary(trace)),
        _metric_table("Observation Contract", build_observation_contract_summary(trace)),
        _metric_table("Cache Efficiency", build_cache_summary(trace)),
        _metric_table("RAG Efficiency", build_rag_summary(trace)),
        _metric_table("Agent Efficiency", build_agent_summary(trace)),
        _rows_table("Service Attribution", build_service_attribution(trace)["rows"]),
        _rows_table(
            "Provider Validation Matrix",
            provider_matrix,
        ),
        _anomaly_table(trace),
    ]
    css = """
body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f7f8fa;color:#1f2933}
header{background:#18202f;color:#fff;padding:24px 32px}
main{padding:24px 32px;max-width:1280px;margin:auto}
section{margin:0 0 24px 0}
h1{font-size:28px;margin:0}
h2{font-size:18px;margin:0 0 10px 0}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d8dde6}
th,td{border:1px solid #d8dde6;padding:8px 10px;text-align:left;vertical-align:top;font-size:13px}
th{background:#eef2f7;font-weight:600}
p{background:#fff;border:1px solid #d8dde6;padding:12px}
.badge{display:inline-block;border-radius:999px;padding:2px 8px;font-size:12px;font-weight:700;text-transform:uppercase}
.pass{background:#dcfce7;color:#166534}
.warn{background:#fef3c7;color:#92400e}
.fail{background:#fee2e2;color:#991b1b}
tr.status-warn td{background:#fffbeb}
tr.status-fail td{background:#fef2f2}
"""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title_text)}</title><style>{css}</style></head>"
        f"<body><header><h1>{html.escape(title_text)}</h1></header><main>" + "".join(sections) + "</main></body></html>"
    )


def export_html_report(trace: Trace, path: str, *, title: str | None = None) -> str:
    """Write a standalone HTML operational report and return its path."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_html_report(trace, title=title))
    return path


__all__ = ["export_html_report", "render_html_report"]
