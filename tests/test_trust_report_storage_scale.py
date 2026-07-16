"""Trust report + scalable storage paths.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_trust_report_storage_scale.py
"""

import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.coverage import build_coverage_exactness, build_coverage_exactness_from_events  # noqa: E402
from tracker.analytics.trust_report import build_trust_report, build_trust_report_from_events  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.data_quality import normalizer_flags  # noqa: E402
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(tt, qty, precision=PrecisionLevel.EXACT, add=Additivity.TOTAL_CONTRIBUTING):
    return TokenQuantity(tt, qty, precision, UsageSource.PROVIDER_RESPONSE, add)


trace = Trace(trace_id=f"trust-{uuid.uuid4().hex}")
under = TokenEvent(
    event_id="under",
    request_correlation_id="r-under",
    trace_id=trace.trace_id,
    span_id="s",
    quantities=[q(TokenType.INPUT, 100), q(TokenType.OUTPUT, 40, PrecisionLevel.ESTIMATE)],
    provider_total_tokens=200,
    timestamp="2026-07-06T10:00:00Z",
    observation={"authoritative": True},
)
over = TokenEvent(
    event_id="over",
    request_correlation_id="r-over",
    trace_id=trace.trace_id,
    span_id="s",
    quantities=[q(TokenType.INPUT, 500)],
    provider_total_tokens=400,
    timestamp="2026-07-06T10:01:00Z",
    observation={"authoritative": True},
)
unverified = TokenEvent(
    event_id="unverified",
    request_correlation_id="r-unverified",
    trace_id=trace.trace_id,
    span_id="s",
    quantities=[q(TokenType.INPUT, 30, add=Additivity.UNVERIFIED)],
    timestamp="2026-07-06T10:02:00Z",
    observation={"authoritative": True},
)
superseded = TokenEvent(
    event_id="old",
    request_correlation_id="r-under",
    trace_id=trace.trace_id,
    span_id="s",
    quantities=[q(TokenType.OUTPUT, 999)],
    superseded=True,
    superseded_by="under",
    timestamp="2026-07-06T09:59:00Z",
    observation={"authoritative": True},
)
custom = TokenEvent(
    event_id="custom",
    request_correlation_id="r-custom",
    trace_id=trace.trace_id,
    span_id="s",
    quantities=[],
    data_quality_flags=["tenant_12345_dynamic_label", "raw_usage_missing"],
    observation={"authoritative": True},
)
for event in (under, over, unverified, superseded, custom):
    trace.add_event(event)

check(under.under_attributed_tokens == 60 and under.over_attributed_tokens == 0, "under-attribution magnitude is signed")
check(over.under_attributed_tokens == 0 and over.over_attributed_tokens == 100, "over-attribution magnitude is signed")
check(
    {"provider_total_mismatch", "provider_total_under_attribution"} <= set(normalizer_flags(under.quantities, 200)),
    "normalizer emits under-attribution direction",
)
check(
    {"provider_total_mismatch", "provider_total_over_attribution"} <= set(normalizer_flags(over.quantities, 400)),
    "normalizer emits over-attribution direction",
)
check(custom.data_quality_flags == ["custom", "raw_usage_missing"], "unknown quality labels are capped to custom")

coverage = build_coverage_exactness(trace)
streaming_coverage = build_coverage_exactness_from_events(event for event in trace.events)
check(streaming_coverage == coverage, "iterator CoverageExactness matches Trace CoverageExactness")
check(coverage["unattributed_tokens"] == 60, "CoverageExactness carries unattributed_tokens")
check(coverage["over_attributed_tokens"] == 100, "CoverageExactness carries over_attributed_tokens")
check(coverage["headline_floor_tokens"] == 600, "provider totals pin the trusted floor")
check(coverage["headline_estimate_tokens"] == 600, "provider totals replace mismatched quantity estimates")
check(coverage["headline_ceiling_tokens"] == 630, "known independent uncertainty widens only the ceiling")
check(coverage["capture_completeness_ratio"] is None, "mixed under/over attribution has no misleading completeness ratio")

report = build_trust_report(trace)
streaming_report = build_trust_report_from_events((event for event in trace.events), trace_id=trace.trace_id)
check(streaming_report.to_dict() == report.to_dict(), "iterator TrustReport matches Trace TrustReport")
aggregate_report = build_trust_report_from_events(
    (event for event in trace.events),
    trace_id=trace.trace_id,
    collect_anomalies=False,
)
check(
    aggregate_report.anomaly_count == report.anomaly_count and aggregate_report.anomalies == [],
    "aggregate-only TrustReport keeps anomaly count without retaining anomaly details",
)
check(report.headline_floor_tokens == 600 and report.headline_ceiling_tokens == 630, "TrustReport carries headline band")
check(report.attribution_status == "mixed", "TrustReport names mixed attribution direction")
check(report.unattributed_tokens == 60 and report.over_attributed_tokens == 100, "TrustReport carries mismatch magnitudes")
check(any(a.event_id == "over" and a.severity == "high" for a in report.anomalies), "over-attribution anomaly is high severity")

work = os.path.join(os.getcwd(), f".test_trust_report_storage_scale_{uuid.uuid4().hex}")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work, exist_ok=True)
flat_path = os.path.join(work, "flat.jsonl")
flat = FileRepository(flat_path)
flat.append_many(trace.events)
streamed_ids = [event.event_id for event in flat.iter_events()]
expected_ids = [event.event_id for event in trace.events]
check(streamed_ids == expected_ids, "FileRepository.iter_events streams in order")
compacted_path = os.path.join(work, "flat-compacted.jsonl")
check(flat.write_compacted(compacted_path) == 4, "flat compaction drops superseded events")
check("old" not in {event.event_id for event in FileRepository(compacted_path).iter_events()}, "compacted JSONL excludes superseded")

partitioned = PartitionedFileRepository(os.path.join(work, "partitioned"))
partitioned.append_many(trace.events)
check(len(partitioned.read_all()) == 5, "partitioned repository reads all events")
expected_partition = os.path.join(
    work,
    "partitioned",
    "date=2026-07-06",
    f"trace_id={trace.trace_id}",
    "events.jsonl",
)
check(os.path.exists(expected_partition), "partitioned repository writes date/trace path")
partitioned_compacted = os.path.join(work, "partitioned-compacted")
check(partitioned.write_compacted(partitioned_compacted) == 4, "partitioned compaction drops superseded events")
check(
    "old" not in {event.event_id for event in PartitionedFileRepository(partitioned_compacted).iter_events()},
    "partitioned compacted copy excludes superseded",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if _failures else 0)
