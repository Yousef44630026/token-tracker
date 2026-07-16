"""Every aggregate consumer must use the same correlation-effective event view."""

import csv
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.effective_events import effective_events  # noqa: E402
from tracker.export.csv_exporter import event_rows, quantity_rows  # noqa: E402
from tracker.export.powerbi_exporter import export_powerbi_events  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.proxy.report import summarize_events  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def quantity(token_type, value, precision, source):
    return TokenQuantity(token_type, value, precision, source, Additivity.TOTAL_CONTRIBUTING)


partial = TokenEvent(
    event_id="partial",
    request_correlation_id="request-1",
    trace_id="trace-1",
    span_id="span-1",
    provider="openai",
    api_surface="responses",
    quantities=[quantity(TokenType.OUTPUT, 40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER)],
    data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
    timestamp="2026-07-16T10:00:00Z",
    observation={"authoritative": True, "status": "incomplete"},
)
final = TokenEvent(
    event_id="final",
    request_correlation_id="request-1",
    trace_id="trace-1",
    span_id="span-1",
    provider="openai",
    api_surface="responses",
    quantities=[
        quantity(TokenType.INPUT, 100, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL),
        quantity(TokenType.OUTPUT, 60, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL),
    ],
    provider_total_tokens=160,
    timestamp="2026-07-16T10:00:01Z",
    observation={"authoritative": True, "status": "complete"},
)

projected = effective_events([partial, final])
projected_by_id = {event.event_id: event for event in projected}
check(partial.superseded is False, "effective projection never mutates the raw partial")
check(projected_by_id["partial"].superseded is True, "effective projection supersedes the correlated partial")
check(sum(event.event_contributing_tokens for event in projected) == 160, "effective total counts final usage once")

summary = summarize_events([partial, final])
check(summary["contributing_tokens"] == 160, "proxy summary consumes the effective projection")

trace = Trace(trace_id="trace-1", events=[partial, final])
csv_events = event_rows(trace)
csv_quantities = quantity_rows(trace)
check(sum(row["event_contributing_tokens"] for row in csv_events) == 160, "CSV event rows use the effective projection")
check(sum(row["quantity_in_total"] for row in csv_quantities) == 160, "CSV quantity rows reconcile to event rows")

work = os.path.join(os.getcwd(), f".test_effective_projection_{uuid.uuid4().hex}")
os.makedirs(work, exist_ok=True)
try:
    store = os.path.join(work, "events.jsonl")
    compacted = os.path.join(work, "compacted.jsonl")
    repository = FileRepository(store)
    repository.append_many([partial, final])
    check(repository.write_compacted(compacted) == 1, "compaction derives supersession before dropping retired events")
    check([event.event_id for event in FileRepository(compacted).iter_events()] == ["final"], "compaction retains the final only")

    exported = export_powerbi_events(repository.iter_events(), os.path.join(work, "powerbi"))
    with open(exported["fact_token_events"], newline="", encoding="utf-8") as handle:
        powerbi_rows = list(csv.DictReader(handle))
    check(
        sum(int(row["event_contributing_tokens"]) for row in powerbi_rows) == 160,
        "Power BI event facts use the effective projection",
    )
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
