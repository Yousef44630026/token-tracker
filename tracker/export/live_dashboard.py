"""Live local dashboard - animates as the ledger receives tokens.

A small standard-library HTTP server that reads the ledger (archive-aware) and serves:
  * ``/``      a self-contained HTML page that polls ``/data`` and animates counters when
               new tokens arrive (per service / provider / model), flashing changed rows.
  * ``/data``  JSON aggregates, recomputed ONLY when the ledger actually changed (cached by
               store signature), so polling is cheap and the numbers move exactly when new
               events land.

Loopback only; it reads the local ledger directly (no collector round-trip, no auth needed).
Totals use the canonical correlation-effective projection plus
``event_contributing_tokens`` so partial/final streams are never double-counted and the live
numbers match the collector, Excel, and Power BI exports.

Run:  python -m tracker.export.live_dashboard --store C:\\ai-token-tracker-data\\collector_events.jsonl --port 8790
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import socket
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import parse

from tracker.derive.derived_fields import event_contributing_tokens
from tracker.derive.effective_events import iter_effective_events
from tracker.derive.headline import HeadlineBandAccumulator
from tracker.models.enums import DataQualityFlag, Overlap, PrecisionLevel, Trust
from tracker.storage.file_repository import FileRepository


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Refuse concurrent dashboard instances on the same address."""

    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _signature(store: str) -> tuple:
    parts: list[tuple[int, int]] = []
    for path in (store, f"{store}.archive"):
        try:
            st = os.stat(path)
            parts.append((int(st.st_size), int(st.st_mtime_ns)))
        except FileNotFoundError:
            parts.append((0, 0))
    # archive is a directory; include its segment count/mtimes
    arch = f"{store}.archive"
    try:
        for entry in sorted(os.scandir(arch), key=lambda e: e.name):
            if entry.name.endswith(".jsonl.gz"):
                st = entry.stat()
                parts.append((int(st.st_size), int(st.st_mtime_ns)))
    except FileNotFoundError:
        pass
    return tuple(parts)


_WINDOWS = frozenset({"today", "24h", "7d", "30d", "date", "all"})
_USAGE_LOSS_FLAGS = frozenset(
    {
        DataQualityFlag.RAW_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_RESPONSE_UNPARSEABLE.value,
        DataQualityFlag.NORMALIZATION_ERROR.value,
    }
)
_CORRELATION_RISK_FLAGS = frozenset(
    {
        DataQualityFlag.CORRELATION_ID_COLLISION.value,
        DataQualityFlag.DUPLICATE_FINAL_UNVERIFIED.value,
    }
)


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _event_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _period(
    window: str,
    *,
    selected_date: str | None,
    timezone_offset_minutes: int,
    now: dt.datetime,
) -> dict[str, Any]:
    if window not in _WINDOWS:
        raise ValueError(f"unknown time window: {window}")
    if not -840 <= timezone_offset_minutes <= 840:
        raise ValueError("timezone offset must be between -840 and 840 minutes")

    # JavaScript getTimezoneOffset() is UTC - local time, hence the sign inversion.
    timezone = dt.timezone(-dt.timedelta(minutes=timezone_offset_minutes))
    now_utc = _utc(now)
    local_now = now_utc.astimezone(timezone)
    start_local: dt.datetime | None = None
    end_local: dt.datetime | None = None
    bucket = "day"
    label = "All time"

    if window == "24h":
        start_utc = now_utc - dt.timedelta(hours=24)
        end_utc = now_utc
        bucket = "hour"
        label = "Last 24 hours"
    elif window in {"today", "date", "7d", "30d"}:
        if window == "date":
            if not selected_date:
                raise ValueError("date window requires YYYY-MM-DD")
            try:
                local_date = dt.date.fromisoformat(selected_date)
            except ValueError as exc:
                raise ValueError("date must use YYYY-MM-DD") from exc
        else:
            local_date = local_now.date()
        days = {"today": 1, "date": 1, "7d": 7, "30d": 30}[window]
        start_date = local_date - dt.timedelta(days=days - 1)
        start_local = dt.datetime.combine(start_date, dt.time.min, timezone)
        end_local = dt.datetime.combine(local_date + dt.timedelta(days=1), dt.time.min, timezone)
        start_utc = start_local.astimezone(dt.UTC)
        end_utc = end_local.astimezone(dt.UTC)
        bucket = "hour" if days == 1 else "day"
        if window == "today":
            label = f"Today - {local_date.isoformat()}"
        elif window == "date":
            label = local_date.isoformat()
        else:
            label = f"Last {days} calendar days"
    else:
        start_utc = None
        end_utc = None

    return {
        "window": window,
        "label": label,
        "bucket": bucket,
        "start": start_utc.isoformat() if start_utc else None,
        "end": end_utc.isoformat() if end_utc else None,
        "start_dt": start_utc,
        "end_dt": end_utc,
        "timezone_offset_minutes": timezone_offset_minutes,
        "timezone": timezone,
    }


def _bucket_key(timestamp: dt.datetime, period: dict[str, Any]) -> str:
    local = timestamp.astimezone(period["timezone"])
    if period["bucket"] == "hour":
        return local.replace(minute=0, second=0, microsecond=0).isoformat()
    return local.date().isoformat()


def _series(period: dict[str, Any], buckets: dict[str, list[int]]) -> list[dict[str, Any]]:
    keys: list[str]
    start = period["start_dt"]
    end = period["end_dt"]
    if start is None or end is None:
        keys = sorted(buckets)
    else:
        cursor = start.astimezone(period["timezone"])
        if period["bucket"] == "hour":
            cursor = cursor.replace(minute=0, second=0, microsecond=0)
            step = dt.timedelta(hours=1)
            keys = []
            while cursor.astimezone(dt.UTC) < end:
                keys.append(cursor.isoformat())
                cursor += step
        else:
            cursor_date = cursor.date()
            end_date = end.astimezone(period["timezone"]).date()
            keys = []
            while cursor_date < end_date:
                keys.append(cursor_date.isoformat())
                cursor_date += dt.timedelta(days=1)

    rows: list[dict[str, Any]] = []
    for key in keys:
        events, tokens = buckets.get(key, [0, 0])
        if period["bucket"] == "hour":
            local = dt.datetime.fromisoformat(key)
            label = local.strftime("%H:00") if period["window"] in {"today", "date"} else local.strftime("%m-%d %H:00")
        else:
            label = key
        rows.append({"key": key, "label": label, "events": events, "tokens": tokens})
    return rows


def aggregate(
    store: str,
    *,
    window: str = "7d",
    selected_date: str | None = None,
    timezone_offset_minutes: int = 0,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at = _utc(now or dt.datetime.now(dt.UTC))
    period = _period(
        window,
        selected_date=selected_date,
        timezone_offset_minutes=timezone_offset_minutes,
        now=generated_at,
    )
    repo = FileRepository(store)
    raw_events = 0
    effective_events = 0
    superseded_events = 0
    excluded_events = 0
    undated_events = 0
    total = 0
    by_service: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_provider: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_model: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    flags: dict[str, int] = defaultdict(int)
    timeline: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    headline = HeadlineBandAccumulator()
    exact_tokens = 0
    estimated_tokens = 0
    unverified_tokens = 0
    unknown_quantity_count = 0
    provider_total_observations = 0
    mismatch_event_count = 0
    schema_drift_event_count = 0
    correlation_risk_event_count = 0
    usage_loss_event_count = 0
    under_attributed_tokens = 0
    over_attributed_tokens = 0
    request_latency: dict[str, dict[str, bool]] = {}
    for event in iter_effective_events(repo.iter_events()):
        timestamp = _event_time(event.timestamp)
        if timestamp is None and window != "all":
            undated_events += 1
            continue
        start = period["start_dt"]
        end = period["end_dt"]
        if timestamp is not None and ((start is not None and timestamp < start) or (end is not None and timestamp >= end)):
            continue
        raw_events += 1
        headline.add(event)
        event_flags = set(event.data_quality_flags)
        for flag in event.data_quality_flags:
            flags[flag] += 1
        # A collision is an identity failure even when reconciliation retires the affected
        # row. Counting it before the supersession gate preserves the audit signal.
        correlation_risk_event_count += int(bool(event_flags & _CORRELATION_RISK_FLAGS))
        if event.superseded:
            superseded_events += 1
            continue
        if not event.is_authoritative:
            excluded_events += 1
            continue

        effective_events += 1
        contributing = event_contributing_tokens(event)
        total += contributing
        provider_total_observations += int(event.provider_total_tokens is not None)
        mismatch_event_count += int(event.event_total_mismatch not in (None, 0))
        schema_drift_event_count += int(DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value in event_flags)
        usage_loss_event_count += int(bool(event_flags & _USAGE_LOSS_FLAGS))
        under_attributed_tokens += event.under_attributed_tokens
        over_attributed_tokens += event.over_attributed_tokens
        request_id = event.request_correlation_id or event.event_id
        local_log_import = bool(
            event_flags
            & {
                DataQualityFlag.CLAUDE_CODE_LOCAL_USAGE.value,
                DataQualityFlag.CODEX_LOCAL_TOKEN_COUNT.value,
            }
        )
        request_state = request_latency.setdefault(request_id, {"observed": False, "applicable": False})
        request_state["observed"] = request_state["observed"] or event.observation.get("duration_ms") is not None
        request_state["applicable"] = request_state["applicable"] or not local_log_import
        for quantity in event.quantities:
            if quantity.precision_level == PrecisionLevel.EXACT:
                exact_tokens += quantity.quantity_in_total
            elif quantity.precision_level == PrecisionLevel.ESTIMATE:
                estimated_tokens += quantity.quantity_in_total
            elif quantity.precision_level == PrecisionLevel.UNKNOWN or quantity.quantity is None:
                unknown_quantity_count += 1
            if (
                quantity.trust == Trust.UNVERIFIED
                and quantity.overlap == Overlap.INDEPENDENT
                and quantity.quantity is not None
            ):
                unverified_tokens += quantity.quantity
        service = str(event.observation.get("service_name") or "unknown")
        provider = event.provider or "unknown"
        model = event.model or "unknown"
        for bucket, key in ((by_service, service), (by_provider, provider), (by_model, model)):
            bucket[key][0] += 1
            bucket[key][1] += contributing
        if timestamp is not None:
            timeline[_bucket_key(timestamp, period)][0] += 1
            timeline[_bucket_key(timestamp, period)][1] += contributing
        else:
            timeline["undated"][0] += 1
            timeline["undated"][1] += contributing

    def rows(bucket: dict[str, list[int]], *, limit: int = 25) -> list[dict[str, Any]]:
        ordered = [
            {"name": name, "events": n, "tokens": tok}
            for name, (n, tok) in sorted(bucket.items(), key=lambda kv: kv[1][1], reverse=True)
        ]
        if len(ordered) <= limit:
            return ordered
        visible = ordered[: limit - 1]
        remainder = ordered[limit - 1 :]
        visible.append(
            {
                "name": f"Other ({len(remainder)})",
                "events": sum(row["events"] for row in remainder),
                "tokens": sum(row["tokens"] for row in remainder),
            }
        )
        return visible

    known_magnitude = exact_tokens + estimated_tokens + unverified_tokens
    request_count = len(request_latency)
    latency_observations = sum(state["observed"] for state in request_latency.values())
    latency_applicable_requests = sum(state["applicable"] for state in request_latency.values())
    band = headline.to_band()
    blocking_quality = usage_loss_event_count + schema_drift_event_count + correlation_risk_event_count + int(
        over_attributed_tokens > 0
    )
    uncertain_quality = (
        estimated_tokens
        + unverified_tokens
        + unknown_quantity_count
        + mismatch_event_count
        + int(under_attributed_tokens > 0)
    )
    quality_status = "blocked" if blocking_quality else ("warning" if uncertain_quality else "clean")
    coverage_values = [
        provider_total_observations / effective_events if effective_events else None,
        latency_observations / latency_applicable_requests if latency_applicable_requests else None,
    ]
    observed_coverage = [value for value in coverage_values if value is not None]
    if not observed_coverage or all(value == 0 for value in observed_coverage):
        coverage_status = "missing"
    elif len(observed_coverage) == len(coverage_values) and all(value == 1.0 for value in observed_coverage):
        coverage_status = "complete"
    else:
        coverage_status = "partial"

    return {
        "events": raw_events,
        "effective_events": effective_events,
        "superseded_events": superseded_events,
        "excluded_events": excluded_events,
        "undated_events": undated_events,
        "total_tokens": total,
        "by_service": rows(by_service),
        "by_provider": rows(by_provider),
        "by_model": rows(by_model),
        "flags": dict(sorted(flags.items(), key=lambda kv: kv[1], reverse=True)),
        "quality": {
            "status": quality_status,
            "coverage_status": coverage_status,
            "exact_tokens": exact_tokens,
            "estimated_tokens": estimated_tokens,
            "unverified_tokens": unverified_tokens,
            "known_exact_token_share": exact_tokens / known_magnitude if known_magnitude else None,
            "provider_total_coverage": provider_total_observations / effective_events if effective_events else None,
            "latency_coverage": latency_observations / request_count if request_count else None,
            "instrumented_latency_coverage": (
                latency_observations / latency_applicable_requests if latency_applicable_requests else None
            ),
            "latency_applicability": latency_applicable_requests / request_count if request_count else None,
            "request_count": request_count,
            "unknown_quantity_count": unknown_quantity_count,
            "mismatch_event_count": mismatch_event_count,
            "schema_drift_event_count": schema_drift_event_count,
            "correlation_risk_event_count": correlation_risk_event_count,
            "usage_loss_event_count": usage_loss_event_count,
            "under_attributed_tokens": under_attributed_tokens,
            "over_attributed_tokens": over_attributed_tokens,
        },
        "headline": {
            "floor_tokens": band.floor_tokens,
            "estimate_tokens": band.estimate_tokens,
            "ceiling_tokens": band.ceiling_tokens,
            "upper_bound_status": band.upper_bound_status,
            "status": band.status,
            "attribution_status": band.attribution_status,
            "capture_completeness_ratio": band.capture_completeness_ratio,
            "total_is_lower_bound": band.total_is_lower_bound,
            "total_is_upper_bound": band.total_is_upper_bound,
            "open_upper_bound_event_count": band.open_upper_bound_event_count,
            "provider_reconciled_event_count": band.provider_reconciled_event_count,
        },
        "timeline": _series(period, timeline),
        "period": {key: value for key, value in period.items() if key not in {"start_dt", "end_dt", "timezone"}},
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "ledger": store,
    }


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Token Tracker - Live</title>
<style>
:root{color-scheme:light dark;--bg:#0b0e14;--card:#151a23;--line:#2a3443;--fg:#e6edf3;--dim:#9aa7b7;--accent:#3fb950;--accent-ink:#071f0d;--warn:#d29922;--danger:#f85149;--bar:#2f81f7;--flash:rgba(63,185,80,.22)}
@media (prefers-color-scheme:light){:root{--bg:#f5f7fa;--card:#fff;--line:#d8e0ea;--fg:#10151c;--dim:#5b6472;--accent:#1f883d;--accent-ink:#fff;--warn:#9a6700;--danger:#cf222e;--bar:#0969da;--flash:rgba(31,136,61,.14)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:18px 28px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:650}#live{font-size:12px;color:var(--accent);display:flex;align-items:center;gap:7px}
#dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 var(--accent);animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(63,185,80,.6)}70%{box-shadow:0 0 0 8px rgba(63,185,80,0)}100%{box-shadow:0 0 0 0 rgba(63,185,80,0)}}
.stale #dot{background:var(--warn);animation:none}.stale #live{color:var(--warn)}
main{padding:22px 28px 36px;max-width:1280px;margin:auto}
.filters{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.segments{display:flex;border:1px solid var(--line);border-radius:5px;overflow:hidden}
.segments button{appearance:none;border:0;border-right:1px solid var(--line);border-radius:0;background:transparent;color:var(--dim);font:inherit;font-size:13px;padding:7px 11px;cursor:pointer;min-height:36px}
.segments button:last-child{border-right:0}.segments button:hover{color:var(--fg);background:var(--card)}
.segments button.active{background:var(--accent);color:var(--accent-ink);font-weight:650}
.date-control{display:flex;align-items:center;gap:7px;color:var(--dim);font-size:13px;border:1px solid var(--line);border-radius:5px;padding:4px 8px;height:38px}
.date-control.active{border-color:var(--accent);color:var(--fg)}
input[type=date]{font:inherit;color:var(--fg);background:transparent;border:0;outline:0;min-width:128px;color-scheme:dark}
@media (prefers-color-scheme:light){input[type=date]{color-scheme:light}}
.period{color:var(--dim);font-size:12px;margin:8px 0 18px}
.big{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:24px;margin-bottom:8px}
.kpi{display:flex;flex-direction:column;min-width:0}.kpi .n{font-size:36px;font-weight:700;font-variant-numeric:tabular-nums;transition:color .25s;overflow-wrap:anywhere}
.kpi .l{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:0}
.toast{position:fixed;right:20px;bottom:20px;background:var(--accent);color:var(--accent-ink);padding:10px 16px;border-radius:6px;font-weight:650;opacity:0;transform:translateY(8px);transition:.3s;z-index:2}
.toast.show{opacity:1;transform:none}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;min-width:0}
.card.wide{grid-column:1/-1}.card h2{font-size:12px;text-transform:uppercase;letter-spacing:0;color:var(--dim);margin:0;padding:12px 16px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse}th{padding:7px 16px;color:var(--dim);font-size:11px;font-weight:500;text-align:left;border-bottom:1px solid var(--line)}
th.n,th.t,td.n,td.t{text-align:right}td{padding:8px 16px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
tr:last-child td{border-bottom:0}td.n{color:var(--dim)}td.t{font-weight:650}tr.flash td{background:var(--flash)}
.trust{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:6px;overflow:hidden;margin:18px 0 8px}.trust-item{background:var(--card);padding:11px 14px;min-width:0}.trust-item .v{display:block;font-size:18px;font-weight:700;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.trust-item .k{display:block;color:var(--dim);font-size:11px;text-transform:uppercase}.quality{color:var(--dim);font-size:12px;margin-top:10px}.quality strong.clean{color:var(--accent)}.quality strong.warning{color:var(--warn)}.quality strong.blocked{color:var(--danger)}.empty{color:var(--dim);text-align:center;padding:24px}
.timeline{padding:12px 16px 16px;display:grid;gap:7px;max-height:430px;overflow:auto}
.timeline-row{display:grid;grid-template-columns:104px minmax(100px,1fr) 118px;align-items:center;gap:12px;min-height:24px;font-variant-numeric:tabular-nums}
.timeline-label{font-size:12px;color:var(--dim);white-space:nowrap}.track{height:10px;background:var(--bg);border:1px solid var(--line);border-radius:3px;overflow:hidden}
.fill{height:100%;background:var(--bar);min-width:0}.timeline-value{text-align:right;font-size:12px;font-weight:650;white-space:nowrap}
@media(max-width:850px){.big,.trust{grid-template-columns:repeat(2,minmax(130px,1fr))}.grid{grid-template-columns:1fr}.card.wide{grid-column:auto}}
@media(max-width:520px){header,main{padding-left:16px;padding-right:16px}.filters{align-items:stretch}.segments{width:100%;overflow:auto}.segments button{flex:1;white-space:nowrap}.date-control{width:100%;justify-content:space-between}.big{gap:16px}.kpi .n{font-size:30px}.timeline-row{grid-template-columns:76px minmax(60px,1fr) 92px;gap:8px}}
</style></head><body>
<header><h1>AI Token Tracker</h1><span id="live"><span id="dot"></span><span id="livetxt">connecting...</span></span></header>
<main>
<div class="filters">
<div class="segments" aria-label="Time range">
<button type="button" data-window="today">Today</button>
<button type="button" data-window="24h">24 hours</button>
<button type="button" data-window="7d" class="active">7 days</button>
<button type="button" data-window="30d">30 days</button>
<button type="button" data-window="all">All time</button>
</div>
<label class="date-control" id="date_control">Date <input type="date" id="date"></label>
</div>
<div class="period" id="period">Loading current contribution...</div>
<div class="big">
<div class="kpi"><span class="n" id="total">-</span><span class="l" id="total_label">tokens over 7 days</span></div>
<div class="kpi"><span class="n" id="events">-</span><span class="l">effective events</span></div>
<div class="kpi"><span class="n" id="exact_share">-</span><span class="l">exact share of known tokens</span></div>
<div class="kpi"><span class="n" id="provider_coverage">-</span><span class="l">provider-total coverage</span></div>
</div>
<div class="trust">
<div class="trust-item"><span class="v" id="band">-</span><span class="k">headline band</span></div>
<div class="trust-item"><span class="v" id="latency_coverage">-</span><span class="k">instrumented latency</span></div>
<div class="trust-item"><span class="v" id="services">-</span><span class="k">active services</span></div>
<div class="trust-item"><span class="v" id="superseded">-</span><span class="k">superseded events</span></div>
</div>
<div id="quality" class="quality"></div>
<div class="grid">
<section class="card wide"><h2 id="timeline_title">Contribution over time</h2><div id="timeline" class="timeline"></div></section>
<section class="card"><h2>By service</h2><table><thead><tr><th>Service</th><th class="n">Events</th><th class="t">Tokens</th></tr></thead><tbody id="by_service"></tbody></table></section>
<section class="card"><h2>By provider</h2><table><thead><tr><th>Provider</th><th class="n">Events</th><th class="t">Tokens</th></tr></thead><tbody id="by_provider"></tbody></table></section>
<section class="card"><h2>By model</h2><table><thead><tr><th>Model</th><th class="n">Events</th><th class="t">Tokens</th></tr></thead><tbody id="by_model"></tbody></table></section>
</div>
</main>
<div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id);const fmt=n=>Number(n||0).toLocaleString();const pct=v=>v===null||v===undefined?'N/A':(Number(v)*100).toFixed(1)+'%';
const tzOffset=new Date().getTimezoneOffset();const localToday=new Date(Date.now()-tzOffset*60000).toISOString().slice(0,10);
const state={window:'7d',date:localToday};let prev={total:0,rows:{},period:null};let first=true;let busy=false;
$('date').value=localToday;$('date').max=localToday;
function choose(window){state.window=window;document.querySelectorAll('[data-window]').forEach(b=>{const active=b.dataset.window===window;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});$('date_control').classList.toggle('active',window==='date');first=true;prev={total:0,rows:{},period:null};tick();}
document.querySelectorAll('[data-window]').forEach(button=>button.addEventListener('click',()=>choose(button.dataset.window)));
$('date').addEventListener('change',()=>{if($('date').value){state.date=$('date').value;choose('date');}});
function tbl(id,rows){const target=$(id);const prior=prev.rows[id]||{};target.replaceChildren();
 if(!rows.length){const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=3;td.className='empty';td.textContent='No events in this period';tr.appendChild(td);target.appendChild(tr);}
 for(const row of rows){const tr=document.createElement('tr');if(!first&&prior[row.name]!==undefined&&prior[row.name]!==row.tokens)tr.className='flash';
  const name=document.createElement('td');name.textContent=row.name;const events=document.createElement('td');events.className='n';events.textContent=fmt(row.events);
  const tokens=document.createElement('td');tokens.className='t';tokens.textContent=fmt(row.tokens);tr.append(name,events,tokens);target.appendChild(tr);}
 prev.rows[id]={};for(const row of rows)prev.rows[id][row.name]=row.tokens;}
function drawTimeline(rows,period){const target=$('timeline');target.replaceChildren();$('timeline_title').textContent=period.bucket==='hour'?'Token consumption by hour':'Daily token consumption';
 if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='No dated events in this period';target.appendChild(empty);return;}
 const max=Math.max(1,...rows.map(row=>row.tokens));for(const row of rows){const line=document.createElement('div');line.className='timeline-row';
  const label=document.createElement('span');label.className='timeline-label';label.textContent=row.label;const track=document.createElement('div');track.className='track';
  const fill=document.createElement('div');fill.className='fill';fill.style.width=(row.tokens/max*100)+'%';track.appendChild(fill);
  const value=document.createElement('span');value.className='timeline-value';value.textContent=fmt(row.tokens)+' tok';line.title=fmt(row.events)+' events';line.append(label,track,value);target.appendChild(line);}}
function toast(message){const el=$('toast');el.textContent=message;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2200);}
async function tick(){if(busy)return;busy=true;try{const params=new URLSearchParams({window:state.window,tz_offset:String(tzOffset)});if(state.window==='date')params.set('date',state.date);
 const response=await fetch('/data?'+params.toString(),{cache:'no-store'});if(!response.ok)throw new Error('dashboard data '+response.status);const data=await response.json();
 document.body.classList.remove('stale');$('livetxt').textContent='live | '+data.generated_at.slice(11,19)+'Z';$('total').textContent=fmt(data.total_tokens);
 $('events').textContent=fmt(data.effective_events);$('superseded').textContent=fmt(data.superseded_events);$('services').textContent=fmt(data.by_service.length);
 $('exact_share').textContent=pct(data.quality.known_exact_token_share);$('provider_coverage').textContent=pct(data.quality.provider_total_coverage);$('latency_coverage').textContent=pct(data.quality.instrumented_latency_coverage);
 const ceiling=data.headline.ceiling_tokens===null?'open':fmt(data.headline.ceiling_tokens);$('band').textContent=fmt(data.headline.floor_tokens)+' / '+fmt(data.headline.estimate_tokens)+' / '+ceiling;
 $('total_label').textContent=data.headline.total_is_lower_bound?'observed token floor':(data.period.window==='all'?'all-time measured tokens':'period measured tokens');$('period').textContent=data.period.label+' | '+(data.period.bucket==='hour'?'hourly':'daily')+' contribution';
 const periodKey=data.period.window+'|'+(data.period.start||'all')+'|'+tzOffset;if(!first&&prev.period===periodKey&&data.total_tokens>prev.total){$('total').style.color='var(--accent)';setTimeout(()=>$('total').style.color='',400);toast('+'+fmt(data.total_tokens-prev.total)+' tokens in '+data.period.label);}
 tbl('by_service',data.by_service);tbl('by_provider',data.by_provider);tbl('by_model',data.by_model);drawTimeline(data.timeline,data.period);
 const flags=Object.entries(data.flags||{});const quality=$('quality');quality.replaceChildren();const status=document.createElement('strong');status.className=data.quality.status;status.textContent='integrity '+data.quality.status;quality.appendChild(status);
 let summary=' | coverage '+data.quality.coverage_status+' | latency applicable '+pct(data.quality.latency_applicability)+' | raw events: '+fmt(data.events)+' | excluded non-authoritative: '+fmt(data.excluded_events)+' | requests: '+fmt(data.quality.request_count);if(data.undated_events)summary+=' | undated excluded: '+fmt(data.undated_events);if(flags.length)summary+=' | flags: '+flags.map(([key,value])=>key+' '+value).join(' | ');quality.appendChild(document.createTextNode(summary));
 prev.total=data.total_tokens;prev.period=periodKey;first=false;
}catch(error){document.body.classList.add('stale');$('livetxt').textContent='disconnected';}finally{busy=false;}}
tick();setInterval(tick,2000);
</script></body></html>"""


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _request_options(path: str) -> dict[str, Any]:
    query = parse.parse_qs(parse.urlparse(path).query)
    window = query.get("window", ["7d"])[0]
    selected_date = query.get("date", [None])[0] or None
    raw_offset = query.get("tz_offset", ["0"])[0]
    try:
        timezone_offset_minutes = int(raw_offset)
    except ValueError as exc:
        raise ValueError("tz_offset must be an integer number of minutes") from exc
    # Validate before the relatively expensive ledger projection.
    _period(
        window,
        selected_date=selected_date,
        timezone_offset_minutes=timezone_offset_minutes,
        now=dt.datetime.now(dt.UTC),
    )
    return {
        "window": window,
        "selected_date": selected_date,
        "timezone_offset_minutes": timezone_offset_minutes,
    }


def make_handler(store: str):
    cache: dict[str, Any] = {"sig": None, "payloads": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed_path = parse.urlparse(self.path).path
            if parsed_path == "/" or parsed_path.startswith("/index"):
                self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed_path == "/data":
                try:
                    options = _request_options(self.path)
                    sig = _signature(store)
                    if sig != cache["sig"]:
                        cache["payloads"] = {}
                        cache["sig"] = sig
                    freshness = int(time.time() // 60) if options["window"] in {"today", "24h", "7d", "30d"} else 0
                    cache_key = (
                        options["window"],
                        options["selected_date"],
                        options["timezone_offset_minutes"],
                        freshness,
                    )
                    if cache_key not in cache["payloads"]:
                        cache["payloads"][cache_key] = json.dumps(aggregate(store, **options)).encode("utf-8")
                    self._send(200, cache["payloads"][cache_key], "application/json")
                except ValueError as exc:
                    body = json.dumps({"error": "invalid_time_filter", "detail": str(exc)}).encode("utf-8")
                    self._send(400, body, "application/json")
                return
            self._send(404, b'{"error":"not_found"}', "application/json")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default=os.environ.get("TRACKER_STORE", r"C:\ai-token-tracker-data\collector_events.jsonl"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    if not _is_loopback(args.host):
        parser.error("live dashboard must bind to a loopback host")
    server = ExclusiveThreadingHTTPServer((args.host, args.port), make_handler(args.store))
    print(f"live dashboard on http://{args.host}:{args.port}  (ledger: {args.store})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
