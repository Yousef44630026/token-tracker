"""Operational doctor command/readiness checks.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_operational_doctor.py
"""

from __future__ import annotations

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
    _durability_check,
    _python_check,
    _storage_substrate_check,
    _tokenizer_check,
    run_checks,
)
from tracker.ops.doctor import main as doctor_main  # noqa: E402
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
check(_python_check((3, 10, 99)).status == "fail", "doctor rejects Python below the declared 3.11 minimum")
check(_python_check((3, 11, 0)).status == "pass", "doctor accepts the minimum supported Python")
check(by_name["storage-contract"].status == "pass", "doctor verifies source/derived storage contract")
check(by_name["store-writable"].status == "pass", "doctor verifies store directory writability")
check(by_name["store-read"].status == "warn", "missing store is a warning, not a failure")
check(by_name["azure-openai-env"].status == "info", "missing Azure/Foundry env is informational")
check(by_name["durable-persistence"].status == "pass", "durable persistence is the operational default")
check(_tokenizer_check().status in {"pass", "warn"}, "doctor discloses the active tokenizer backend")
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
