"""Live dashboard totals must use the canonical effective-event projection."""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.export.live_dashboard import (  # noqa: E402
    _PAGE,
    ExclusiveThreadingHTTPServer,
    _is_loopback,
    _request_options,
    aggregate,
    make_handler,
)
from tracker.models.enums import Additivity, DataQualityFlag, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()


def quantity(token_type: TokenType, value: int, precision: PrecisionLevel, source: UsageSource) -> TokenQuantity:
    return TokenQuantity(token_type, value, precision, source, Additivity.TOTAL_CONTRIBUTING)


partial = TokenEvent(
    event_id="dashboard-partial",
    request_correlation_id="dashboard-request",
    trace_id="dashboard-trace",
    span_id="dashboard-span",
    provider="azure_openai",
    model="gpt-5-mini",
    api_surface="responses",
    quantities=[quantity(TokenType.OUTPUT, 40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER)],
    data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
    timestamp="2026-07-21T09:00:00Z",
    observation={"authoritative": True, "status": "incomplete", "service_name": "demo-service"},
)
final = TokenEvent(
    event_id="dashboard-final",
    request_correlation_id="dashboard-request",
    trace_id="dashboard-trace",
    span_id="dashboard-span",
    provider="azure_openai",
    model="gpt-5-mini",
    api_surface="responses",
    quantities=[
        quantity(TokenType.INPUT, 100, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL),
        quantity(TokenType.OUTPUT, 60, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL),
    ],
    provider_total_tokens=160,
    timestamp="2026-07-21T09:00:01Z",
    observation={
        "authoritative": True,
        "status": "complete",
        "service_name": "demo-service",
        "duration_ms": 125,
    },
)
failed = TokenEvent(
    event_id="dashboard-failed",
    request_correlation_id="dashboard-failed-request",
    trace_id="dashboard-trace",
    span_id="dashboard-failed-span",
    provider="azure_openai",
    model="gpt-5-mini",
    api_surface="responses",
    quantities=[],
    data_quality_flags=["auth_failure"],
    timestamp="2026-07-21T09:00:02Z",
    observation={"authoritative": False, "status": "failed", "service_name": "demo-service"},
)
previous_day = TokenEvent(
    event_id="dashboard-previous",
    request_correlation_id="dashboard-previous-request",
    trace_id="dashboard-trace",
    span_id="dashboard-previous-span",
    provider="anthropic",
    model="claude-test",
    api_surface="messages",
    quantities=[quantity(TokenType.INPUT, 50, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
    timestamp="2026-07-20T22:30:00Z",
    observation={"authoritative": True, "status": "complete", "service_name": "previous-service"},
)
local_today = TokenEvent(
    event_id="dashboard-local-today",
    request_correlation_id="dashboard-local-today-request",
    trace_id="dashboard-trace",
    span_id="dashboard-local-today-span",
    provider="azure_openai",
    model="gpt-5-mini",
    api_surface="responses",
    quantities=[quantity(TokenType.INPUT, 25, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
    timestamp="2026-07-20T23:30:00Z",
    observation={"authoritative": True, "status": "complete", "service_name": "midnight-service"},
)
undated = TokenEvent(
    event_id="dashboard-undated",
    request_correlation_id="dashboard-undated-request",
    trace_id="dashboard-trace",
    span_id="dashboard-undated-span",
    provider="openai",
    model="gpt-test",
    api_surface="responses",
    quantities=[quantity(TokenType.INPUT, 30, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
    observation={"authoritative": True, "status": "complete", "service_name": "undated-service"},
)

root = Path(os.path.abspath(f".test_live_dashboard_{uuid.uuid4().hex}"))
root.mkdir(parents=True, exist_ok=True)
try:
    store = root / "events.jsonl"
    FileRepository(str(store)).append_many([partial, final, failed, previous_day, local_today, undated])
    now = dt.datetime(2026, 7, 21, 12, tzinfo=dt.UTC)
    data = aggregate(str(store), window="today", timezone_offset_minutes=-60, now=now)

    check(data["events"] == 4, "raw source-event count is scoped to the selected local day")
    check(data["effective_events"] == 2, "today includes the authoritative final and the post-midnight event")
    check(data["superseded_events"] == 1, "the correlated partial is visibly superseded")
    check(data["excluded_events"] == 1, "the non-authoritative failure is visibly excluded")
    check(data["undated_events"] == 1, "undated events are explicit and excluded from time windows")
    check(data["total_tokens"] == 185, "today never sums partial plus final and respects local midnight")
    check(data["quality"]["status"] == "clean", "clean period receives a clean token-integrity status")
    check(data["quality"]["coverage_status"] == "partial", "incomplete latency/provider coverage is separate")
    check(data["quality"]["known_exact_token_share"] == 1.0, "period exact-token share is explicit")
    check(data["quality"]["provider_total_coverage"] == 0.5, "provider-total coverage is period scoped")
    check(data["quality"]["latency_coverage"] == 0.5, "latency coverage is request scoped")
    check(data["quality"]["instrumented_latency_coverage"] == 0.5, "instrumented latency is request scoped")
    check(data["quality"]["latency_applicability"] == 1.0, "all live dashboard requests are latency applicable")
    check(
        data["headline"]["floor_tokens"] == 185
        and data["headline"]["estimate_tokens"] == 185
        and data["headline"]["ceiling_tokens"] == 185,
        "dashboard exposes an exact floor/estimate/ceiling band",
    )
    check(
        data["by_service"]
        == [
            {"name": "demo-service", "events": 1, "tokens": 160},
            {"name": "midnight-service", "events": 1, "tokens": 25},
        ],
        "service rollup is recomputed for the selected period",
    )
    check(data["flags"].get("auth_failure") == 1, "active non-authoritative failures remain observable")
    check(data["period"]["window"] == "today" and data["period"]["bucket"] == "hour", "today uses hourly buckets")
    check(len(data["timeline"]) == 24, "a selected calendar day returns 24 stable hourly slots")
    check(sum(row["tokens"] for row in data["timeline"]) == 185, "hourly contributions reconcile to the period total")
    check(data["timeline"][0]["tokens"] == 25, "UTC+1 midnight attribution lands in the correct local hour")

    previous = aggregate(
        str(store),
        window="date",
        selected_date="2026-07-20",
        timezone_offset_minutes=-60,
        now=now,
    )
    check(previous["total_tokens"] == 50, "custom date isolates the previous local calendar day")
    check(sum(row["tokens"] for row in previous["timeline"]) == 50, "custom-date hourly series reconciles")

    rolling = aggregate(str(store), window="24h", timezone_offset_minutes=-60, now=now)
    check(rolling["total_tokens"] == 235, "rolling 24 hours includes both sides of local midnight")
    check(rolling["period"]["bucket"] == "hour", "rolling 24 hours remains an hourly contribution view")

    all_time = aggregate(str(store), window="all", timezone_offset_minutes=-60, now=now)
    check(all_time["total_tokens"] == 265, "all-time option retains dated and undated authoritative usage")
    check(all_time["effective_events"] == 4, "all-time option keeps every authoritative effective event")
    check(sum(row["tokens"] for row in all_time["timeline"]) == 265, "all-time daily series includes undated usage")

    default_data = aggregate(str(store), timezone_offset_minutes=-60, now=now)
    check(default_data["period"]["window"] == "7d", "dashboard aggregation defaults to a daily seven-day view")
    check(default_data["period"]["bucket"] == "day", "default contribution is grouped by date")
    check(len(default_data["timeline"]) == 7, "default dashboard exposes seven stable daily buckets")

    options = _request_options("/data?window=date&date=2026-07-20&tz_offset=-60")
    check(options["selected_date"] == "2026-07-20", "HTTP query parser preserves the requested date")
    default_options = _request_options("/data?tz_offset=-60")
    check(default_options["window"] == "7d", "HTTP dashboard API defaults to daily consumption")
    try:
        _request_options("/data?window=quarter&tz_offset=-60")
    except ValueError:
        invalid_rejected = True
    else:
        invalid_rejected = False
    check(invalid_rejected, "unknown dashboard windows fail closed")
    check("tr.innerHTML" not in _PAGE, "provider-controlled labels are inserted as text, not HTML")
    check('data-window="today"' in _PAGE and 'data-window="all"' in _PAGE, "UI exposes today and all-time options")
    check('data-window="7d" class="active"' in _PAGE, "UI opens on the seven-day daily view")
    check(
        'type="date"' in _PAGE and "Daily token consumption" in _PAGE and 'id="provider_coverage"' in _PAGE,
        "UI exposes date selection, daily consumption, and validation coverage",
    )
    check("exact share of known tokens" in _PAGE, "exact-share label names its known-token denominator")

    collision_partial = TokenEvent.from_dict(final.to_dict())
    collision_partial.event_id = "dashboard-collision-first-final"
    collision_partial.request_correlation_id = "dashboard-collision-request"
    collision_partial.request_hash = "first-request-hash"
    collision_partial.timestamp = "2026-07-21T09:00:00Z"
    collision_final = TokenEvent.from_dict(final.to_dict())
    collision_final.event_id = "dashboard-collision-latest-final"
    collision_final.request_correlation_id = "dashboard-collision-request"
    collision_final.request_hash = "different-request-hash"
    collision_final.timestamp = "2026-07-21T09:00:01Z"
    collision_store = root / "collision-events.jsonl"
    FileRepository(str(collision_store)).append_many([collision_partial, collision_final])
    collision_data = aggregate(str(collision_store), window="today", timezone_offset_minutes=0, now=now)
    check(
        collision_data["quality"]["correlation_risk_event_count"] == 1,
        "a correlation risk remains counted after the affected row is superseded",
    )
    check(collision_data["quality"]["status"] == "blocked", "a superseded correlation risk remains blocking")

    cardinality_store = root / "cardinality-events.jsonl"
    cardinality_events = []
    for index in range(30):
        event = TokenEvent.from_dict(local_today.to_dict())
        event.event_id = f"cardinality-{index}"
        event.request_correlation_id = f"cardinality-request-{index}"
        event.provider = f"provider-{index:02d}"
        cardinality_events.append(event)
    FileRepository(str(cardinality_store)).append_many(cardinality_events)
    cardinality_data = aggregate(str(cardinality_store), window="today", timezone_offset_minutes=-60, now=now)
    check(
        len(cardinality_data["by_provider"]) == 25
        and cardinality_data["by_provider"][-1]["name"] == "Other (6)"
        and sum(row["tokens"] for row in cardinality_data["by_provider"]) == cardinality_data["total_tokens"],
        "high-cardinality dashboard groups collapse to Top 24 plus a reconciled Other bucket",
    )

    local_import = TokenEvent(
        event_id="dashboard-local-import",
        request_correlation_id="dashboard-local-import-request",
        trace_id="dashboard-local-import-trace",
        span_id="dashboard-local-import-span",
        provider="anthropic",
        model="claude-test",
        api_surface="messages",
        quantities=[quantity(TokenType.INPUT, 20, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
        data_quality_flags=[DataQualityFlag.CLAUDE_CODE_LOCAL_USAGE.value],
        timestamp="2026-07-21T10:00:00Z",
        observation={"authoritative": True, "status": "complete"},
    )
    latency_store = root / "latency-events.jsonl"
    FileRepository(str(latency_store)).append_many([final, local_import])
    latency_data = aggregate(str(latency_store), window="today", timezone_offset_minutes=0, now=now)
    check(latency_data["quality"]["latency_coverage"] == 0.5, "overall live latency exposes missing local data")
    check(
        latency_data["quality"]["instrumented_latency_coverage"] == 1.0,
        "local log imports are excluded only from the instrumentable denominator",
    )
    check(latency_data["quality"]["latency_applicability"] == 0.5, "live UI reports latency applicability")
    check(_is_loopback("127.0.0.1") and _is_loopback("::1"), "loopback dashboard binds are accepted")
    check(not _is_loopback("0.0.0.0"), "unauthenticated non-loopback dashboard binds are rejected")

    first_server = ExclusiveThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(store)))
    try:
        bound_port = first_server.server_address[1]
        try:
            duplicate_server = ExclusiveThreadingHTTPServer(("127.0.0.1", bound_port), make_handler(str(store)))
        except OSError:
            duplicate_rejected = True
        else:
            duplicate_rejected = False
            duplicate_server.server_close()
        check(duplicate_rejected, "a second dashboard process cannot serve stale data on the same port")
    finally:
        first_server.server_close()
finally:
    shutil.rmtree(root, ignore_errors=True)

sys.exit(check.report("RESULT test_live_dashboard"))
