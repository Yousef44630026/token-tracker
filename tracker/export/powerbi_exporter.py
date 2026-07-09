"""Power BI export package for operational token analytics.

The export is intentionally file-based: Power BI can import the folder of CSV files, while
Excel can still open the same facts when a lighter inspection path is needed. The primary
grain remains the tracker model grain:

- fact_token_events: one row per TokenEvent, with event_contributing_tokens as the safe
  event-grain token total.
- fact_token_quantities: one row per TokenQuantity, with quantity_in_total as the safe
  quantity-grain token total.
- fact_service_daily: pre-aggregated trend rows for production dashboards.

One-shot event iterators are captured into a temporary SQLite snapshot keyed by event_id.
This keeps deduplication and multi-table replay disk-backed instead of retaining every event
and fact row in memory. The snapshot is removed after the export.

No pricing fields are generated here.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from math import ceil
from typing import Any

from tracker.analytics.agent import build_agent_summary
from tracker.analytics.cache import build_cache_summary
from tracker.analytics.coverage import build_coverage_exactness, build_coverage_exactness_from_events
from tracker.analytics.latency import build_latency_summary
from tracker.analytics.observation_contract import build_observation_contract_summary
from tracker.analytics.provider_validation import (
    build_provider_validation_matrix,
    summarize_provider_validation,
)
from tracker.analytics.rag import build_rag_summary
from tracker.analytics.reliability import build_reliability_summary
from tracker.analytics.trust_report import build_trust_report, build_trust_report_from_events
from tracker.models.enums import Additivity, TokenType
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace
from tracker.validation.fixture_manifest import realistic_fixture_records

FACT_TOKEN_EVENT_HEADERS = [
    "event_id",
    "request_correlation_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "event_date",
    "event_month",
    "event_hour",
    "timestamp",
    "service_name",
    "tenant",
    "cloud_provider",
    "region",
    "workflow",
    "environment",
    "provider",
    "api_surface",
    "model",
    "deployment",
    "status",
    "http_status",
    "authoritative",
    "superseded",
    "provider_total_tokens",
    "event_contributing_tokens",
    "input_tokens",
    "fresh_input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
    "thinking_tokens",
    "embedding_tokens",
    "rerank_tokens",
    "duration_ms",
    "time_to_first_token_ms",
    "time_to_last_token_ms",
    "retry_count",
    "measured",
    "error_count",
    "rate_limit_count",
    "flagged_event",
    "provider_total_mismatch",
    "event_total_mismatch",
    "under_attributed_tokens",
    "over_attributed_tokens",
    "quality_flag_count",
    "data_quality_flags",
    "provider_request_id",
    "provider_response_id",
]

FACT_TOKEN_QUANTITY_HEADERS = [
    "event_id",
    "trace_id",
    "event_date",
    "service_name",
    "provider",
    "api_surface",
    "model",
    "deployment",
    "token_type",
    "token_role",
    "quantity",
    "quantity_in_total",
    "precision_level",
    "usage_source",
    "additivity",
    "subtotal_of",
    "export_warning",
]

FACT_SPAN_HEADERS = [
    "span_id",
    "trace_id",
    "parent_span_id",
    "span_type",
    "name",
    "start_ts",
    "end_ts",
    "metadata_json",
]

FACT_SERVICE_DAILY_HEADERS = [
    "event_date",
    "service_name",
    "tenant",
    "cloud_provider",
    "region",
    "workflow",
    "environment",
    "provider",
    "api_surface",
    "model",
    "deployment",
    "event_count",
    "contributing_tokens",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "measured_count",
    "error_count",
    "rate_limit_count",
    "flagged_event_count",
    "provider_total_mismatch_count",
    "event_total_mismatch",
    "under_attributed_tokens",
    "over_attributed_tokens",
    "retry_count",
    "average_duration_ms",
    "p95_duration_ms",
]

DIM_SERVICE_HEADERS = [
    "service_key",
    "service_name",
    "tenant",
    "cloud_provider",
    "region",
    "workflow",
    "environment",
]

DIM_MODEL_HEADERS = [
    "model_key",
    "provider",
    "api_surface",
    "model",
    "deployment",
]

DIM_PROVIDER_SURFACE_HEADERS = [
    "provider_surface_key",
    "provider",
    "api_surface",
    "adapter_name",
    "validation_status",
    "validation_level",
    "gaps",
]

DIM_TOKEN_TYPE_HEADERS = [
    "token_type",
    "purpose",
    "default_dashboard_use",
]

METRIC_SNAPSHOT_HEADERS = [
    "snapshot_ts",
    "metric_group",
    "metric",
    "value",
    "value_json",
]

PROVIDER_VALIDATION_HEADERS = [
    "status",
    "provider",
    "api_surface",
    "adapter_name",
    "validation_level",
    "real_fixture_count",
    "simulated_fixture_count",
    "fixture_count",
    "gaps",
    "fixture_names",
]

DATA_DICTIONARY_HEADERS = [
    "table",
    "column",
    "grain",
    "summable",
    "purpose",
]


def _now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _safe_key(*parts: object) -> str:
    return "|".join(str(part if part not in (None, "") else "unknown") for part in parts)


def _first_observation(event: TokenEvent, *keys: str) -> Any:
    for key in keys:
        value = event.observation.get(key)
        if value not in (None, ""):
            return value
    return None


def _service_name(event: TokenEvent) -> str:
    return str(_first_observation(event, "service_name", "service", "application") or "unknown")


def _tenant(event: TokenEvent) -> str:
    return str(
        _first_observation(
            event,
            "tenant",
            "tenant_id",
            "customer_id",
            "subscription_id",
            "account_id",
            "project_id",
        )
        or "unknown"
    )


_CLOUD_BY_PROVIDER = {
    "azure_openai": "azure",
    "bedrock": "aws",
    "vertex_ai": "gcp",
    # deliberately NOT "gemini": "gcp" — direct Gemini (API-key/AI Studio) is a genuinely
    # different billing/auth surface from Vertex AI, often not tied to any GCP project at
    # all. See the identical fix + rationale in tracker/analytics/service_attribution.py.
}


def _cloud_provider(event: TokenEvent) -> str:
    value = _first_observation(event, "cloud_provider", "cloud")
    if value not in (None, ""):
        return str(value)
    return _CLOUD_BY_PROVIDER.get(event.provider or "", event.provider or "unknown")


def _region(event: TokenEvent) -> str:
    return str(_first_observation(event, "region", "provider_region", "azure_region", "aws_region", "_region") or "unknown")


def _deployment(event: TokenEvent) -> str:
    value = _first_observation(event, "deployment", "deployment_name", "azure_deployment", "aws_model_id", "model_id")
    if value not in (None, ""):
        return str(value)
    for quantity in event.quantities:
        for key in ("azure_deployment", "deployment", "model_id", "aws_model_id"):
            value = quantity.metadata.get(key)
            if value not in (None, ""):
                return str(value)
    return event.model or "unknown"


def _date_parts(timestamp: str | None) -> tuple[str, str, str]:
    if not timestamp:
        return "undated", "undated", "undated"
    text = str(timestamp)
    event_date = text[:10] if len(text) >= 10 else "undated"
    event_month = text[:7] if len(text) >= 7 else "undated"
    event_hour = text[:13] if len(text) >= 13 else "undated"
    return event_date, event_month, event_hour


def _is_authoritative_event(event: TokenEvent) -> bool:
    return not event.superseded and event.is_authoritative


def _quantity_sum(event: TokenEvent, *token_types: TokenType) -> int:
    if not _is_authoritative_event(event):
        return 0
    wanted = set(token_types)
    return sum(quantity.quantity or 0 for quantity in event.quantities if quantity.token_type in wanted)


def _safe_quantity_in_total(event: TokenEvent, quantity) -> int:
    return quantity.quantity_in_total if _is_authoritative_event(event) else 0


def _fresh_input_tokens(event: TokenEvent) -> int:
    """Prompt tokens NOT served from cache — consistent regardless of cache additivity style.

    ``input_tokens`` (raw, via _quantity_sum) already includes the cached portion for
    OpenAI-style providers (cache is subtotal_of input) but NOT for Anthropic-style providers
    (cache is a separate additive bucket) — the same raw column means two different things
    depending on the provider. This is derived from quantity_in_total (which already handles
    both additivity styles correctly) minus VERIFIED cache, so `measures.dax` can compute a
    correct, provider-agnostic cache hit rate. See the identical fix in
    tracker/analytics/cache.py.
    """
    if not _is_authoritative_event(event):
        return 0
    prompt_total = sum(
        quantity.quantity_in_total
        for quantity in event.quantities
        if quantity.token_type in (TokenType.INPUT, TokenType.CACHED_INPUT, TokenType.CACHE_CREATION_INPUT)
    )
    verified_cache = sum(
        quantity.quantity or 0
        for quantity in event.quantities
        if quantity.token_type in (TokenType.CACHED_INPUT, TokenType.CACHE_CREATION_INPUT) and quantity.additivity != Additivity.UNVERIFIED
    )
    return max(prompt_total - verified_cache, 0)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _duration_ms(event: TokenEvent) -> float | None:
    for key in ("duration_ms", "total_duration_ms", "provider_duration_ms"):
        value = _number(event.observation.get(key))
        if value is not None:
            return value
    return None


def _status(event: TokenEvent) -> str:
    return str(event.observation.get("status") or "unknown")


def _is_error(event: TokenEvent) -> bool:
    status = _status(event).lower()
    http_status = event.observation.get("http_status")
    return (
        status in {"error", "failed", "timeout", "rate_limited", "cancelled"}
        or (isinstance(http_status, int) and http_status >= 400)
        or bool(event.observation.get("provider_error_code"))
    )


def _is_measured(event: TokenEvent) -> bool:
    """Whether there is ANY operational signal to judge success/failure from at all.

    ``observation`` is optional and defaults to {} — nothing in this project's own workflow
    helpers populates status/http_status/error fields, so error_count silently read 0 (and a
    Power BI "Success Rate" measure silently read 100%) for every unmeasured event. The
    `measured` column lets DAX measures divide by ONLY the events that were actually judged
    instead of by every row. See the identical fix in tracker/analytics/reliability.py.
    """
    status = event.observation.get("status")
    http_status = event.observation.get("http_status")
    return (
        (isinstance(status, str) and status.strip().lower() not in ("", "unknown"))
        or isinstance(http_status, int)
        or bool(event.observation.get("provider_error_code"))
    )


def _is_rate_limited(event: TokenEvent) -> bool:
    status = _status(event).lower()
    code = str(event.observation.get("provider_error_code") or "").lower()
    http_status = event.observation.get("http_status")
    return status == "rate_limited" or http_status == 429 or "rate" in code


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def _dedupe_events_by_id(events: Iterable[TokenEvent]) -> list[TokenEvent]:
    """Collapse repeated event_ids (identity is the event_id), keeping first occurrence.

    The Trace model already rejects duplicate event_ids, but these fact builders aggregate a
    RAW sequence that bypasses that guard (e.g. an at-least-once collector delivery, or a
    re-read of appended JSONL). Deduping here keeps the 'never double-count' promise at the
    export boundary; order is preserved so output stays deterministic."""
    seen: set[str] = set()
    unique: list[TokenEvent] = []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        unique.append(event)
    return unique


class _DiskEventSnapshot:
    """Replayable, event-id-deduplicated snapshot backed by a temporary SQLite file."""

    def __init__(self, events: Iterable[TokenEvent]) -> None:
        descriptor, self.path = tempfile.mkstemp(
            prefix=".powerbi-event-snapshot-",
            suffix=".sqlite3",
        )
        os.close(descriptor)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("CREATE TABLE events (sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE, payload TEXT NOT NULL)")
        try:
            with self._connection:
                for event in events:
                    self._connection.execute(
                        "INSERT OR IGNORE INTO events(event_id, payload) VALUES (?, ?)",
                        (
                            event.event_id,
                            json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")),
                        ),
                    )
        except Exception:
            self.close()
            raise

    def __iter__(self) -> Iterator[TokenEvent]:
        cursor = self._connection.execute("SELECT payload FROM events ORDER BY sequence")
        for (payload,) in cursor:
            yield TokenEvent.from_dict(json.loads(payload))

    def close(self) -> None:
        self._connection.close()
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def _iter_fact_token_event_rows(events: Iterable[TokenEvent]) -> Iterator[dict[str, Any]]:
    for event in events:
        event_date, event_month, event_hour = _date_parts(event.timestamp)
        duration = _duration_ms(event)
        yield {
            "event_id": event.event_id,
            "request_correlation_id": event.request_correlation_id,
            "trace_id": event.trace_id,
            "span_id": event.span_id,
            "parent_span_id": event.parent_span_id,
            "event_date": event_date,
            "event_month": event_month,
            "event_hour": event_hour,
            "timestamp": event.timestamp,
            "service_name": _service_name(event),
            "tenant": _tenant(event),
            "cloud_provider": _cloud_provider(event),
            "region": _region(event),
            "workflow": event.workflow or "unknown",
            "environment": event.environment or "unknown",
            "provider": event.provider or "unknown",
            "api_surface": event.api_surface or "unknown",
            "model": event.model or "unknown",
            "deployment": _deployment(event),
            "status": _status(event),
            "http_status": event.observation.get("http_status"),
            "authoritative": event.is_authoritative,
            "superseded": event.superseded,
            "provider_total_tokens": event.provider_total_tokens,
            "event_contributing_tokens": event.event_contributing_tokens,
            "input_tokens": _quantity_sum(event, TokenType.INPUT),
            "fresh_input_tokens": _fresh_input_tokens(event),
            "output_tokens": _quantity_sum(event, TokenType.OUTPUT),
            "cached_input_tokens": _quantity_sum(event, TokenType.CACHED_INPUT),
            "cache_creation_input_tokens": _quantity_sum(event, TokenType.CACHE_CREATION_INPUT),
            "reasoning_tokens": _quantity_sum(event, TokenType.REASONING),
            "thinking_tokens": _quantity_sum(event, TokenType.THINKING),
            "embedding_tokens": _quantity_sum(event, TokenType.EMBEDDING),
            "rerank_tokens": _quantity_sum(event, TokenType.RERANK_INPUT, TokenType.RERANK_OUTPUT),
            "duration_ms": duration,
            "time_to_first_token_ms": _number(event.observation.get("time_to_first_token_ms")),
            "time_to_last_token_ms": _number(event.observation.get("time_to_last_token_ms")),
            "retry_count": _integer(event.observation.get("retry_count")),
            "measured": 1 if _is_measured(event) else 0,
            "error_count": 1 if _is_error(event) else 0,
            "rate_limit_count": 1 if _is_rate_limited(event) else 0,
            "flagged_event": 1 if event.data_quality_flags else 0,
            "provider_total_mismatch": 1 if event.event_total_mismatch not in (None, 0) else 0,
            "event_total_mismatch": event.event_total_mismatch,
            "under_attributed_tokens": event.under_attributed_tokens,
            "over_attributed_tokens": event.over_attributed_tokens,
            "quality_flag_count": len(event.data_quality_flags),
            "data_quality_flags": ";".join(event.data_quality_flags),
            "provider_request_id": event.observation.get("provider_request_id"),
            "provider_response_id": event.observation.get("provider_response_id"),
        }


def fact_token_event_rows(events: Iterable[TokenEvent]) -> list[dict[str, Any]]:
    return list(_iter_fact_token_event_rows(_dedupe_events_by_id(events)))


def _iter_fact_token_quantity_rows(events: Iterable[TokenEvent]) -> Iterator[dict[str, Any]]:
    for event in events:
        event_date, _, _ = _date_parts(event.timestamp)
        for quantity in event.quantities:
            yield {
                "event_id": event.event_id,
                "trace_id": event.trace_id,
                "event_date": event_date,
                "service_name": _service_name(event),
                "provider": event.provider or "unknown",
                "api_surface": event.api_surface or "unknown",
                "model": event.model or "unknown",
                "deployment": _deployment(event),
                "token_type": quantity.token_type.value,
                "token_role": quantity.token_role,
                "quantity": quantity.quantity,
                "quantity_in_total": _safe_quantity_in_total(event, quantity),
                "precision_level": quantity.precision_level.value,
                "usage_source": quantity.usage_source.value,
                "additivity": quantity.additivity.value,
                "subtotal_of": quantity.subtotal_of,
                "export_warning": quantity.export_warning,
            }


def fact_token_quantity_rows(events: Iterable[TokenEvent]) -> list[dict[str, Any]]:
    return list(_iter_fact_token_quantity_rows(_dedupe_events_by_id(events)))


def fact_span_rows(trace: Trace | None) -> list[dict[str, Any]]:
    if trace is None:
        return []
    return [
        {
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "parent_span_id": span.parent_span_id,
            "span_type": span.span_type,
            "name": span.name,
            "start_ts": span.start_ts,
            "end_ts": span.end_ts,
            "metadata_json": json.dumps(span.metadata, ensure_ascii=False, sort_keys=True),
        }
        for span in trace.spans
    ]


def _fact_service_daily_rows(events: Iterable[TokenEvent]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    aggregate_fields = (
        "event_contributing_tokens",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "measured",
        "error_count",
        "rate_limit_count",
        "flagged_event",
        "provider_total_mismatch",
        "event_total_mismatch",
        "under_attributed_tokens",
        "over_attributed_tokens",
        "retry_count",
    )
    for row in _iter_fact_token_event_rows(events):
        key = (
            row["event_date"],
            row["service_name"],
            row["tenant"],
            row["cloud_provider"],
            row["region"],
            row["workflow"],
            row["environment"],
            row["provider"],
            row["api_surface"],
            row["model"],
            row["deployment"],
        )
        group = groups.setdefault(
            key,
            {
                "event_count": 0,
                "durations": [],
                **{field: 0 for field in aggregate_fields},
            },
        )
        group["event_count"] += 1
        for field in aggregate_fields:
            group[field] += int(row[field] or 0)
        if row["duration_ms"] not in (None, ""):
            group["durations"].append(float(row["duration_ms"]))

    rows = []
    for key, group in sorted(groups.items()):
        durations = group["durations"]
        rows.append(
            {
                "event_date": key[0],
                "service_name": key[1],
                "tenant": key[2],
                "cloud_provider": key[3],
                "region": key[4],
                "workflow": key[5],
                "environment": key[6],
                "provider": key[7],
                "api_surface": key[8],
                "model": key[9],
                "deployment": key[10],
                "event_count": group["event_count"],
                "contributing_tokens": group["event_contributing_tokens"],
                "input_tokens": group["input_tokens"],
                "output_tokens": group["output_tokens"],
                "cached_input_tokens": group["cached_input_tokens"],
                "cache_creation_input_tokens": group["cache_creation_input_tokens"],
                "measured_count": group["measured"],
                "error_count": group["error_count"],
                "rate_limit_count": group["rate_limit_count"],
                "flagged_event_count": group["flagged_event"],
                "provider_total_mismatch_count": group["provider_total_mismatch"],
                "event_total_mismatch": group["event_total_mismatch"],
                "under_attributed_tokens": group["under_attributed_tokens"],
                "over_attributed_tokens": group["over_attributed_tokens"],
                "retry_count": group["retry_count"],
                "average_duration_ms": _round(sum(durations) / len(durations) if durations else None),
                "p95_duration_ms": _round(_percentile(durations, 0.95)),
            }
        )
    return rows


def fact_service_daily_rows(events: Iterable[TokenEvent]) -> list[dict[str, Any]]:
    return _fact_service_daily_rows(_dedupe_events_by_id(events))


def dim_service_rows(events: Iterable[TokenEvent]) -> list[dict[str, Any]]:
    rows = {}
    for event in events:
        row = {
            "service_name": _service_name(event),
            "tenant": _tenant(event),
            "cloud_provider": _cloud_provider(event),
            "region": _region(event),
            "workflow": event.workflow or "unknown",
            "environment": event.environment or "unknown",
        }
        rows[_safe_key(*row.values())] = {"service_key": _safe_key(*row.values()), **row}
    return [rows[key] for key in sorted(rows)]


def dim_model_rows(events: Iterable[TokenEvent]) -> list[dict[str, Any]]:
    rows = {}
    for event in events:
        row = {
            "provider": event.provider or "unknown",
            "api_surface": event.api_surface or "unknown",
            "model": event.model or "unknown",
            "deployment": _deployment(event),
        }
        rows[_safe_key(*row.values())] = {"model_key": _safe_key(*row.values()), **row}
    return [rows[key] for key in sorted(rows)]


def provider_validation_rows() -> list[dict[str, Any]]:
    matrix = build_provider_validation_matrix(realistic_fixture_records())
    return [
        {
            "status": row["status"],
            "provider": row["provider"],
            "api_surface": row["api_surface"],
            "adapter_name": row["adapter_name"],
            "validation_level": row["validation_level"],
            "real_fixture_count": row["real_fixture_count"],
            "simulated_fixture_count": row["simulated_fixture_count"],
            "fixture_count": row["fixture_count"],
            "gaps": ";".join(row["gaps"]),
            "fixture_names": ";".join(row["fixture_names"]),
        }
        for row in matrix
    ]


def dim_provider_surface_rows(events: Iterable[TokenEvent]) -> list[dict[str, Any]]:
    validation = {(row["provider"], row["api_surface"]): row for row in build_provider_validation_matrix(realistic_fixture_records())}
    pairs = {(event.provider or "unknown", event.api_surface or "unknown") for event in events}
    pairs.update(validation)
    rows = []
    for provider, surface in sorted(pairs):
        matrix_row = validation.get((provider, surface), {})
        rows.append(
            {
                "provider_surface_key": _safe_key(provider, surface),
                "provider": provider,
                "api_surface": surface,
                "adapter_name": matrix_row.get("adapter_name", ""),
                "validation_status": matrix_row.get("status", "unknown"),
                "validation_level": matrix_row.get("validation_level", "unknown"),
                "gaps": ";".join(matrix_row.get("gaps", [])),
            }
        )
    return rows


def dim_token_type_rows() -> list[dict[str, Any]]:
    purposes = {
        TokenType.INPUT: ("Prompt/user/context tokens.", "Volume, model pressure, prompt optimization."),
        TokenType.OUTPUT: ("Generated response tokens.", "Output volume and throughput."),
        TokenType.CACHED_INPUT: ("Provider cache-read input tokens.", "Cache hit and prompt reuse analysis."),
        TokenType.CACHE_CREATION_INPUT: ("Provider cache-write input tokens.", "Cache warmup and reuse setup."),
        TokenType.REASONING: ("Reasoning subtotal when provider exposes it.", "Reasoning behavior, not added twice."),
        TokenType.THINKING: ("Gemini thinking subtotal.", "Thinking behavior, not added twice."),
        TokenType.EMBEDDING: ("Embedding request tokens.", "RAG indexing/search volume."),
        TokenType.RERANK_INPUT: ("Rerank input tokens.", "Rerank workload volume."),
        TokenType.RERANK_OUTPUT: ("Rerank output tokens if reported.", "Rerank result volume."),
        TokenType.AUDIO_INPUT: ("Audio input tokens.", "Multimodal input volume."),
        TokenType.AUDIO_OUTPUT: ("Audio output tokens.", "Multimodal output volume."),
        TokenType.IMAGE_INPUT: ("Image input tokens.", "Multimodal image volume."),
        TokenType.VIDEO_INPUT: ("Video input tokens.", "Multimodal video volume."),
    }
    return [
        {
            "token_type": token_type.value,
            "purpose": purposes[token_type][0],
            "default_dashboard_use": purposes[token_type][1],
        }
        for token_type in TokenType
    ]


def metric_snapshot_rows(
    trace: Trace | None,
    snapshot_ts: str,
    *,
    events: Iterable[TokenEvent] | None = None,
) -> list[dict[str, Any]]:
    """Build KPI snapshots from a Trace or an event-only export source.

    Event-only exports cannot derive span-based latency/RAG/agent summaries, but they still
    have enough source data for coverage and the audit-oriented trust headline. Omitting
    those metrics made iterator exports look healthier by absence precisely when no Trace
    container was available.
    """
    summaries: dict[str, dict[str, Any]] = {}
    if trace is not None:
        summaries.update(
            {
                "coverage_exactness": build_coverage_exactness(trace),
                "latency": build_latency_summary(trace),
                "reliability": build_reliability_summary(trace),
                "observation_contract": build_observation_contract_summary(trace),
                "cache_efficiency": build_cache_summary(trace),
                "rag_efficiency": build_rag_summary(trace),
                "agent_efficiency": build_agent_summary(trace),
            }
        )
        trust = build_trust_report(trace).to_dict()
    elif events is not None:
        summaries["coverage_exactness"] = build_coverage_exactness_from_events(events)
        trust = build_trust_report_from_events(events, collect_anomalies=False).to_dict()
    else:
        return []

    summaries["trust_report"] = {metric: value for metric, value in trust.items() if metric not in {"anomalies", "coverage"}}
    rows = []
    for group, summary in summaries.items():
        for metric, value in summary.items():
            if metric == "rows":
                continue
            rows.append(
                {
                    "snapshot_ts": snapshot_ts,
                    "metric_group": group,
                    "metric": metric,
                    "value": value if not isinstance(value, (dict, list)) else "",
                    "value_json": json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else "",
                }
            )
    return rows


def provider_validation_summary_rows(snapshot_ts: str) -> list[dict[str, Any]]:
    summary = summarize_provider_validation(build_provider_validation_matrix(realistic_fixture_records()))
    return [
        {
            "snapshot_ts": snapshot_ts,
            "metric_group": "provider_validation",
            "metric": metric,
            "value": value,
            "value_json": "",
        }
        for metric, value in sorted(summary.items())
    ]


def data_dictionary_rows() -> list[dict[str, Any]]:
    rows = [
        ("fact_token_events", "event_contributing_tokens", "event", "yes", "Safe event-grain token total. Sum this for total usage."),
        (
            "fact_token_events",
            "input_tokens",
            "event",
            "yes",
            "Raw prompt/input tokens. For OpenAI-style providers this ALREADY includes the cached portion; for Anthropic-style providers it does not. Use fresh_input_tokens for a provider-consistent cache hit rate.",
        ),
        (
            "fact_token_events",
            "fresh_input_tokens",
            "event",
            "yes",
            "Prompt tokens NOT served from cache, consistent across providers. Use with cached_input_tokens for Cache Hit Rate.",
        ),
        ("fact_token_events", "output_tokens", "event", "yes", "Generated output tokens for trend and throughput analysis."),
        ("fact_token_events", "cached_input_tokens", "event", "yes", "Cache-read tokens. Use for cache hit rate and reuse dashboards."),
        ("fact_token_events", "duration_ms", "event", "no", "Latency value. Use average or percentile measures, not SUM."),
        (
            "fact_token_events",
            "measured",
            "event",
            "yes",
            "1 when the event carries ANY operational signal (status/http_status/error code). Divide error/success counts by SUM(measured), not by row count, or an unmeasured event silently counts as a success.",
        ),
        (
            "fact_token_events",
            "error_count",
            "event",
            "yes",
            "1 when the provider call is an error or failed status. Only meaningful for measured=1 rows.",
        ),
        ("fact_token_events", "rate_limit_count", "event", "yes", "1 when the provider call is rate limited."),
        ("fact_token_events", "provider_total_mismatch", "event", "yes", "1 when provider total does not reconcile to quantities."),
        ("fact_token_events", "event_total_mismatch", "event", "yes", "Signed provider-total minus attributed-token delta."),
        ("fact_token_events", "under_attributed_tokens", "event", "yes", "Positive unattributed provider tokens."),
        ("fact_token_events", "over_attributed_tokens", "event", "yes", "Positive over-attributed tracker tokens."),
        (
            "fact_token_quantities",
            "quantity_in_total",
            "quantity",
            "yes",
            "Safe quantity-grain token total. Do not sum together with event totals.",
        ),
        ("fact_token_quantities", "quantity", "quantity", "no", "Raw provider quantity. Useful for inspection, not global totals."),
        ("fact_service_daily", "contributing_tokens", "service-date", "yes", "Pre-aggregated service/provider daily token trend."),
        ("metric_snapshots", "value", "snapshot-metric", "depends", "Derived operational metric snapshot for KPI cards."),
        ("provider_validation_matrix", "status", "provider-surface", "no", "Validation readiness status for each adapter surface."),
    ]
    return [
        {
            "table": table,
            "column": column,
            "grain": grain,
            "summable": summable,
            "purpose": purpose,
        }
        for table, column, grain, summable, purpose in rows
    ]


def dax_measures() -> str:
    return """Total Contributing Tokens = SUM(fact_token_events[event_contributing_tokens])
Input Tokens = SUM(fact_token_events[input_tokens])
Fresh Input Tokens = SUM(fact_token_events[fresh_input_tokens])
Output Tokens = SUM(fact_token_events[output_tokens])
Cached Input Tokens = SUM(fact_token_events[cached_input_tokens])
Cache Creation Tokens = SUM(fact_token_events[cache_creation_input_tokens])
Cache Hit Rate = DIVIDE([Cached Input Tokens], [Fresh Input Tokens] + [Cached Input Tokens])
Total Events = COUNTROWS(fact_token_events)
Measured Events = SUM(fact_token_events[measured])
Successful Events = CALCULATE([Total Events], fact_token_events[error_count] = 0, fact_token_events[measured] = 1)
Success Rate = DIVIDE([Successful Events], [Measured Events])
Error Rate = DIVIDE(SUM(fact_token_events[error_count]), [Measured Events])
Rate Limited Events = SUM(fact_token_events[rate_limit_count])
Retry Count = SUM(fact_token_events[retry_count])
Flagged Events = SUM(fact_token_events[flagged_event])
Provider Mismatch Events = SUM(fact_token_events[provider_total_mismatch])
Provider Total Mismatch Tokens = SUM(fact_token_events[event_total_mismatch])
Under Attributed Tokens = SUM(fact_token_events[under_attributed_tokens])
Over Attributed Tokens = SUM(fact_token_events[over_attributed_tokens])
Average Duration MS = AVERAGE(fact_token_events[duration_ms])
P95 Duration MS = PERCENTILEX.INC(FILTER(fact_token_events, NOT ISBLANK(fact_token_events[duration_ms])), fact_token_events[duration_ms], 0.95)
Average TTFT MS = AVERAGE(fact_token_events[time_to_first_token_ms])
Successful Contributing Tokens = CALCULATE([Total Contributing Tokens], fact_token_events[error_count] = 0)
Tokens Per Successful Event = DIVIDE([Successful Contributing Tokens], [Successful Events])
"""


def readme_text(dataset_name: str) -> str:
    return f"""# {dataset_name} Power BI Dataset

This folder is a Power BI import base for AI token tracking operations.

Import the CSV files as separate tables, then create relationships on these natural keys:

- fact_token_events[provider] + fact_token_events[api_surface] to dim_provider_surface
- fact_token_events[provider] + fact_token_events[api_surface] + fact_token_events[model] + fact_token_events[deployment] to dim_model
- fact_token_quantities[event_id] to fact_token_events[event_id]
- fact_spans[span_id] to fact_token_events[span_id]

Use `measures.dax` to create the first dashboard measures.

Important counting rules:

- Sum `fact_token_events[event_contributing_tokens]` for event-grain totals.
- Sum `fact_token_quantities[quantity_in_total]` for quantity-grain totals.
- Do not sum `provider_total_tokens` or raw `quantity` as business totals.
- Do not mix event-grain totals and quantity-grain totals in the same total.
- Cache, reasoning, and thinking fields are diagnostic dimensions/metrics; they are not added
  twice into total contributing tokens.

Recommended first report pages:

1. Executive usage: tokens, events, success rate, p95 latency, flagged events.
2. Service attribution: service, tenant, cloud, region, provider, model, deployment.
3. Reliability: errors, rate limits, retries, provider-total mismatches.
4. Cache efficiency: cache-read tokens and hit rate by provider/model/service.
5. RAG and agent metrics: use `metric_snapshots` plus span facts when spans are available.
6. Trust readiness: `provider_validation_matrix` and provider validation summary metrics.

No pricing or cost fields are included.
"""


def _write_csv(path: str, headers: list[str], rows: Iterable[dict[str, Any]]) -> int:
    row_count = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: "" if row.get(header) is None else row.get(header) for header in headers})
            row_count += 1
    return row_count


def _write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _write_json(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _manifest(
    *,
    dataset_name: str,
    snapshot_ts: str,
    table_specs: dict[str, tuple[str, int, str, str]],
) -> dict[str, Any]:
    return {
        "dataset_name": dataset_name,
        "generated_at": snapshot_ts,
        "target": "power_bi_import_folder",
        "also_works_with": ["excel_csv_import"],
        "source_of_truth": {
            "event_total": "fact_token_events.event_contributing_tokens",
            "quantity_total": "fact_token_quantities.quantity_in_total",
            "never_sum": ["fact_token_events.provider_total_tokens", "fact_token_quantities.quantity"],
        },
        "refresh_strategy": {
            "mode": "replace_folder_then_refresh_power_bi",
            "recommended_schedule": "hourly_or_daily_depending_on_volume",
            "stable_schema": True,
            "event_snapshot": "temporary_sqlite_event_id_deduplicated",
        },
        "tables": {
            name: {
                "file": filename,
                "rows": row_count,
                "grain": grain,
                "primary_key": primary_key,
            }
            for name, (filename, row_count, grain, primary_key) in sorted(table_specs.items())
        },
        "relationships": [
            "fact_token_quantities.event_id -> fact_token_events.event_id",
            "fact_spans.span_id -> fact_token_events.span_id",
            "fact_token_events.provider/api_surface -> dim_provider_surface.provider/api_surface",
            "fact_token_events.provider/api_surface/model/deployment -> dim_model.provider/api_surface/model/deployment",
        ],
        "files": {
            "measures": "measures.dax",
            "readme": "README.md",
            "data_dictionary": "data_dictionary.csv",
        },
        "warnings": [
            "Power BI measures must not add event-grain and quantity-grain totals together.",
            "Provider validation warnings are expected until real and stream fixtures are captured.",
            "No pricing fields are exported.",
        ],
    }


def export_powerbi_events(
    events: Iterable[TokenEvent],
    out_dir: str,
    *,
    dataset_name: str = "ai_token_tracker",
    trace: Trace | None = None,
    generated_at: str | None = None,
) -> dict[str, str]:
    """Export event data as a Power BI import folder and return created paths."""
    os.makedirs(out_dir, exist_ok=True)
    snapshot_ts = generated_at or _now_utc()
    event_snapshot = _DiskEventSnapshot(events)
    try:
        metric_rows = metric_snapshot_rows(trace, snapshot_ts, events=event_snapshot)
        metric_rows.extend(provider_validation_summary_rows(snapshot_ts))

        table_specs: dict[str, tuple[str, Iterable[dict[str, Any]], list[str], str, str]] = {
            "fact_token_events": (
                "fact_token_events.csv",
                _iter_fact_token_event_rows(event_snapshot),
                FACT_TOKEN_EVENT_HEADERS,
                "event",
                "event_id",
            ),
            "fact_token_quantities": (
                "fact_token_quantities.csv",
                _iter_fact_token_quantity_rows(event_snapshot),
                FACT_TOKEN_QUANTITY_HEADERS,
                "quantity",
                "event_id + token_type + token_role",
            ),
            "fact_spans": (
                "fact_spans.csv",
                fact_span_rows(trace),
                FACT_SPAN_HEADERS,
                "span",
                "span_id",
            ),
            "fact_service_daily": (
                "fact_service_daily.csv",
                _fact_service_daily_rows(event_snapshot),
                FACT_SERVICE_DAILY_HEADERS,
                "service/provider/model/date",
                "event_date + service + provider + model",
            ),
            "dim_service": (
                "dim_service.csv",
                dim_service_rows(event_snapshot),
                DIM_SERVICE_HEADERS,
                "service",
                "service_key",
            ),
            "dim_model": (
                "dim_model.csv",
                dim_model_rows(event_snapshot),
                DIM_MODEL_HEADERS,
                "model",
                "model_key",
            ),
            "dim_provider_surface": (
                "dim_provider_surface.csv",
                dim_provider_surface_rows(event_snapshot),
                DIM_PROVIDER_SURFACE_HEADERS,
                "provider_surface",
                "provider_surface_key",
            ),
            "dim_token_type": (
                "dim_token_type.csv",
                dim_token_type_rows(),
                DIM_TOKEN_TYPE_HEADERS,
                "token_type",
                "token_type",
            ),
            "metric_snapshots": (
                "metric_snapshots.csv",
                metric_rows,
                METRIC_SNAPSHOT_HEADERS,
                "snapshot_metric",
                "snapshot_ts + metric_group + metric",
            ),
            "provider_validation_matrix": (
                "provider_validation_matrix.csv",
                provider_validation_rows(),
                PROVIDER_VALIDATION_HEADERS,
                "provider_surface",
                "provider + api_surface",
            ),
            "data_dictionary": (
                "data_dictionary.csv",
                data_dictionary_rows(),
                DATA_DICTIONARY_HEADERS,
                "field",
                "table + column",
            ),
        }

        paths = {}
        manifest_specs: dict[str, tuple[str, int, str, str]] = {}
        for table_name, (filename, rows, headers, grain, primary_key) in table_specs.items():
            path = os.path.join(out_dir, filename)
            row_count = _write_csv(path, headers, rows)
            paths[table_name] = path
            manifest_specs[table_name] = (filename, row_count, grain, primary_key)
    finally:
        event_snapshot.close()

    measures_path = os.path.join(out_dir, "measures.dax")
    readme_path = os.path.join(out_dir, "README.md")
    manifest_path = os.path.join(out_dir, "manifest.json")
    _write_text(measures_path, dax_measures())
    _write_text(readme_path, readme_text(dataset_name))
    _write_json(
        manifest_path,
        _manifest(
            dataset_name=dataset_name,
            snapshot_ts=snapshot_ts,
            table_specs=manifest_specs,
        ),
    )
    paths.update({"measures": measures_path, "readme": readme_path, "manifest": manifest_path})
    return paths


def export_powerbi(
    trace: Trace,
    out_dir: str,
    *,
    dataset_name: str = "ai_token_tracker",
    generated_at: str | None = None,
) -> dict[str, str]:
    """Export a full Trace as a Power BI import folder."""
    return export_powerbi_events(
        trace.events,
        out_dir,
        dataset_name=dataset_name,
        trace=trace,
        generated_at=generated_at,
    )


__all__ = [
    "export_powerbi",
    "export_powerbi_events",
    "fact_service_daily_rows",
    "fact_token_event_rows",
    "fact_token_quantity_rows",
]
