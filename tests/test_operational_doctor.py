"""Operational doctor command/readiness checks.

Run: python tests/test_operational_doctor.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import uuid
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.ops.doctor import (  # noqa: E402
    _claude_import_evidence_check,
    _dashboard_evidence_check,
    _durability_check,
    _health_evidence_check,
    _python_check,
    _storage_substrate_check,
    _tokenizer_check,
    run_checks,
)
from tracker.ops.doctor import main as doctor_main  # noqa: E402
from tracker.ops.runtime_fingerprint import runtime_fingerprint  # noqa: E402
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
        observation={"authoritative": True},
    )


root = os.path.abspath(f".test_operational_doctor_{uuid.uuid4().hex}")
os.makedirs(root, exist_ok=True)

missing_store = os.path.join(root, "missing.jsonl")
missing = run_checks(store=missing_store)
by_name = {item.name: item for item in missing}
check(by_name["python"].status == "pass", "doctor checks supported Python")
check(_python_check((3, 10, 99)).status == "fail", "doctor rejects Python below the declared 3.11 minimum")
check(_python_check((3, 11, 0)).status == "pass", "doctor accepts the minimum supported Python")
check(by_name["storage-contract"].status == "pass", "doctor verifies source/derived storage contract")
check(by_name["store-writable"].status == "pass", "doctor verifies store directory writability")
check(by_name["store-read"].status == "warn", "missing store is a warning, not a failure")
check(by_name["azure-openai-env"].status == "info", "missing Azure/Foundry env is informational")
check(by_name["durable-persistence"].status == "pass", "durable persistence is the operational default")
check(_tokenizer_check().status == "pass", "doctor requires the installed tiktoken backend")
check(
    _storage_substrate_check(r"C:\Users\operator\OneDrive\tracker\events.jsonl").status == "warn",
    "doctor warns when the append-only store is inside OneDrive",
)
check(
    _storage_substrate_check(r"D:\tracker-data\events.jsonl").status == "pass",
    "doctor accepts a non-synced local store path",
)
check(
    _durability_check({"TRACKER_DURABLE": "false"}).status == "warn",
    "doctor warns when collector durability is explicitly disabled",
)
check(
    _durability_check({"TRACKER_PROXY_DURABLE": "sometimes"}).status == "fail",
    "doctor fails invalid durability configuration",
)

health_now = dt.datetime(2026, 7, 15, 10, 0, tzinfo=dt.UTC)
health_log = os.path.join(root, "collector-health.jsonl")
check(
    _health_evidence_check(health_log, now=health_now).status == "warn",
    "doctor warns when health evidence has not started",
)
with open(health_log, "w", encoding="utf-8") as handle:
    handle.write(
        json.dumps(
            {
                "timestamp": "2026-07-15T09:59:00Z",
                "healthy": True,
                "status": "ok",
                "runtime_fingerprint": runtime_fingerprint(),
            }
        )
        + "\n"
    )
check(
    _health_evidence_check(health_log, max_age_seconds=300, now=health_now).status == "pass",
    "doctor accepts fresh healthy collector evidence",
)
with open(health_log, "a", encoding="utf-8") as handle:
    handle.write('{"timestamp":"2026-07-15T09:50:00Z","healthy":true,"status":"ok"}\n')
stale_health = _health_evidence_check(health_log, max_age_seconds=300, now=health_now)
check(stale_health.status == "fail", "doctor dead-man fails stale collector evidence")
check(stale_health.data["age_seconds"] == 600, "dead-man reports the measured evidence age")
with open(health_log, "a", encoding="utf-8") as handle:
    handle.write('{"timestamp":"2026-07-15T10:00:00Z","healthy":false,"status":"offline"}\n')
check(
    _health_evidence_check(health_log, max_age_seconds=300, now=health_now).status == "fail",
    "doctor fails a fresh probe that reports the collector offline",
)
with open(health_log, "a", encoding="utf-8") as handle:
    handle.write("{malformed}\n")
check(
    _health_evidence_check(health_log, max_age_seconds=300, now=health_now).status == "fail",
    "doctor fails malformed latest health evidence",
)
with open(health_log, "a", encoding="utf-8") as handle:
    handle.write('{"timestamp":"2026-07-15T10:02:00Z","healthy":true,"status":"ok"}\n')
check(
    _health_evidence_check(health_log, max_age_seconds=300, now=health_now).status == "fail",
    "doctor fails health evidence with material future clock skew",
)

claude_import_log = os.path.join(root, "claude-import.log")
check(
    _claude_import_evidence_check(claude_import_log, now=health_now).status == "warn",
    "doctor warns before scheduled Claude import evidence exists",
)
with open(claude_import_log, "w", encoding="utf-8") as handle:
    handle.write(
        '{"timestamp":"2026-07-15T09:30:00Z","status":"ok",'
        '"import_report":{"format_drift_suspected":false}}\n'
    )
check(
    _claude_import_evidence_check(claude_import_log, max_age_seconds=7200, now=health_now).status == "pass",
    "doctor accepts a fresh healthy Claude import run",
)
with open(claude_import_log, "a", encoding="utf-8") as handle:
    handle.write(
        '{"timestamp":"2026-07-15T09:40:00Z","status":"ok",'
        '"import_report":{"format_drift_suspected":false,"provider_schema_drift_events":1}}\n'
    )
check(
    _claude_import_evidence_check(claude_import_log, max_age_seconds=7200, now=health_now).status == "fail",
    "doctor fails a nominal import run that stored provider schema drift",
)
with open(claude_import_log, "a", encoding="utf-8") as handle:
    handle.write(
        '{"timestamp":"2026-07-15T09:45:00Z","status":"format_drift",'
        '"import_report":{"format_drift_suspected":true}}\n'
    )
check(
    _claude_import_evidence_check(claude_import_log, max_age_seconds=7200, now=health_now).status == "fail",
    "doctor fails a fresh import run that detects format drift",
)
with open(claude_import_log, "a", encoding="utf-8") as handle:
    handle.write(
        '{"timestamp":"2026-07-15T06:00:00Z","status":"ok",'
        '"import_report":{"format_drift_suspected":false}}\n'
    )
check(
    _claude_import_evidence_check(claude_import_log, max_age_seconds=7200, now=health_now).status == "fail",
    "doctor dead-man fails stale Claude import evidence",
)

dashboard_evidence = os.path.join(root, "dashboard-refresh.json")
dashboard_output = os.path.join(root, "dashboard.xlsx")
check(
    _dashboard_evidence_check(dashboard_evidence, now=health_now).status == "warn",
    "doctor warns before scheduled dashboard evidence exists",
)
with open(dashboard_output, "wb") as handle:
    handle.write(b"test workbook")
with open(dashboard_evidence, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "timestamp": "2026-07-15T09:30:00Z",
            "status": "ok",
            "output_file": dashboard_output,
            "report": {"valid_events": 10, "skipped_lines": 0, "duplicate_event_ids": 0},
        },
        handle,
    )
check(
    _dashboard_evidence_check(dashboard_evidence, max_age_seconds=7200, now=health_now).status == "pass",
    "doctor accepts a fresh complete dashboard refresh",
)
with open(dashboard_evidence, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "timestamp": "2026-07-15T09:45:00Z",
            "status": "ok",
            "output_file": dashboard_output,
            "report": {"valid_events": 9, "skipped_lines": 1, "duplicate_event_ids": 0},
        },
        handle,
    )
check(
    _dashboard_evidence_check(dashboard_evidence, max_age_seconds=7200, now=health_now).status == "fail",
    "doctor rejects a dashboard refresh that skipped source rows",
)
with open(dashboard_evidence, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "timestamp": "2026-07-15T06:00:00Z",
            "status": "ok",
            "output_file": dashboard_output,
            "report": {"valid_events": 10, "skipped_lines": 0, "duplicate_event_ids": 0},
        },
        handle,
    )
check(
    _dashboard_evidence_check(dashboard_evidence, max_age_seconds=7200, now=health_now).status == "fail",
    "doctor dead-man fails stale dashboard refresh evidence",
)

foundry_checks = run_checks(
    store=missing_store,
    skip_store=True,
    environment={
        "AZURE_OPENAI_API_KEY": "unit-key",
        "AZURE_OPENAI_RESPONSES_ENDPOINT": "https://unit.services.ai.azure.com/openai/v1",
        "AZURE_OPENAI_RESPONSES_DEPLOYMENT": "gpt-5-mini",
    },
)
foundry_env = {item.name: item for item in foundry_checks}["azure-openai-env"]
check(foundry_env.status == "pass", "Foundry Responses-only env passes doctor")
check(foundry_env.data["profiles"] == ["foundry-responses"], "doctor reports the Foundry Responses profile")

classic_checks = run_checks(
    store=missing_store,
    skip_store=True,
    environment={
        "AZURE_OPENAI_API_KEY": "unit-key",
        "AZURE_OPENAI_ENDPOINT": "https://unit.openai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "chat-dep",
    },
)
classic_env = {item.name: item for item in classic_checks}["azure-openai-env"]
check(classic_env.status == "pass", "classic Azure chat env passes without requiring explicit api-version")
check(classic_env.data["profiles"] == ["azure-chat"], "doctor reports the Azure chat profile")

partial_checks = run_checks(
    store=missing_store,
    skip_store=True,
    environment={"AZURE_OPENAI_API_KEY": "unit-key"},
)
partial_env = {item.name: item for item in partial_checks}["azure-openai-env"]
check(partial_env.status == "warn", "API key alone is a partial Azure/Foundry env")

secret_root = os.path.join(root, "secret-scan")
os.makedirs(secret_root, exist_ok=True)
with open(os.path.join(secret_root, "README.md"), "w", encoding="utf-8") as handle:
    handle.write("leaked=" + ("A" * 88) + "\n")
secret_checks = run_checks(store=missing_store, skip_store=True, environment={}, secret_scan_root=secret_root)
secret_scan = {item.name: item for item in secret_checks}["secret-scan"]
check(secret_scan.status == "fail", "doctor fails when credential-shaped values are in project files")
check(secret_scan.data["findings"][0]["path"] == "README.md", "secret scan reports only path/line/kind metadata")

local_secret_root = os.path.join(root, "local-secret-scan")
os.makedirs(local_secret_root, exist_ok=True)
with open(os.path.join(local_secret_root, ".env"), "w", encoding="utf-8") as handle:
    handle.write("AZURE_OPENAI_API_KEY=" + ("B" * 88) + "\n")
local_secret_checks = run_checks(store=missing_store, skip_store=True, environment={}, secret_scan_root=local_secret_root)
local_secret_scan = {item.name: item for item in local_secret_checks}["secret-scan"]
check(local_secret_scan.status == "warn", "doctor warns, not fails, for ignored local .env secrets")

empty_store = os.path.join(root, "empty.jsonl")
with open(empty_store, "w", encoding="utf-8"):
    pass
empty_checks = run_checks(store=empty_store)
empty_store_read = {item.name: item for item in empty_checks}["store-read"]
check(empty_store_read.status == "pass", "initialized empty JSONL store passes readiness")
check(empty_store_read.data["event_count"] == 0, "doctor reports zero events for an empty store")

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
clean_scan_root = os.path.join(root, "clean-scan")
os.makedirs(clean_scan_root, exist_ok=True)
with redirect_stdout(buffer):
    exit_code = doctor_main(["--store", store, "--secret-scan-root", clean_scan_root, "--json"])
payload = json.loads(buffer.getvalue())
check(exit_code == 0, "doctor CLI exits 0 for ready store")
check(payload["passed"] is True and payload["failure_count"] == 0, "doctor JSON reports passed")

buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = doctor_main(["--store", corrupt, "--secret-scan-root", clean_scan_root])
check(exit_code == 1, "doctor CLI exits non-zero for corrupt store")

shutil.rmtree(root, ignore_errors=True)
sys.exit(check.report("RESULT test_operational_doctor"))
