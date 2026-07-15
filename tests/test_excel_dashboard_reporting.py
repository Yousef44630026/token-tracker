"""Optional Excel dashboard preserves accounting grain and request-level latency."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

if importlib.util.find_spec("pandas") is None:
    print("[SKIP] test_excel_dashboard_reporting: install .[reporting] to run")
    raise SystemExit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, DataQualityFlag, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.observability.observation import Observation  # noqa: E402
from tracker.reporting.excel_dashboard import (  # noqa: E402
    build_data_frame,
    build_summary_frames,
    load_jsonl_events,
    load_prices,
    write_dashboard,
)

check = make_checker()
root = Path(f".test_excel_dashboard_{uuid.uuid4().hex}").resolve()
data_dir = root / "data"
data_dir.mkdir(parents=True)


def quantity(token_type, value, additivity=Additivity.TOTAL_CONTRIBUTING, *, subtotal_of=None):
    return TokenQuantity(
        token_type=token_type,
        quantity=value,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=additivity,
        subtotal_of=subtotal_of,
    )


partial = TokenEvent(
    event_id="partial",
    request_correlation_id="request-1",
    trace_id="trace-1",
    span_id="span-1",
    workflow="support",
    provider="azure_openai",
    model="model-a",
    api_surface="responses",
    quantities=[
        TokenQuantity(
            token_type=TokenType.OUTPUT,
            quantity=40,
            precision_level=PrecisionLevel.ESTIMATE,
            usage_source=UsageSource.PARTIAL_STREAM_TOKENIZER,
            additivity=Additivity.TOTAL_CONTRIBUTING,
        )
    ],
    data_quality_flags=[DataQualityFlag.PARTIAL_STREAM_ESTIMATE.value],
    timestamp="2026-07-01T09:59:00Z",
    observation=Observation(authoritative=True, status="incomplete", duration_ms=50),
)
final_one = TokenEvent(
    event_id="final-1",
    request_correlation_id="request-1",
    trace_id="trace-1",
    span_id="span-1",
    workflow="support",
    provider="azure_openai",
    model="model-a",
    api_surface="responses",
    quantities=[
        quantity(TokenType.INPUT, 100),
        quantity(TokenType.CACHED_INPUT, 20, Additivity.SUBTOTAL_OF, subtotal_of="input"),
        quantity(TokenType.OUTPUT, 50),
    ],
    provider_total_tokens=150,
    timestamp="2026-07-01T10:00:00Z",
    observation=Observation(authoritative=True, status="success", duration_ms=100, time_to_first_token_ms=20),
)
final_two = TokenEvent(
    event_id="final-2",
    request_correlation_id="request-2",
    trace_id="trace-2",
    span_id="span-2",
    workflow="summarization",
    provider="azure_openai",
    model="model-b",
    api_surface="responses",
    quantities=[quantity(TokenType.INPUT, 200), quantity(TokenType.OUTPUT, 100)],
    provider_total_tokens=300,
    timestamp="2026-07-02T11:00:00Z",
    observation=Observation(authoritative=True, status="success", duration_ms=300),
)

events_path = data_dir / "events.jsonl"
with events_path.open("w", encoding="utf-8") as handle:
    for event in (partial, final_one, final_two):
        handle.write(json.dumps(event.to_dict()) + "\n")
    handle.write("{not-json\n")
    handle.write(json.dumps({"not": "an event"}) + "\n")

prices_path = root / "prices.csv"
pd.DataFrame(
    [
        ("azure_openai", "model-a", "input", 1.0, "USD", "2026-01-01", ""),
        ("azure_openai", "model-a", "cached_input", 0.2, "USD", "2026-01-01", ""),
        ("azure_openai", "model-a", "output", 2.0, "USD", "2026-01-01", ""),
        ("azure_openai", "model-b", "input", 0.5, "USD", "2026-01-01", ""),
        ("azure_openai", "model-b", "output", 1.5, "USD", "2026-01-01", ""),
    ],
    columns=("provider", "model", "token_type", "price_per_million_tokens", "currency", "effective_from", "effective_to"),
).to_csv(prices_path, index=False)

events, report = load_jsonl_events(data_dir)
check(report.valid_events == 3, "three valid events survive JSONL validation")
check(report.malformed_lines == 1, "malformed JSONL is logged and skipped")
check(report.schema_invalid_lines == 1, "schema-invalid JSON object is logged and skipped")
check(next(item.event for item in events if item.event.event_id == "partial").superseded, "final usage supersedes partial stream")

data = build_data_frame(events, load_prices(prices_path))
summaries = build_summary_frames(data)
check(data["event_contributing_tokens_once"].sum() == 450, "event-grain safe total is never repeated at quantity grain")
check(data["request_count_once"].sum() == 2, "request count appears exactly once per correlation id")
check(data.loc[data["request_count_once"] == 1, "request_latency_ms"].mean() == 200, "latency average uses one row per request")

model_a = data[(data["model"] == "model-a") & ~data["event_superseded"]]
check(model_a.loc[model_a["token_type"] == "input", "billing_tokens"].iloc[0] == 80, "input billing removes cached subtotal")
check(model_a.loc[model_a["token_type"] == "cached_input", "billing_tokens"].iloc[0] == 20, "cache subtotal has its own billing rate")
check(abs(float(data["derived_cost"].sum()) - 0.000434) < 1e-12, "derived cost uses allocated tokens without double counting")
check(abs(float(summaries["pricing_coverage"]) - 1.0) < 1e-12, "pricing coverage is complete")

output = root / "dashboard.xlsx"
write_dashboard(data, summaries, report, output)
workbook = load_workbook(output, data_only=False)
check(workbook.sheetnames == ["Data", "Coûts", "Tokens & Latence", "Use cases"], "workbook has exactly the four requested sheets")
check("DataTable" in workbook["Data"].tables, "Data is an Excel table suitable for pivots")
check(len(workbook["Coûts"]._charts) == 2, "cost sheet has two native charts")
check(len(workbook["Tokens & Latence"]._charts) == 2, "tokens and latency sheet has two native charts")
check(len(workbook["Use cases"]._charts) == 1, "use-case sheet has a native pie chart")
check(not any("derived_cost" in event.to_dict() for event in (partial, final_one, final_two)), "derived cost never enters stored events")

workbook.close()
demo_output = os.environ.get("TRACKER_DASHBOARD_DEMO_OUTPUT")
if demo_output:
    Path(demo_output).resolve().parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output, demo_output)
shutil.rmtree(root, ignore_errors=True)
sys.exit(check.report("RESULT test_excel_dashboard_reporting"))
