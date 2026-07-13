"""Standalone HTML operational report for one Trace.

The report is intentionally derived-only: it renders existing Trace/Event/Span facts and
analytics summaries, but it does not store new totals or introduce pricing logic.
"""

from __future__ import annotations

import html
import json
import os
import re
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


def _section_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return f"{slug or 'report'}-section"


def _status_parts(status: Any) -> tuple[str, str]:
    label = "unknown" if status is None else str(status).strip() or "unknown"
    candidate = label.casefold()
    normalized = candidate if candidate in {"pass", "warn", "fail"} else "unknown"
    return normalized, label


def _status_badge(status: Any) -> str:
    normalized, label = _status_parts(status)
    escaped_label = html.escape(label)
    return f'<span class="badge {normalized}" aria-label="Status: {escaped_label}">{escaped_label}</span>'


def _metric_table(title: str, summary: dict[str, Any]) -> str:
    rows = []
    for key, value in summary.items():
        if key == "rows":
            continue
        rendered_value = _status_badge(value) if key in {"status", "overall_status"} else _value(value)
        rows.append(f'<tr><th scope="row">{html.escape(key)}</th><td>{rendered_value}</td></tr>')
    escaped_title = html.escape(title)
    section_id = _section_id(title)
    return (
        f'<section aria-labelledby="{section_id}"><h2 id="{section_id}">{escaped_title}</h2>'
        f'<div class="table-wrap" role="region" aria-labelledby="{section_id}" tabindex="0">'
        f'<table class="metric-table"><caption>{escaped_title}</caption><tbody>{"".join(rows)}</tbody></table>'
        "</div></section>"
    )


def _rows_table(title: str, rows: Iterable[dict[str, Any]], *, empty_message: str | None = None) -> str:
    materialized = list(rows)
    escaped_title = html.escape(title)
    section_id = _section_id(title)
    if not materialized:
        message = empty_message or f"No {title.casefold()} data available."
        return (
            f'<section aria-labelledby="{section_id}"><h2 id="{section_id}">{escaped_title}</h2>'
            f'<p class="empty-state">{html.escape(message)}</p></section>'
        )
    headers: list[str] = []
    seen_headers: set[str] = set()
    for row in materialized:
        for header in row:
            if header not in seen_headers:
                headers.append(header)
                seen_headers.add(header)
    head = "".join(f'<th scope="col">{html.escape(header)}</th>' for header in headers)
    body = []
    for row in materialized:
        status_value = row.get("status")
        normalized_status, _ = _status_parts(status_value)
        row_class = f' class="status-{normalized_status}"' if "status" in headers else ""
        body.append(
            f"<tr{row_class}>"
            + "".join(f"<td>{_status_badge(row.get(header)) if header == 'status' else _value(row.get(header))}</td>" for header in headers)
            + "</tr>"
        )
    return (
        f'<section aria-labelledby="{section_id}"><h2 id="{section_id}">{escaped_title}</h2>'
        f'<div class="table-wrap" role="region" aria-labelledby="{section_id}" tabindex="0">'
        f'<table class="data-table"><caption>{escaped_title}</caption>'
        f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div></section>'
    )


def _anomaly_table(trace: Trace) -> str:
    rows = [{"event_id": signal.event_id, "code": signal.code, "detail": signal.detail} for signal in detect_anomalies(trace)]
    return _rows_table("Anomalies", rows, empty_message="No anomalies detected.")


def _readiness_verdict(readiness: dict[str, Any]) -> str:
    normalized_status, _ = _status_parts(readiness.get("overall_status"))
    return (
        f'<section class="verdict status-{normalized_status}" aria-labelledby="readiness-verdict">'
        '<div class="verdict-copy"><p class="eyebrow">Provider validation readiness</p>'
        f'<h2 id="readiness-verdict">Evidence status {_status_badge(readiness.get("overall_status"))}</h2>'
        '<p class="verdict-note">Based on adapter and fixture evidence. '
        "This status does not describe the health of the selected trace.</p></div>"
        '<dl class="verdict-stats">'
        f'<div><dt>Surfaces</dt><dd>{_value(readiness.get("surface_count"))}</dd></div>'
        f'<div><dt>Pass</dt><dd>{_value(readiness.get("pass_count"))}</dd></div>'
        f'<div><dt>Warn</dt><dd>{_value(readiness.get("warn_count"))}</dd></div>'
        f'<div><dt>Fail</dt><dd>{_value(readiness.get("fail_count"))}</dd></div>'
        "</dl></section>"
    )


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
        # The headline number never travels without its epistemic status. observed_total is a
        # POINT value only when total_is_lower_bound is False; otherwise it is a FLOOR (true
        # total >= it), so the floor/estimate/ceiling band and the flag sit right beside it here
        # rather than only in the separate Coverage section below — a reader scanning the Trace
        # Summary must not mistake a floor for a measurement (see test_lower_bound_signal_regression).
        "total_is_lower_bound": rollup.total_is_lower_bound,
        "headline_floor_tokens": rollup.headline_floor_tokens,
        "headline_estimate_tokens": rollup.headline_estimate_tokens,
        "headline_ceiling_tokens": rollup.headline_ceiling_tokens,
        "capture_completeness_ratio": rollup.capture_completeness_ratio,
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
        _rows_table(
            "Service Attribution",
            build_service_attribution(trace)["rows"],
            empty_message="No service attribution data is available for this trace.",
        ),
        _rows_table(
            "Provider Validation Matrix",
            provider_matrix,
        ),
        _anomaly_table(trace),
    ]
    css = """
:root{color-scheme:light;--ink:#1f2933;--muted:#52606d;--surface:#fff;--page:#f5f7fa;--line:#cbd2dc;--navy:#18202f;--focus:#2563eb}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:"Segoe UI",Arial,sans-serif;margin:0;background:var(--page);color:var(--ink);line-height:1.45}
.skip-link{
  position:fixed;z-index:10;left:16px;top:0;transform:translateY(-140%);
  background:#fff;color:#123b6d;padding:10px 14px;border-radius:0 0 6px 6px;font-weight:700
}
.skip-link:focus{transform:translateY(0)}
header{background:var(--navy);color:#fff;padding:24px 32px}
.header-inner{max-width:1280px;margin:auto}
.report-kind{margin:0 0 4px;color:#d9e2ec;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
main{padding:24px 32px;max-width:1344px;margin:auto}
section{margin:0 0 24px}
h1{font-size:clamp(24px,3vw,32px);line-height:1.2;margin:0;overflow-wrap:anywhere}
h2{font-size:18px;line-height:1.3;margin:0 0 10px}
.table-wrap{
  overflow-x:auto;background:var(--surface);border:1px solid var(--line);
  border-radius:8px;box-shadow:0 1px 2px rgba(16,24,40,.05)
}
.table-wrap:focus-visible,.skip-link:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
table{border-collapse:collapse;width:100%;background:var(--surface)}
.data-table{min-width:720px}
caption{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
th,td{
  border:0;border-bottom:1px solid var(--line);border-right:1px solid var(--line);
  padding:9px 11px;text-align:left;vertical-align:top;font-size:13px;overflow-wrap:anywhere
}
th:last-child,td:last-child{border-right:0}
tbody tr:last-child>th,tbody tr:last-child>td{border-bottom:0}
th{background:#eef2f7;font-weight:650;color:#243b53}
.metric-table th{width:min(42%,360px)}
.badge{
  display:inline-block;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:750;
  line-height:1.35;text-transform:uppercase;letter-spacing:.02em
}
.pass{background:#dcfce7;color:#166534}
.warn{background:#fef3c7;color:#854d0e}
.fail{background:#fee2e2;color:#991b1b}
.unknown{background:#e5e7eb;color:#374151}
tr.status-warn td{background:#fffbeb}
tr.status-fail td{background:#fef2f2}
.verdict{
  display:grid;grid-template-columns:minmax(0,1.5fr) minmax(300px,1fr);gap:24px;align-items:center;
  background:var(--surface);border:1px solid var(--line);border-left:6px solid #64748b;
  border-radius:10px;padding:20px 22px;box-shadow:0 2px 8px rgba(16,24,40,.07)
}
.verdict.status-pass{border-left-color:#15803d}
.verdict.status-warn{border-left-color:#d97706}
.verdict.status-fail{border-left-color:#be123c}
.eyebrow{margin:0 0 6px;color:var(--muted);font-size:12px;font-weight:750;letter-spacing:.08em;text-transform:uppercase}
.verdict h2{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:22px}
.verdict-note{margin:0;color:var(--muted);max-width:70ch}
.verdict-stats{display:grid;grid-template-columns:repeat(4,minmax(60px,1fr));gap:8px;margin:0}
.verdict-stats div{background:#f8fafc;border:1px solid #e2e8f0;border-radius:7px;padding:10px;text-align:center}
.verdict-stats dt{font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase}
.verdict-stats dd{margin:2px 0 0;font-size:20px;font-weight:750;font-variant-numeric:tabular-nums}
.empty-state{margin:0;background:var(--surface);border:1px dashed var(--line);border-radius:8px;padding:16px;color:var(--muted)}
@media (max-width:700px){
  header{padding:20px 16px}
  main{padding:18px 12px}
  section{margin-bottom:20px}
  .verdict{grid-template-columns:1fr;padding:17px}
  .verdict-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .metric-table th{width:48%}
  th,td{padding:8px;font-size:12px}
}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{
  @page{margin:12mm}
  body{background:#fff;color:#000;font-size:10pt;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .skip-link{display:none}
  header{background:#fff;color:#000;border-bottom:2px solid #000;padding:0 0 12px}
  .report-kind{color:#333}
  main{max-width:none;padding:14px 0 0}
  .verdict,.table-wrap{box-shadow:none}
  .table-wrap{overflow:visible}
  .data-table{min-width:0}
  h2{break-after:avoid-page}
  tr{break-inside:avoid-page}
  th,td{font-size:8.5pt;padding:5px 6px}
}
"""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title_text)}</title><style>{css}</style></head>"
        '<body><a class="skip-link" href="#report-content">Skip to report content</a>'
        '<header><div class="header-inner"><p class="report-kind">Operational trace report</p>'
        f"<h1>{html.escape(title_text)}</h1></div></header>"
        f'<main id="report-content" tabindex="-1">{_readiness_verdict(readiness)}' + "".join(sections) + "</main></body></html>"
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
