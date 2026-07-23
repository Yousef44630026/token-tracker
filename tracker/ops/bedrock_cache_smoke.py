"""Auditable two-call AWS Bedrock prompt-cache smoke harness.

The live path deliberately keeps boto3 optional. It sends the same cache-pointed request
twice, verifies a cache write followed by a cache read, normalizes both responses, and stores
only usage/latency/request metadata. Prompt and generated content never enter the audit bundle.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter
from tracker.analytics.trust_report import build_trust_report
from tracker.context.model import TraceContext, new_trace
from tracker.models.enums import TokenType
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace
from tracker.normalization.normalizer import normalize
from tracker.observability.observation import Observation
from tracker.ops.provider_proof import write_capture_attestation
from tracker.ops.runtime_fingerprint import runtime_fingerprint
from tracker.storage.file_repository import FileRepository

ClientFactory = Callable[[str], Any]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class BedrockCacheCallResult:
    """Outcome and accounting evidence for one Bedrock call."""

    call_index: int
    status: str
    detail: str
    http_status: int | None = None
    provider_request_id: str | None = None
    event_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    provider_total_tokens: int | None = None
    event_total_mismatch: int | None = None
    data_quality_flags: list[str] = field(default_factory=list)
    artifact: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass(frozen=True)
class BedrockCacheSmokeSummary:
    """Whole-run cache and normalization result."""

    out_dir: str
    passed: bool
    dry_run: bool
    ran_count: int
    skipped_count: int
    failure_count: int
    event_count: int
    cache_write_tokens: int
    cache_read_tokens: int
    observed_total_contributing_tokens: int
    artifacts: dict[str, str]
    results: list[BedrockCacheCallResult]
    generated_at: str = ""
    runtime_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["results"] = [asdict(result) for result in self.results]
        return data


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_timestamp_for_path() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _env(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _credential_source(environment: Mapping[str, str]) -> str:
    if _env(environment, "AWS_BEARER_TOKEN_BEDROCK"):
        return "bedrock_bearer_env"
    if _env(environment, "AWS_ACCESS_KEY_ID"):
        return "aws_access_key_env"
    if _env(environment, "AWS_PROFILE"):
        return "aws_profile"
    return "aws_default_chain"


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _build_prefix(prefix_words: int, run_marker: str) -> str:
    if prefix_words < 1:
        raise ValueError("prefix_words must be positive")
    vocabulary = (
        "audit token observability request trace cache input output provider latency quality "
        "authoritative reconciliation evidence metric storage derived invariant"
    ).split()
    body = " ".join(vocabulary[index % len(vocabulary)] for index in range(prefix_words))
    return f"Cache validation run {run_marker}. {body}\nReply with only OK."


def _request_payload(model_id: str, prefix: str, max_tokens: int) -> dict[str, Any]:
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    return {
        "modelId": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": prefix},
                    {"cachePoint": {"type": "default"}},
                ],
            }
        ],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }


def _default_client_factory(region: str) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError('boto3_missing: install with pip install -e ".[bedrock]"') from exc
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
    )


def _response_metadata(response: Mapping[str, Any]) -> tuple[int, str | None, int | None]:
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return 200, None, None
    status = metadata.get("HTTPStatusCode")
    http_status = int(status) if isinstance(status, int) and 100 <= status <= 599 else 200
    request_id = metadata.get("RequestId")
    retries = metadata.get("RetryAttempts")
    return (
        http_status,
        str(request_id) if request_id else None,
        int(retries) if isinstance(retries, int) and retries >= 0 else None,
    )


def _redacted_response(response: Mapping[str, Any], model_id: str) -> dict[str, Any]:
    """Keep accounting evidence while excluding prompts and generated content."""
    http_status, request_id, retries = _response_metadata(response)
    usage = response.get("usage")
    metrics = response.get("metrics")
    return {
        "modelId": model_id,
        "stopReason": response.get("stopReason"),
        "usage": dict(usage) if isinstance(usage, Mapping) else None,
        "metrics": dict(metrics) if isinstance(metrics, Mapping) else None,
        "ResponseMetadata": {
            "HTTPStatusCode": http_status,
            "RequestId": request_id,
            "RetryAttempts": retries,
        },
    }


def _quantity(event: TokenEvent, token_type: TokenType) -> int:
    return sum(
        quantity.quantity or 0
        for quantity in event.quantities
        if quantity.token_type == token_type and quantity.included_in_total
    )


def _classify_exception(exc: Exception) -> tuple[str, int | None, str]:
    response = getattr(exc, "response", None)
    error: Mapping[str, Any] = {}
    metadata: Mapping[str, Any] = {}
    if isinstance(response, Mapping):
        candidate_error = response.get("Error")
        candidate_metadata = response.get("ResponseMetadata")
        if isinstance(candidate_error, Mapping):
            error = candidate_error
        if isinstance(candidate_metadata, Mapping):
            metadata = candidate_metadata
    raw_status = metadata.get("HTTPStatusCode")
    status = int(raw_status) if isinstance(raw_status, int) else None
    provider_code = str(error.get("Code") or "")
    detail = str(error.get("Message") or exc)[:500]
    lowered = f"{provider_code} {detail}".lower()
    if "boto3_missing" in lowered:
        return "sdk_missing", status, detail
    if status in {401, 403} or "unauthorized" in lowered or "unrecognizedclient" in lowered:
        return "auth_or_access_denied", status, detail
    if "accessdenied" in lowered:
        return "auth_or_access_denied", status, detail
    if status == 404 or "resourcenotfound" in lowered or "modelnotfound" in lowered:
        return "model_or_endpoint_not_found", status, detail
    if status == 429 or "throttl" in lowered or "quota" in lowered:
        return "rate_limited_or_quota", status, detail
    if status == 400 or "validation" in lowered:
        return "request_or_cache_not_supported", status, detail
    return "network_or_client_failure", status, detail


def _error_event(
    ctx: TraceContext,
    *,
    call_index: int,
    model_id: str,
    region: str,
    duration_ms: float,
    error_code: str,
    status: int | None,
    detail: str,
) -> TokenEvent:
    return TokenEvent(
        event_id=f"bedrock-cache-smoke-{call_index}-{ctx.request_correlation_id}",
        request_correlation_id=ctx.request_correlation_id,
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        parent_span_id=ctx.parent_span_id,
        workflow=ctx.workflow,
        environment=ctx.environment,
        provider="bedrock",
        model=model_id,
        api_surface="converse",
        quantities=[],
        provider_total_tokens=None,
        data_quality_flags=[error_code],
        timestamp=_timestamp(),
        observation=Observation(
            authoritative=False,
            status="failed",
            http_status=status,
            duration_ms=round(duration_ms, 3),
            provider_error_code=error_code,
            service_name="bedrock-cache-smoke",
            cloud_provider="aws",
            region=region,
            extra={"failure_detail": detail},
        ),
    )


def _run_call(
    client: Any,
    request_payload: dict[str, Any],
    *,
    call_index: int,
    out_dir: Path,
    root_context: TraceContext,
    region: str,
) -> tuple[BedrockCacheCallResult, TokenEvent]:
    ctx = root_context.child_span()
    artifact_path = out_dir / "calls" / f"call_{call_index}.json"
    started = time.perf_counter()
    try:
        response = client.converse(**request_payload)
        duration_ms = (time.perf_counter() - started) * 1000
        if not isinstance(response, Mapping):
            raise TypeError("Bedrock Converse response must be a mapping")
    except Exception as exc:  # noqa: BLE001 - provider failures become auditable outcomes
        duration_ms = (time.perf_counter() - started) * 1000
        error_code, status, detail = _classify_exception(exc)
        event = _error_event(
            ctx,
            call_index=call_index,
            model_id=request_payload["modelId"],
            region=region,
            duration_ms=duration_ms,
            error_code=error_code,
            status=status,
            detail=detail,
        )
        artifact = _write_json(
            artifact_path,
            {
                "captured_at": _timestamp(),
                "call_index": call_index,
                "status": "fail",
                "error_code": error_code,
                "http_status": status,
                "error_detail": detail,
            },
        )
        return BedrockCacheCallResult(
            call_index=call_index,
            status="fail",
            detail=error_code,
            http_status=status,
            event_id=event.event_id,
            data_quality_flags=list(event.data_quality_flags),
            artifact=artifact,
        ), event

    model_id = request_payload["modelId"]
    redacted = _redacted_response(response, model_id)
    http_status, request_id, retries = _response_metadata(response)
    event = normalize(
        redacted,
        BedrockConverseAdapter(),
        context=ctx,
        timestamp=_timestamp(),
        observation=Observation(
            authoritative=True,
            status="complete",
            http_status=http_status,
            duration_ms=round(duration_ms, 3),
            provider_request_id=request_id,
            retry_count=retries,
            service_name="bedrock-cache-smoke",
            cloud_provider="aws",
            region=region,
            deployment=model_id,
        ),
    )
    cache_read = _quantity(event, TokenType.CACHED_INPUT)
    cache_write = _quantity(event, TokenType.CACHE_CREATION_INPUT)
    input_tokens = _quantity(event, TokenType.INPUT)
    output_tokens = _quantity(event, TokenType.OUTPUT)
    mismatch = event.event_total_mismatch
    failure_detail = None
    if not event.quantities or event.provider_total_tokens is None or "provider_usage_missing" in event.data_quality_flags:
        failure_detail = "usage_missing"
    elif retries not in {None, 0}:
        failure_detail = f"automatic_retry_detected={retries}"
    elif mismatch != 0 or event.over_attributed_tokens:
        failure_detail = f"normalization_mismatch={mismatch}"
    artifact = _write_json(
        artifact_path,
        {
            "captured_at": _timestamp(),
            "call_index": call_index,
            "status": "fail" if failure_detail else "pass",
            "response": redacted,
        },
    )
    return BedrockCacheCallResult(
        call_index=call_index,
        status="fail" if failure_detail else "pass",
        detail=failure_detail or "normalized and reconciled",
        http_status=http_status,
        provider_request_id=request_id,
        event_id=event.event_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        provider_total_tokens=event.provider_total_tokens,
        event_total_mismatch=mismatch,
        data_quality_flags=list(event.data_quality_flags),
        artifact=artifact,
    ), event


def _apply_cache_expectations(results: list[BedrockCacheCallResult]) -> list[BedrockCacheCallResult]:
    checked = list(results)
    if len(checked) >= 1 and not checked[0].failed and checked[0].cache_write_tokens <= 0:
        checked[0] = BedrockCacheCallResult(**{**asdict(checked[0]), "status": "fail", "detail": "cache_write_not_observed"})
    if len(checked) >= 2 and not checked[1].failed and checked[1].cache_read_tokens <= 0:
        checked[1] = BedrockCacheCallResult(**{**asdict(checked[1]), "status": "fail", "detail": "cache_read_not_observed"})
    return checked


def _write_audit_readme(path: Path, summary: BedrockCacheSmokeSummary) -> str:
    lines = [
        "# Bedrock Prompt Cache Smoke Audit",
        "",
        f"- passed: {summary.passed}",
        f"- ran_count: {summary.ran_count}",
        f"- failure_count: {summary.failure_count}",
        f"- cache_write_tokens: {summary.cache_write_tokens}",
        f"- cache_read_tokens: {summary.cache_read_tokens}",
        f"- observed_total_contributing_tokens: {summary.observed_total_contributing_tokens}",
        "- content_capture: disabled (usage and technical metadata only)",
        "",
        "## Calls",
        "",
    ]
    for result in summary.results:
        lines.append(
            f"- call {result.call_index}: {result.status} - {result.detail}; "
            f"input={result.input_tokens}; output={result.output_tokens}; "
            f"cache_write={result.cache_write_tokens}; cache_read={result.cache_read_tokens}; "
            f"mismatch={result.event_total_mismatch}"
        )
    lines.extend(["", "## Artifacts", ""])
    for name, artifact in summary.artifacts.items():
        lines.append(f"- {name}: `{artifact}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def run_bedrock_cache_smoke(
    *,
    out_dir: str | None = None,
    environment: Mapping[str, str] | None = None,
    client_factory: ClientFactory | None = None,
    sleeper: Sleeper = time.sleep,
    dry_run: bool = False,
    require_live: bool = False,
    prefix_words: int = 5000,
    max_tokens: int = 8,
    wait_seconds: float = 0.25,
    run_marker: str | None = None,
) -> BedrockCacheSmokeSummary:
    """Run a unique-prefix cache write/read pair and write redacted evidence."""
    if wait_seconds < 0:
        raise ValueError("wait_seconds cannot be negative")
    env = dict(os.environ if environment is None else environment)
    generated_at = _timestamp()
    code_fingerprint = runtime_fingerprint()
    region = _env(env, "AWS_REGION") or _env(env, "AWS_DEFAULT_REGION")
    model_id = _env(env, "BEDROCK_MODEL_ID")
    root = Path(out_dir or Path("runs") / "bedrock-cache-smoke" / _safe_timestamp_for_path()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}
    artifacts["config"] = _write_json(
        root / "config_redacted.json",
        {
            "AWS_REGION": region,
            "BEDROCK_MODEL_ID": model_id,
            "credential_source": _credential_source(env),
            "credential_value_captured": False,
        },
    )

    marker = run_marker or uuid.uuid4().hex
    prefix = _build_prefix(prefix_words, marker)
    prefix_hash = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
    artifacts["plan"] = _write_json(
        root / "request_plan.json",
        {
            "calls": 2,
            "region": region,
            "model_id": model_id,
            "cache_point_location": "messages[0].content after static text",
            "prefix_word_count": len(prefix.split()),
            "prefix_sha256": prefix_hash,
            "prompt_or_output_content_stored": False,
            "max_output_tokens_per_call": max_tokens,
            "note": "word count is not a provider token count; cache minimums are model-specific",
        },
    )

    missing = [name for name, value in (("AWS_REGION", region), ("BEDROCK_MODEL_ID", model_id)) if not value]
    if missing or dry_run:
        detail = "missing env vars: " + ", ".join(missing) if missing else "dry-run: two live calls not executed"
        result = BedrockCacheCallResult(call_index=0, status="skip", detail=detail)
        failure_count = 1 if require_live else 0
        summary = BedrockCacheSmokeSummary(
            out_dir=str(root),
            passed=failure_count == 0,
            dry_run=dry_run,
            ran_count=0,
            skipped_count=1,
            failure_count=failure_count,
            event_count=0,
            cache_write_tokens=0,
            cache_read_tokens=0,
            observed_total_contributing_tokens=0,
            artifacts=artifacts,
            results=[result],
            generated_at=generated_at,
            runtime_fingerprint=code_fingerprint,
        )
        artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
        artifacts["readme"] = _write_audit_readme(root / "README_AUDIT.md", summary)
        capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
        if capture_key:
            artifacts["capture_attestation"] = write_capture_attestation(
                artifacts["summary"], capture_key, harness="bedrock_cache_smoke"
            )
        return summary

    request_payload = _request_payload(model_id or "", prefix, max_tokens)
    root_context = new_trace(workflow="bedrock-cache-smoke", environment="live")
    events: list[TokenEvent] = []
    results: list[BedrockCacheCallResult] = []
    try:
        client = (client_factory or _default_client_factory)(region or "")
    except Exception as exc:  # noqa: BLE001 - dependency/auth setup becomes a stable result
        error_code, status, detail = _classify_exception(exc)
        results.append(BedrockCacheCallResult(0, "fail", error_code, http_status=status))
        artifacts["client_error"] = _write_json(
            root / "calls" / "client_error.json",
            {"captured_at": _timestamp(), "status": "fail", "error_code": error_code, "error_detail": detail},
        )
    else:
        for call_index in (1, 2):
            if call_index == 2 and wait_seconds:
                sleeper(wait_seconds)
            result, event = _run_call(
                client,
                request_payload,
                call_index=call_index,
                out_dir=root,
                root_context=root_context,
                region=region or "",
            )
            results.append(result)
            events.append(event)
            if result.failed:
                break

    results = _apply_cache_expectations(results)
    observed_total = 0
    if events:
        store_path = root / "events.jsonl"
        FileRepository(str(store_path)).append_many(events)
        artifacts["events_jsonl"] = str(store_path)
        trace = Trace(trace_id=root_context.trace_id, workflow="bedrock-cache-smoke", environment="live", events=events)
        trust_report = build_trust_report(trace).to_dict()
        artifacts["trust_report"] = _write_json(root / "trust_report.json", trust_report)
        observed_total = trust_report["observed_total_contributing_tokens"]

    failure_count = sum(1 for result in results if result.failed)
    ran_count = sum(1 for result in results if result.status != "skip")
    if require_live and ran_count == 0:
        failure_count += 1
    summary = BedrockCacheSmokeSummary(
        out_dir=str(root),
        passed=failure_count == 0 and ran_count == 2,
        dry_run=False,
        ran_count=ran_count,
        skipped_count=sum(1 for result in results if result.status == "skip"),
        failure_count=failure_count,
        event_count=len(events),
        cache_write_tokens=sum(result.cache_write_tokens for result in results),
        cache_read_tokens=sum(result.cache_read_tokens for result in results),
        observed_total_contributing_tokens=observed_total,
        artifacts=artifacts,
        results=results,
        generated_at=generated_at,
        runtime_fingerprint=code_fingerprint,
    )
    artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
    artifacts["readme"] = _write_audit_readme(root / "README_AUDIT.md", summary)
    capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
    if capture_key:
        artifacts["capture_attestation"] = write_capture_attestation(
            artifacts["summary"], capture_key, harness="bedrock_cache_smoke"
        )
    return summary


def _render_text(summary: BedrockCacheSmokeSummary) -> str:
    lines = ["AWS Bedrock prompt-cache smoke harness"]
    for result in summary.results:
        lines.append(
            f"[{result.status.upper()}] call {result.call_index}: {result.detail}; "
            f"write={result.cache_write_tokens} read={result.cache_read_tokens}"
        )
    lines.append(
        "summary: "
        f"passed={summary.passed} ran={summary.ran_count} failures={summary.failure_count} "
        f"cache_write={summary.cache_write_tokens} cache_read={summary.cache_read_tokens}"
    )
    lines.append(f"artifacts: {summary.out_dir}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run two tiny Bedrock Converse calls and prove prompt-cache accounting")
    parser.add_argument("--out-dir", help="audit bundle directory; default runs/bedrock-cache-smoke/<timestamp>")
    parser.add_argument("--dry-run", action="store_true", help="write redacted plan/config only; no live calls")
    parser.add_argument("--require-live", action="store_true", help="return non-zero unless both live cache calls pass")
    parser.add_argument("--prefix-words", type=int, default=5000, help="static prefix words; provider token count is model-specific")
    parser.add_argument("--max-tokens", type=int, default=8, help="maximum output tokens per call")
    parser.add_argument("--wait-seconds", type=float, default=0.25, help="delay between cache write and read calls")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = run_bedrock_cache_smoke(
        out_dir=args.out_dir,
        dry_run=args.dry_run,
        require_live=args.require_live,
        prefix_words=args.prefix_words,
        max_tokens=args.max_tokens,
        wait_seconds=args.wait_seconds,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) if args.json else _render_text(summary))
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
