"""Collector partitioning and effective-event stats stay aligned."""

import json
import os
import shutil
import sys
import threading
import uuid
from urllib import request as urllib_request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server, make_http_transport  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import PartitionedFileRepository  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def get_json(url):
    with urllib_request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def quantity(value, precision, source):
    return TokenQuantity(TokenType.OUTPUT, value, precision, source, Additivity.TOTAL_CONTRIBUTING)


partial = TokenEvent(
    event_id="partitioned-partial",
    request_correlation_id="partitioned-request",
    trace_id="partitioned-trace",
    span_id="span",
    quantities=[quantity(40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER)],
    data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
    timestamp="2026-07-16T10:00:00Z",
    observation={"authoritative": True, "status": "incomplete"},
)
final = TokenEvent(
    event_id="partitioned-final",
    request_correlation_id="partitioned-request",
    trace_id="partitioned-trace",
    span_id="span",
    quantities=[quantity(160, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL)],
    provider_total_tokens=160,
    timestamp="2026-07-16T10:00:01Z",
    observation={"authoritative": True, "status": "complete"},
)

work = os.path.join(os.getcwd(), f".test_api_partitioned_{uuid.uuid4().hex}")
store = os.path.join(work, "ledger")
os.makedirs(work, exist_ok=True)
repository = PartitionedFileRepository(store)
server = create_server(repository, "127.0.0.1", 0)
port = server.server_address[1]
base = f"http://127.0.0.1:{port}"
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

try:
    transport = make_http_transport(base + "/v1/events")
    check(
        transport([partial.to_dict(), final.to_dict()]) == [partial.event_id, final.event_id],
        "partitioned collector accepts both source events",
    )
    summary = get_json(base + "/v1/stats?summary=1")
    check(summary["events"] == 2, "raw source-event count remains auditable")
    check(summary["effective_events"] == 1, "one correlated final is operationally effective")
    check(summary["superseded_events"] == 1, "partial retirement is visible")
    check(summary["total"] == 160, "collector stats never double-count partial plus final")
    check(os.path.exists(repository.index_path), "partitioned store maintains its disposable event index")
    expected_partition = os.path.join(store, "date=2026-07-16", "trace_id=partitioned-trace", "events.jsonl")
    check(os.path.exists(expected_partition), "collector writes date/trace partitions")
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    shutil.rmtree(work, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
