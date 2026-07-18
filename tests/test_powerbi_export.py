"""Power BI integration base: CSV star schema, manifest, and DAX measures.

Run: python tests/test_powerbi_export.py
"""

import csv
import glob
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.export.powerbi_exporter import export_powerbi, export_powerbi_events  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.span import Span  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.proxy.cli import main as proxy_main  # noqa: E402
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def q(token_type, quantity, additivity=Additivity.TOTAL_CONTRIBUTING, *, subtotal_of=None, metadata=None):
    return TokenQuantity(
        token_type=token_type,
        quantity=quantity,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=additivity,
        subtotal_of=subtotal_of,
        metadata=metadata or {},
    )


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


trace = Trace(trace_id="trace-powerbi", workflow="support", environment="prod")
trace.add_span(Span(span_id="llm-1", trace_id=trace.trace_id, span_type="llm", name="answer"))
trace.add_span(
    Span(
        span_id="vector-1",
        trace_id=trace.trace_id,
        span_type="vector_search",
        metadata={"num_results": 4, "latency_ms": 11},
    )
)

trace.add_event(
    TokenEvent(
        event_id="evt-azure",
        request_correlation_id="req-azure",
        trace_id=trace.trace_id,
        span_id="llm-1",
        workflow="support",
        environment="prod",
        provider="azure_openai",
        model="gpt-prod",
        api_surface="responses",
        quantities=[
            q(TokenType.INPUT, 1000, metadata={"azure_deployment": "prod-gpt"}),
            q(TokenType.CACHED_INPUT, 200, Additivity.SUBTOTAL_OF, subtotal_of="input", metadata={"azure_deployment": "prod-gpt"}),
            q(TokenType.OUTPUT, 300, metadata={"azure_deployment": "prod-gpt"}),
            q(TokenType.REASONING, 50, Additivity.SUBTOTAL_OF, subtotal_of="output", metadata={"azure_deployment": "prod-gpt"}),
        ],
        provider_total_tokens=1300,
        timestamp="2026-07-02T10:15:00Z",
        observation={
            "authoritative": True,
            "status": "complete",
            "duration_ms": 1000,
            "time_to_first_token_ms": 120,
            "provider_request_id": "az-req",
            "provider_response_id": "az-resp",
            "service_name": "support-api",
            "tenant_id": "tenant-a",
            "cloud_provider": "azure",
            "region": "francecentral",
        },
    )
)
trace.add_event(
    TokenEvent(
        event_id="evt-openai",
        request_correlation_id="req-openai",
        trace_id=trace.trace_id,
        span_id="llm-1",
        workflow="support",
        environment="prod",
        provider="openai",
        model="gpt-agent",
        api_surface="responses",
        quantities=[q(TokenType.INPUT, 100), q(TokenType.OUTPUT, 50)],
        provider_total_tokens=160,
        timestamp="2026-07-02T11:30:00Z",
        observation={
            "authoritative": True,
            "status": "complete",
            "duration_ms": 500,
            "service_name": "support-api",
            "tenant_id": "tenant-a",
            "region": "us-east",
        },
    )
)
trace.add_event(
    TokenEvent(
        event_id="evt-error",
        request_correlation_id="req-error",
        trace_id=trace.trace_id,
        span_id="vector-1",
        workflow="support",
        environment="prod",
        provider="bedrock",
        model="nova",
        api_surface="converse",
        quantities=[],
        data_quality_flags=["raw_usage_missing"],
        timestamp="2026-07-02T12:00:00Z",
        observation={
            "status": "rate_limited",
            "http_status": 429,
            "provider_error_code": "rate_limit",
            "retry_count": 2,
            "authoritative": False,
            "service_name": "support-api",
            "tenant_id": "tenant-a",
            "cloud_provider": "aws",
            "region": "eu-west-3",
        },
    )
)

out_dir = os.path.join(os.getcwd(), ".test_powerbi_out")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir, exist_ok=True)
paths = export_powerbi(
    trace,
    out_dir,
    dataset_name="tracker_ops",
    generated_at="2026-07-02T12:00:00+00:00",
)

for key in (
    "fact_token_events",
    "fact_token_quantities",
    "fact_spans",
    "fact_service_daily",
    "dim_service",
    "dim_model",
    "dim_provider_surface",
    "dim_token_type",
    "metric_snapshots",
    "provider_validation_matrix",
    "data_dictionary",
    "manifest",
    "measures",
    "readme",
):
    check(key in paths and os.path.exists(paths[key]), f"Power BI export writes {key}")

event_rows = read_csv(paths["fact_token_events"])
check(len(event_rows) == 3, "fact_token_events has one row per event")
azure_row = next(row for row in event_rows if row["event_id"] == "evt-azure")
check(azure_row["event_contributing_tokens"] == "1300", "event fact exposes safe contributing total")
check(azure_row["cached_input_tokens"] == "200", "event fact exposes cache-read tokens for dashboards")
check(azure_row["deployment"] == "prod-gpt", "event fact carries deployment dimension")
openai_row = next(row for row in event_rows if row["event_id"] == "evt-openai")
check(openai_row["provider_total_mismatch"] == "1", "event fact exposes provider mismatch flag")
check(openai_row["event_total_mismatch"] == "10", "event fact exposes signed mismatch magnitude")
check(openai_row["under_attributed_tokens"] == "10", "event fact exposes under-attributed magnitude")
check(openai_row["over_attributed_tokens"] == "0", "event fact exposes over-attributed magnitude")
error_row = next(row for row in event_rows if row["event_id"] == "evt-error")
check(error_row["event_contributing_tokens"] == "0", "non-authoritative error contributes 0")
check(error_row["error_count"] == "1" and error_row["rate_limit_count"] == "1", "event fact tracks reliability counters")

quantity_rows = read_csv(paths["fact_token_quantities"])
quantity_total = sum(int(row["quantity_in_total"] or 0) for row in quantity_rows)
event_total = sum(int(row["event_contributing_tokens"] or 0) for row in event_rows)
check(quantity_total == event_total == 1450, "quantity and event safe totals agree")

daily_rows = read_csv(paths["fact_service_daily"])
check(
    any(row["provider"] == "azure_openai" and row["contributing_tokens"] == "1300" for row in daily_rows),
    "daily fact has Azure service trend row",
)
openai_daily = next(row for row in daily_rows if row["provider"] == "openai")
check(openai_daily["provider_total_mismatch_count"] == "1", "daily fact aggregates mismatch count")
check(openai_daily["event_total_mismatch"] == "10", "daily fact aggregates signed mismatch magnitude")
check(openai_daily["under_attributed_tokens"] == "10", "daily fact aggregates under-attributed tokens")
check(openai_daily["over_attributed_tokens"] == "0", "daily fact aggregates over-attributed tokens")

metric_rows = read_csv(paths["metric_snapshots"])
check(
    any(row["metric_group"] == "provider_validation" and row["metric"] == "fail_count" for row in metric_rows),
    "metric snapshots include provider validation",
)
check(any(row["metric_group"] == "rag_efficiency" for row in metric_rows), "metric snapshots include RAG metrics when trace spans exist")

with open(paths["manifest"], encoding="utf-8") as handle:
    manifest = json.load(handle)
check(manifest["target"] == "power_bi_import_folder", "manifest declares Power BI import target")
check(manifest["tables"]["fact_token_events"]["rows"] == 3, "manifest records fact row counts")
check(
    manifest["refresh_strategy"]["event_snapshot"] == "temporary_sqlite_event_id_deduplicated",
    "manifest documents disk-backed event snapshot semantics",
)
check(
    "fact_token_events.provider_total_tokens" in manifest["source_of_truth"]["never_sum"], "manifest warns against summing provider totals"
)

with open(paths["measures"], encoding="utf-8") as handle:
    measures = handle.read()
check("Total Contributing Tokens" in measures, "DAX measures include total contributing tokens")
check("Under Attributed Tokens" in measures and "Over Attributed Tokens" in measures, "DAX measures include mismatch magnitudes")
check("pricing" not in measures.lower() and "cost" not in measures.lower(), "DAX measures do not introduce pricing")

store_path = os.path.join(out_dir, "events.jsonl")
FileRepository(store_path).append_many(trace.events)
cli_out_dir = os.path.join(out_dir, "cli")
buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = proxy_main(
        [
            "powerbi-export",
            "--store",
            store_path,
            "--output",
            cli_out_dir,
            "--dataset-name",
            "cli_tracker",
        ]
    )
check(exit_code == 0, "powerbi-export CLI exits successfully")
check(os.path.exists(os.path.join(cli_out_dir, "manifest.json")), "powerbi-export CLI writes manifest")
check("Power BI export:" in buffer.getvalue(), "powerbi-export CLI prints artifact location")

partitioned_store = os.path.join(out_dir, "partitioned-store")
PartitionedFileRepository(partitioned_store).append_many(trace.events)
partitioned_cli_out_dir = os.path.join(out_dir, "partitioned-cli")
buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = proxy_main(
        [
            "powerbi-export",
            "--store",
            partitioned_store,
            "--partitioned-store",
            "--output",
            partitioned_cli_out_dir,
            "--dataset-name",
            "partitioned_tracker",
        ]
    )
check(exit_code == 0, "partitioned powerbi-export CLI exits successfully")
check(os.path.exists(os.path.join(partitioned_cli_out_dir, "manifest.json")), "partitioned powerbi-export writes manifest")

iterator_out_dir = os.path.join(out_dir, "iterator")
snapshots_before = set(glob.glob(os.path.join(tempfile.gettempdir(), ".powerbi-event-snapshot-*.sqlite3")))
iterator_paths = export_powerbi_events(
    (event for event in [*trace.events, trace.events[0]]),
    iterator_out_dir,
    dataset_name="iterator_tracker",
    generated_at="2026-07-02T12:00:00+00:00",
)
snapshots_after = set(glob.glob(os.path.join(tempfile.gettempdir(), ".powerbi-event-snapshot-*.sqlite3")))
iterator_event_rows = read_csv(iterator_paths["fact_token_events"])
iterator_quantity_rows = read_csv(iterator_paths["fact_token_quantities"])
iterator_daily_rows = read_csv(iterator_paths["fact_service_daily"])
iterator_metric_rows = read_csv(iterator_paths["metric_snapshots"])
check(len(iterator_event_rows) == 3, "disk-backed iterator export deduplicates event ids")
check(sum(int(row["quantity_in_total"] or 0) for row in iterator_quantity_rows) == 1450, "iterator export keeps quantity rows")
check(any(row["provider"] == "azure_openai" for row in iterator_daily_rows), "iterator export keeps downstream derived tables")
check(snapshots_after == snapshots_before, "temporary disk snapshot is removed after export")
check(
    any(row["metric_group"] == "coverage_exactness" for row in iterator_metric_rows),
    "iterator export keeps coverage metrics without requiring a Trace container",
)
check(
    any(row["metric_group"] == "trust_report" and row["metric"] == "headline_ceiling_tokens" for row in iterator_metric_rows),
    "iterator export keeps the audit trust band without requiring a Trace container",
)

shutil.rmtree(out_dir, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
