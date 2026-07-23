"""Bedrock ConverseStream proof harness tests without live AWS calls."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.ops.bedrock_stream_smoke import run_bedrock_stream_smoke  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()


class FakeBedrockClient:
    def __init__(self, *, with_usage: bool = True, retry_attempts: int = 0) -> None:
        self.with_usage = with_usage
        self.retry_attempts = retry_attempts
        self.requests: list[dict] = []

    def converse_stream(self, **payload):
        self.requests.append(payload)
        events = [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "89 valid records"}}},
            {"messageStop": {"stopReason": "end_turn"}},
        ]
        if self.with_usage:
            events.append(
                {
                    "metadata": {
                        "usage": {
                            "inputTokens": 10,
                            "outputTokens": 5,
                            "totalTokens": 20,
                            "cacheReadInputTokens": 2,
                            "cacheWriteInputTokens": 3,
                        },
                        "metrics": {"latencyMs": 12},
                    }
                }
            )
        return {
            "ResponseMetadata": {
                "RequestId": "bedrock-unit-request",
                "RetryAttempts": self.retry_attempts,
            },
            "stream": events,
        }


root = Path(f".test_bedrock_stream_{uuid.uuid4().hex}").resolve()
root.mkdir(parents=True, exist_ok=True)
env = {"AWS_REGION": "eu-west-1", "BEDROCK_MODEL_ID": "unit.model-v1"}

try:
    client = FakeBedrockClient()
    summary = run_bedrock_stream_smoke(
        out_dir=str(root / "success"),
        environment=env,
        client_factory=lambda region: client,
        require_live=True,
    )
    check(summary.passed is True, "Bedrock ConverseStream proof passes exact terminal usage")
    check(summary.observed_total_contributing_tokens == 20, "cache and ordinary Bedrock buckets reconcile")
    check(summary.provider_total_tokens == 20, "Bedrock provider total is preserved")
    check(client.requests[0]["inferenceConfig"]["maxTokens"] == 64, "live proof bounds generated output")
    event = FileRepository(summary.artifacts["events_jsonl"]).read_all()[0]
    quantities = {quantity.token_type: quantity.quantity for quantity in event.quantities}
    check(quantities[TokenType.INPUT] == 10 and quantities[TokenType.OUTPUT] == 5, "input and output are exact")
    check(
        quantities[TokenType.CACHED_INPUT] == 2 and quantities[TokenType.CACHE_CREATION_INPUT] == 3,
        "stream proof preserves both cache directions",
    )
    check(event.event_total_mismatch == 0, "Bedrock stream event exactly reconciles")
    raw = Path(summary.artifacts["raw"]).read_text(encoding="utf-8")
    check("89 valid records" not in raw, "Bedrock audit never stores generated text")
    raw_payload = json.loads(raw)
    check(
        raw_payload["events"][1]["contentBlockDelta"]["delta"]["text_characters"] == 16,
        "audit retains only the generated character count",
    )

    cut = run_bedrock_stream_smoke(
        out_dir=str(root / "missing-usage"),
        environment=env,
        client_factory=lambda region: FakeBedrockClient(with_usage=False),
        require_live=True,
    )
    check(cut.passed is False, "Bedrock stream without terminal metadata usage fails closed")
    check(
        "provider_stream_usage_missing" in cut.data_quality_flags,
        "missing Bedrock terminal usage raises the canonical quality signal",
    )

    retried = run_bedrock_stream_smoke(
        out_dir=str(root / "retried"),
        environment=env,
        client_factory=lambda region: FakeBedrockClient(retry_attempts=1),
        require_live=True,
    )
    check(retried.passed is False, "Bedrock proof rejects an SDK call that hid an automatic retry")

    dry = run_bedrock_stream_smoke(
        out_dir=str(root / "dry"),
        environment=env,
        client_factory=lambda region: (_ for _ in ()).throw(AssertionError("dry-run called client")),
        dry_run=True,
    )
    check(dry.passed is True and dry.ran_count == 0, "dry-run performs no AWS call")

    missing = run_bedrock_stream_smoke(
        out_dir=str(root / "missing"),
        environment={},
        require_live=True,
    )
    check(missing.passed is False and missing.skipped_count == 1, "require-live rejects missing AWS configuration")
finally:
    shutil.rmtree(root, ignore_errors=True)

raise SystemExit(check.report("RESULT test_bedrock_stream_smoke"))
