"""Focused checks for proxy reliability summaries."""

import csv
import os
import sys
import uuid
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.proxy.cli import main as proxy_main  # noqa: E402
from tracker.proxy.report import (  # noqa: E402
    render_summary,
    summarize_events,
    write_prompt_groups_csv,
)
from tracker.storage.file_repository import PartitionedFileRepository  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def quantity(token_type, value, *, metadata=None):
    return TokenQuantity(
        token_type=token_type,
        quantity=value,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=Additivity.TOTAL_CONTRIBUTING,
        metadata=metadata or {},
    )


def event(
    event_id,
    quantities,
    *,
    status,
    authoritative,
    suite_sequence=None,
    suite_label=None,
    suite_fingerprint=None,
):
    observation = {"status": status, "authoritative": authoritative}
    if suite_sequence is not None:
        observation["suite_prompt_sequence"] = suite_sequence
        observation["suite_prompt_label"] = suite_label or f"prompt-{suite_sequence}"
        observation["suite_prompt_fingerprint"] = suite_fingerprint or (f"{suite_sequence}" * 64)[:64]
        observation["suite_prompt_source"] = "test-suite.md"
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=f"req-{event_id}",
        trace_id="trace-report-test",
        span_id=f"span-{event_id}",
        provider="anthropic",
        model="claude-test",
        api_surface="messages",
        quantities=quantities,
        observation=observation,
    )


complete = event(
    "complete",
    [
        quantity(
            TokenType.INPUT,
            10,
            metadata={
                "prompt_estimate": {
                    "quantity": 7,
                    "provider_prompt_tokens": 17,
                }
            },
        ),
        quantity(TokenType.CACHED_INPUT, 2),
        quantity(
            TokenType.CACHE_CREATION_INPUT,
            5,
            metadata={
                "ephemeral_5m_input_tokens": 4,
                "ephemeral_1h_input_tokens": 1,
            },
        ),
        quantity(TokenType.OUTPUT, 3),
    ],
    status="complete",
    authoritative=True,
    suite_sequence=1,
    suite_label="Small prompt",
    suite_fingerprint="a" * 64,
)

failed_with_exact_usage = event(
    "failed",
    [
        quantity(
            TokenType.INPUT,
            999,
            metadata={
                "prompt_estimate": {
                    "quantity": 100,
                    "provider_prompt_tokens": 1199,
                }
            },
        ),
        quantity(TokenType.CACHE_CREATION_INPUT, 200),
        quantity(TokenType.OUTPUT, 50),
    ],
    status="failed",
    authoritative=False,
    suite_sequence=2,
    suite_label="Failed prompt",
    suite_fingerprint="b" * 64,
)

summary = summarize_events([complete, failed_with_exact_usage])
iterator_summary = summarize_events(event for event in [complete, failed_with_exact_usage])

check(summary["events"] == 2, "all events remain visible")
check(
    iterator_summary["contributing_tokens"] == summary["contributing_tokens"]
    and iterator_summary["prompt_groups"] == summary["prompt_groups"],
    "summary consumes an event iterator without losing prompt groups",
)
check(summary["statuses"] == {"complete": 1, "failed": 1}, "statuses include failed events")
check(
    summary["exact_usage_events"] == 1,
    "exact usage count excludes non-authoritative events",
)
check(summary["incomplete_events"] == 1, "non-authoritative event is incomplete")
check(summary["fresh_input_tokens"] == 10, "fresh input total excludes failed exact usage")
check(summary["cache_read_input_tokens"] == 2, "cache read total uses authoritative events")
check(
    summary["cache_creation_input_tokens"] == 5,
    "cache creation total excludes failed exact usage",
)
check(summary["output_tokens"] == 3, "output total excludes failed exact usage")
check(summary["contributing_tokens"] == 20, "contributing total excludes failed exact usage")
check(summary["stored_contributing_tokens"] == 20, "stored contributing total matches")
check(
    summary["provider_prompt_tokens"] == 17,
    "prompt comparison excludes failed exact usage",
)
check(
    summary["estimated_prompt_tokens"] == 7,
    "estimate comparison excludes failed exact usage",
)
check(
    summary["cache_creation_5m_input_tokens"] == 4,
    "5m cache lifetime comes from complete events",
)
check(
    summary["cache_creation_1h_input_tokens"] == 1,
    "1h cache lifetime comes from complete events",
)
check(
    summary["cache_creation_quantity_count"] == 1,
    "cache detail count excludes failed exact usage",
)
check(
    "detail_events=1/1" in render_summary(summary),
    "summary renders cache lifetime detail coverage",
)
prompt_groups = summary["prompt_groups"]
check(len(prompt_groups) == 2, "report includes two prompt groups")
check(
    prompt_groups[0]["label"] == "Small prompt" and prompt_groups[0]["contributing_tokens"] == 20,
    "complete prompt group carries its authoritative total",
)
check(
    prompt_groups[1]["label"] == "Failed prompt"
    and prompt_groups[1]["contributing_tokens"] == 0
    and prompt_groups[1]["incomplete_events"] == 1,
    "failed prompt group stays visible but contributes zero",
)
check("per-prompt:" in render_summary(summary), "summary renders per-prompt section")

partitioned_root = os.path.abspath(f".test_proxy_report_partitioned_{uuid.uuid4().hex}")
PartitionedFileRepository(partitioned_root).append_many([complete, failed_with_exact_usage])
buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = proxy_main(["report", "--store", partitioned_root, "--partitioned-store"])
report_output = buffer.getvalue()
check(exit_code == 0, "partitioned report CLI exits successfully")
check("events: 2" in report_output and "contributing tokens:" in report_output, "partitioned report CLI reads all partitions")

csv_path = os.path.join(os.getcwd(), ".test_prompt_groups.csv")
try:
    os.remove(csv_path)
except OSError:
    pass
write_prompt_groups_csv(summary, csv_path)
with open(csv_path, encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle))
check(len(rows) == 2 and rows[0]["label"] == "Small prompt", "per-prompt CSV exports groups")
check(rows[0]["contributing_tokens"] == "20", "per-prompt CSV exports token totals")
try:
    os.remove(csv_path)
except OSError:
    pass

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
