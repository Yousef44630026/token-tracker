"""Operational doctor command/readiness checks.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_operational_doctor.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.ops.doctor import main as doctor_main  # noqa: E402
from tracker.ops.doctor import run_checks  # noqa: E402
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository  # noqa: E402

check = make_checker()


def event(event_id: str, trace_id: str = "doctor-trace") -> TokenEvent:
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=f"req-{event_id}",
        trace_id=trace_id,
        span_id="span",
        quantities=[
            TokenQuantity(
                TokenType.OUTPUT,
                42,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        provider_total_tokens=42,
    )


root = os.path.abspath(f".test_operational_doctor_{uuid.uuid4().hex}")
os.makedirs(root, exist_ok=True)

missing_store = os.path.join(root, "missing.jsonl")
missing = run_checks(store=missing_store)
by_name = {item.name: item for item in missing}
check(by_name["python"].status == "pass", "doctor checks supported Python")
check(by_name["storage-contract"].status == "pass", "doctor verifies source/derived storage contract")
check(by_name["store-writable"].status == "pass", "doctor verifies store directory writability")
check(by_name["store-read"].status == "warn", "missing store is a warning, not a failure")

store = os.path.join(root, "events.jsonl")
FileRepository(store).append(event("evt-1"))
valid = run_checks(store=store)
valid_by_name = {item.name: item for item in valid}
check(valid_by_name["store-read"].status == "pass", "valid JSONL store passes read check")
check(valid_by_name["store-read"].data["event_count"] == 1, "doctor reports event count")
check(valid_by_name["store-read"].data["observed_total_contributing_tokens"] == 42, "doctor reports contributing total")

partitioned = os.path.join(root, "partitioned")
PartitionedFileRepository(partitioned).append(event("evt-2", trace_id="partitioned-trace"))
partitioned_checks = run_checks(store=partitioned, partitioned_store=True)
partitioned_by_name = {item.name: item for item in partitioned_checks}
check(partitioned_by_name["store-read"].status == "pass", "partitioned store passes read check")
check(partitioned_by_name["store-read"].data["event_count"] == 1, "partitioned doctor counts events")

corrupt = os.path.join(root, "corrupt.jsonl")
with open(corrupt, "w", encoding="utf-8") as handle:
    handle.write("{not-json}\n")
corrupt_checks = run_checks(store=corrupt)
check({item.name: item for item in corrupt_checks}["store-read"].status == "fail", "corrupt JSONL store fails readiness")

buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = doctor_main(["--store", store, "--json"])
payload = json.loads(buffer.getvalue())
check(exit_code == 0, "doctor CLI exits 0 for ready store")
check(payload["passed"] is True and payload["failure_count"] == 0, "doctor JSON reports passed")

buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = doctor_main(["--store", corrupt])
check(exit_code == 1, "doctor CLI exits non-zero for corrupt store")

sys.exit(check.report("RESULT test_operational_doctor"))
