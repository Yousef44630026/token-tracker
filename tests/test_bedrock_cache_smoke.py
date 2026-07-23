"""Bedrock cache smoke harness is auditable without making live AWS calls."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.bedrock_cache_smoke import run_bedrock_cache_smoke  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()


class FakeBedrockClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def converse(self, **payload):
        self.requests.append(payload)
        call_index = len(self.requests)
        cache_write = 5000 if call_index == 1 else 0
        cache_read = 0 if call_index == 1 else 5000
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "SENSITIVE GENERATED CONTENT"}]}},
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 10,
                "outputTokens": 2,
                "totalTokens": 5012,
                "cacheReadInputTokens": cache_read,
                "cacheWriteInputTokens": cache_write,
                "cacheDetails": ([{"ttl": "5m", "inputTokens": cache_write}] if cache_write else []),
            },
            "metrics": {"latencyMs": 7 + call_index},
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": f"aws-request-{call_index}",
                "RetryAttempts": 0,
                "HTTPHeaders": {"authorization": "must-not-be-captured"},
            },
        }


class FakeClientError(Exception):
    def __init__(self) -> None:
        super().__init__("Access denied")
        self.response = {
            "Error": {"Code": "AccessDeniedException", "Message": "Access denied"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        }


class FailingBedrockClient:
    def converse(self, **payload):
        raise FakeClientError


class RetriedBedrockClient(FakeBedrockClient):
    def converse(self, **payload):
        response = super().converse(**payload)
        response["ResponseMetadata"]["RetryAttempts"] = 1
        return response


environment = {
    "AWS_REGION": "us-east-1",
    "BEDROCK_MODEL_ID": "anthropic.claude-unit-v1:0",
    "AWS_BEARER_TOKEN_BEDROCK": "secret-bedrock-token",
}

root = Path.cwd() / f".test_bedrock_cache_smoke_{uuid.uuid4().hex}"
root.mkdir(parents=True, exist_ok=False)
try:
    client = FakeBedrockClient()
    summary = run_bedrock_cache_smoke(
        out_dir=str(root / "success"),
        environment=environment,
        client_factory=lambda region: client,
        sleeper=lambda seconds: None,
        prefix_words=5000,
        run_marker="unit-run-marker",
        require_live=True,
    )

    check(summary.passed, "two-call cache proof passes")
    check(summary.ran_count == 2 and summary.event_count == 2, "both live-shaped calls produce events")
    check(summary.cache_write_tokens == 5000, "first call proves cache creation magnitude")
    check(summary.cache_read_tokens == 5000, "second call proves cache reuse magnitude")
    check(len(client.requests) == 2 and client.requests[0] == client.requests[1], "both calls use an identical request")
    content = client.requests[0]["messages"][0]["content"]
    check(content[-1] == {"cachePoint": {"type": "default"}}, "request uses the official Converse cachePoint block")

    events = FileRepository(summary.artifacts["events_jsonl"]).read_all()
    check(all(event.is_authoritative for event in events), "successful responses are authoritative")
    check(all(event.event_total_mismatch == 0 for event in events), "cache buckets reconcile to Bedrock totalTokens")
    check(all(event.model == "anthropic.claude-unit-v1:0" for event in events), "model identity survives normalization")
    check(
        all("provider_schema_drift" not in event.data_quality_flags for event in events),
        "current cacheDetails.inputTokens is recognized without schema-drift noise",
    )

    bundle_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "success").rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".jsonl"}
    )
    check("secret-bedrock-token" not in bundle_text, "credential value never enters audit artifacts")
    check("SENSITIVE GENERATED CONTENT" not in bundle_text, "generated content never enters audit artifacts")
    check("must-not-be-captured" not in bundle_text, "raw HTTP headers are excluded")
    check("Cache validation run unit-run-marker" not in bundle_text, "prompt content never enters audit artifacts")
    plan = json.loads(Path(summary.artifacts["plan"]).read_text(encoding="utf-8"))
    check(len(plan["prefix_sha256"]) == 64 and plan["prompt_or_output_content_stored"] is False, "plan stores hash and policy only")

    factory_called = False

    def forbidden_factory(region):
        nonlocal_factory_marker[0] = True
        raise AssertionError("dry-run must not create a live client")

    nonlocal_factory_marker = [factory_called]
    dry = run_bedrock_cache_smoke(
        out_dir=str(root / "dry"),
        environment=environment,
        client_factory=forbidden_factory,
        dry_run=True,
        require_live=False,
        prefix_words=10,
        run_marker="dry",
    )
    check(dry.passed and dry.ran_count == 0, "dry-run remains zero-call and successful")
    check(nonlocal_factory_marker == [False], "dry-run never constructs the SDK client")

    retried = run_bedrock_cache_smoke(
        out_dir=str(root / "retried"),
        environment=environment,
        client_factory=lambda region: RetriedBedrockClient(),
        sleeper=lambda seconds: None,
        prefix_words=10,
        run_marker="retried",
        require_live=True,
    )
    check(not retried.passed, "Bedrock cache proof rejects hidden SDK retries")
    check(
        retried.results[0].detail == "automatic_retry_detected=1",
        "Bedrock retry rejection carries an auditable reason",
    )

    missing = run_bedrock_cache_smoke(
        out_dir=str(root / "missing"),
        environment={},
        client_factory=forbidden_factory,
        require_live=True,
        prefix_words=10,
        run_marker="missing",
    )
    check(not missing.passed and missing.failure_count == 1, "require-live fails closed on missing model/region")

    failed = run_bedrock_cache_smoke(
        out_dir=str(root / "failure"),
        environment=environment,
        client_factory=lambda region: FailingBedrockClient(),
        sleeper=lambda seconds: None,
        prefix_words=10,
        run_marker="failure",
        require_live=True,
    )
    check(not failed.passed and failed.results[0].detail == "auth_or_access_denied", "AWS access failure gets a stable label")
    failed_events = FileRepository(failed.artifacts["events_jsonl"]).read_all()
    check(not failed_events[0].is_authoritative, "failed AWS calls cannot enter authoritative totals")
finally:
    shutil.rmtree(root, ignore_errors=True)

sys.exit(check.report("RESULT test_bedrock_cache_smoke"))
