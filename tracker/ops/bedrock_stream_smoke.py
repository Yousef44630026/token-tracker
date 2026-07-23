"""Auditable live AWS Bedrock ConverseStream token proof."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter
from tracker.context.propagation import new_trace
from tracker.models.enums import PrecisionLevel, UsageSource
from tracker.models.token_event import TokenEvent
from tracker.ops.provider_proof import write_capture_attestation
from tracker.ops.runtime_fingerprint import runtime_fingerprint
from tracker.storage.file_repository import FileRepository
from tracker.streaming.stream_consumer import consume_stream


@dataclass(frozen=True)
class BedrockStreamSummary:
    passed: bool
    out_dir: str
    ran_count: int
    skipped_count: int
    failure_count: int
    event_count: int
    observed_total_contributing_tokens: int
    artifacts: dict[str, str]
    detail: str
    event_id: str | None = None
    provider_total_tokens: int | None = None
    data_quality_flags: list[str] = field(default_factory=list)
    generated_at: str = ""
    runtime_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def _env(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _default_client_factory(region: str) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - exercised only by optional live runtime
        raise RuntimeError('Bedrock live proof requires: pip install -e ".[bedrock]"') from exc
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
    )


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _text_delta(event: Any) -> str | None:
    if not isinstance(event, Mapping):
        return None
    content = event.get("contentBlockDelta")
    delta = content.get("delta") if isinstance(content, Mapping) else None
    text = delta.get("text") if isinstance(delta, Mapping) else None
    return text if isinstance(text, str) else None


def _redact_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    redacted: list[dict[str, Any]] = []
    for event in events:
        current = dict(event)
        content = current.get("contentBlockDelta")
        if isinstance(content, Mapping):
            bounded = dict(content)
            delta = bounded.get("delta")
            if isinstance(delta, Mapping) and isinstance(delta.get("text"), str):
                bounded["delta"] = {"text_characters": len(delta["text"]), "content_redacted": True}
            current["contentBlockDelta"] = bounded
        redacted.append(current)
    return redacted


def _request_id(response: Mapping[str, Any]) -> str | None:
    metadata = response.get("ResponseMetadata")
    value = metadata.get("RequestId") if isinstance(metadata, Mapping) else None
    return str(value) if value else None


def _retry_count(response: Mapping[str, Any]) -> int | None:
    metadata = response.get("ResponseMetadata")
    value = metadata.get("RetryAttempts") if isinstance(metadata, Mapping) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _valid_event(event: TokenEvent) -> bool:
    known = [quantity for quantity in event.quantities if quantity.quantity is not None]
    return (
        bool(known)
        and all(quantity.precision_level == PrecisionLevel.EXACT for quantity in known)
        and all(quantity.usage_source == UsageSource.PROVIDER_STREAM_FINAL for quantity in known)
        and event.provider_total_tokens is not None
        and event.event_total_mismatch == 0
        and event.over_attributed_tokens == 0
        and event.observation.get("retry_count") in {None, 0}
        and "provider_stream_usage_missing" not in event.data_quality_flags
    )


def run_bedrock_stream_smoke(
    *,
    out_dir: str | None = None,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[str], Any] | None = None,
    dry_run: bool = False,
    require_live: bool = False,
    max_tokens: int = 64,
) -> BedrockStreamSummary:
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    env = dict(os.environ if environment is None else environment)
    generated_at = _timestamp()
    code_fingerprint = runtime_fingerprint()
    region = _env(env, "AWS_REGION") or _env(env, "AWS_DEFAULT_REGION")
    model_id = _env(env, "BEDROCK_MODEL_ID")
    root = Path(out_dir or Path("runs") / "bedrock-stream-smoke" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "config": _write_json(
            root / "config_redacted.json",
            {
                "region": region,
                "model_id": model_id,
                "credential_source": (
                    "bearer"
                    if _env(env, "AWS_BEARER_TOKEN_BEDROCK")
                    else "aws_sdk_chain"
                ),
                "max_tokens": max_tokens,
                "content_capture": "disabled",
            },
        )
    }
    if not region or not model_id:
        summary = BedrockStreamSummary(
            passed=not require_live,
            out_dir=str(root),
            ran_count=0,
            skipped_count=1,
            failure_count=1 if require_live else 0,
            event_count=0,
            observed_total_contributing_tokens=0,
            artifacts=artifacts,
            detail="missing configuration: AWS_REGION, BEDROCK_MODEL_ID",
            generated_at=generated_at,
            runtime_fingerprint=code_fingerprint,
        )
        artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
        capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
        if capture_key:
            artifacts["capture_attestation"] = write_capture_attestation(
                artifacts["summary"], capture_key, harness="bedrock_stream_smoke"
            )
        return summary
    if dry_run:
        summary = BedrockStreamSummary(
            passed=not require_live,
            out_dir=str(root),
            ran_count=0,
            skipped_count=1,
            failure_count=1 if require_live else 0,
            event_count=0,
            observed_total_contributing_tokens=0,
            artifacts=artifacts,
            detail="dry-run: no live call executed",
            generated_at=generated_at,
            runtime_fingerprint=code_fingerprint,
        )
        artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
        capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
        if capture_key:
            artifacts["capture_attestation"] = write_capture_attestation(
                artifacts["summary"], capture_key, harness="bedrock_stream_smoke"
            )
        return summary

    started = time.perf_counter()
    try:
        client = (client_factory or _default_client_factory)(region)
        response = client.converse_stream(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "A batch contains 12 groups of 8 records and 7 records fail validation. "
                                "Return the valid record count and one short calculation."
                            )
                        }
                    ],
                }
            ],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        )
        stream = response.get("stream") if isinstance(response, Mapping) else None
        if stream is None:
            raise ValueError("Bedrock ConverseStream response contains no stream")
        events = list(stream)
        context = new_trace(workflow="provider-proof", environment="validation", business_id="bedrock")
        event = consume_stream(
            events,
            BedrockConverseAdapter(model_id=model_id),
            context=context,
            text_extractor=_text_delta,
            model=model_id,
        )
        event.observation.update(
            {
                "http_status": 200,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "provider_request_id": _request_id(response),
                "retry_count": _retry_count(response),
                "service_name": "bedrock-stream-proof",
                "cloud_provider": "aws",
                "region": region,
                "deployment": model_id,
                "scenario": "bedrock-converse-stream-proof",
                "stream_event_count": len(events),
            }
        )
        passed = _valid_event(event)
        detail = "streamed, normalized and reconciled" if passed else "terminal_usage_validation_failed"
        artifacts["raw"] = _write_json(
            root / "raw" / "converse_stream.json",
            {
                "captured_at": _timestamp(),
                "status": "pass" if passed else "fail",
                "request_id": _request_id(response),
                "retry_count": _retry_count(response),
                "events": _redact_events(events),
            },
        )
        repository = FileRepository(root / "events.jsonl")
        repository.append(event)
        artifacts["events_jsonl"] = repository.path
        summary = BedrockStreamSummary(
            passed=passed,
            out_dir=str(root),
            ran_count=1,
            skipped_count=0,
            failure_count=0 if passed else 1,
            event_count=1,
            observed_total_contributing_tokens=event.event_contributing_tokens,
            artifacts=artifacts,
            detail=detail,
            event_id=event.event_id,
            provider_total_tokens=event.provider_total_tokens,
            data_quality_flags=list(event.data_quality_flags),
            generated_at=generated_at,
            runtime_fingerprint=code_fingerprint,
        )
    except Exception as exc:  # noqa: BLE001 - provider/SDK failures become bounded proof evidence
        artifacts["raw"] = _write_json(
            root / "raw" / "converse_stream.json",
            {"captured_at": _timestamp(), "status": "fail", "error": f"{type(exc).__name__}: {exc}"},
        )
        summary = BedrockStreamSummary(
            passed=False,
            out_dir=str(root),
            ran_count=1,
            skipped_count=0,
            failure_count=1,
            event_count=0,
            observed_total_contributing_tokens=0,
            artifacts=artifacts,
            detail="provider_or_client_failure",
            generated_at=generated_at,
            runtime_fingerprint=code_fingerprint,
        )
    artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
    capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
    if capture_key:
        artifacts["capture_attestation"] = write_capture_attestation(
            artifacts["summary"], capture_key, harness="bedrock_stream_smoke"
        )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prove exact Bedrock ConverseStream token accounting")
    parser.add_argument("--out-dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = run_bedrock_stream_smoke(
        out_dir=args.out_dir,
        dry_run=args.dry_run,
        require_live=args.require_live,
        max_tokens=args.max_tokens,
    )
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("AWS Bedrock ConverseStream token proof")
        print(
            f"[{'PASS' if summary.passed else 'FAIL'}] {summary.detail} | "
            f"tokens={summary.observed_total_contributing_tokens} provider_total={summary.provider_total_tokens}"
        )
        print(
            f"summary: passed={summary.passed} ran={summary.ran_count} skipped={summary.skipped_count} "
            f"failures={summary.failure_count}"
        )
        print(f"artifacts: {summary.out_dir}")
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
