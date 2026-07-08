"""Regression checks for architecture hardening added after the initial phases."""

import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.analytics.anomaly_signals import detect_anomalies  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.estimation.historical_forecaster import forecast_tokens  # noqa: E402
from tracker.export.csv_exporter import export_csv, quantity_rows  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.span import Span  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.additivity import assign_additivity  # noqa: E402
from tracker.normalization.reconciler import reconcile_event_quality  # noqa: E402
from tracker.service import track_response, track_stream  # noqa: E402
from tracker.storage.trace_repository import TraceFileRepository  # noqa: E402

_failures = 0
WORK_DIR = os.path.join(os.getcwd(), ".test_architecture_hardening")
shutil.rmtree(WORK_DIR, ignore_errors=True)
os.makedirs(WORK_DIR, exist_ok=True)


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


# Unknown provider fields cannot silently affect totals.
additivity, parent = assign_additivity("new-provider", "new-surface", TokenType.OUTPUT)
check(
    additivity == Additivity.UNVERIFIED and parent is None,
    "unknown additivity fails closed",
)

# Streaming uses the same quality policy as normal responses.
context = new_trace(workflow="stream")
stream = track_stream(context=context, provider="openai", api_surface="responses")
mismatch = stream.complete(output_tokens=10, input_tokens=5, provider_total_tokens=99)
check("provider_total_mismatch" in mismatch.data_quality_flags, "stream mismatch is flagged")
timed_out = track_stream(context=context, provider="openai", api_surface="responses").timeout()
check("unknown_quantity_present" in timed_out.data_quality_flags, "stream unknown is flagged")
signal_trace = Trace(trace_id=context.trace_id)
signal_trace.add_event(mismatch)
check(
    detect_anomalies(signal_trace)[0].code == "provider_total_mismatch",
    "derived anomaly signals expose mismatches",
)

# Invalid source-of-truth states are rejected at construction/aggregate boundaries.
negative_rejected = False
try:
    TokenQuantity(
        TokenType.OUTPUT,
        -1,
        PrecisionLevel.EXACT,
        UsageSource.PROVIDER_RESPONSE,
        Additivity.TOTAL_CONTRIBUTING,
    )
except ValueError:
    negative_rejected = True
check(negative_rejected, "negative quantities are rejected")

trace_mismatch_rejected = False
try:
    Trace(trace_id="trace-a").add_event(
        TokenEvent(
            event_id="event-a",
            request_correlation_id="request-a",
            trace_id="trace-b",
            span_id="span-a",
        )
    )
except ValueError:
    trace_mismatch_rejected = True
check(trace_mismatch_rejected, "cross-trace events are rejected")
check(forecast_tokens([10, 1000, 12]) == 12, "historical forecast is robust to an outlier")

# Complete trace snapshots preserve span metadata and event derivations.
trace = Trace(trace_id="trace-1", workflow="rag")
span = Span(
    span_id="span-1",
    trace_id="trace-1",
    span_type="tool",
    metadata={"tool_name": "search", "result_tokens": 12},
)
trace.add_span(span)
quantity = TokenQuantity(
    TokenType.OUTPUT,
    20,
    PrecisionLevel.EXACT,
    UsageSource.PROVIDER_RESPONSE,
    Additivity.TOTAL_CONTRIBUTING,
)
trace.add_event(
    TokenEvent(
        event_id="event-1",
        request_correlation_id="request-1",
        trace_id="trace-1",
        span_id="span-1",
        quantities=[quantity],
        provider_total_tokens=20,
    )
)
trace.events[0].provider_total_tokens = 99
reconcile_event_quality(trace.events[0])
check(
    "provider_total_mismatch" in trace.events[0].data_quality_flags,
    "event quality can be reconciled after source fields change",
)
trace.events[0].provider_total_tokens = 20
reconcile_event_quality(trace.events[0])
snapshot_dir = os.path.join(WORK_DIR, "trace")
os.makedirs(snapshot_dir, exist_ok=True)
snapshot_path = os.path.join(snapshot_dir, "trace.json")
snapshot = TraceFileRepository(snapshot_path)
snapshot.save(trace)
loaded = snapshot.load()
check(loaded == trace, "complete trace snapshot round-trips")
check(
    loaded is not None and loaded.spans[0].metadata["tool_name"] == "search",
    "span metadata survives persistence",
)
with open(snapshot_path, encoding="utf-8") as handle:
    raw_snapshot = json.load(handle)
check(
    "event_contributing_tokens" not in json.dumps(raw_snapshot),
    "trace snapshot stores no derived totals",
)

# Quantity exports are directly summable and spans are exported.
superseded = TokenEvent(
    event_id="event-old",
    request_correlation_id="request-old",
    trace_id="trace-1",
    span_id="span-1",
    quantities=[quantity],
    superseded=True,
    superseded_by="event-1",
)
trace.add_event(superseded)
check(
    sum(row["quantity_in_total"] for row in quantity_rows(trace)) == 20,
    "quantity export total needs no superseded-row filter",
)
export_dir = os.path.join(WORK_DIR, "export")
os.makedirs(export_dir, exist_ok=True)
paths = export_csv(trace, export_dir)
with open(paths["token_spans"], newline="", encoding="utf-8") as handle:
    span_export = list(csv.DictReader(handle))
check(
    len(span_export) == 1 and json.loads(span_export[0]["metadata"])["tool_name"] == "search",
    "span metadata is exported",
)

# Public façade performs the normal response path and attaches to a trace.
payload = {
    "model": "example",
    "usage": {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10},
}
facade_trace = Trace(trace_id=context.trace_id)
result = track_response(
    payload,
    OpenAIResponsesAdapter(),
    context=context,
    trace=facade_trace,
)
check(result.event.event_contributing_tokens == 10, "public response façade normalizes usage")
check(facade_trace.events == [result.event], "public response façade attaches the event")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
shutil.rmtree(WORK_DIR, ignore_errors=True)
sys.exit(1 if _failures else 0)
